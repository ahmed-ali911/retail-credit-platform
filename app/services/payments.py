"""Record a payment against a contract and apply the allocation waterfall."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    InstallmentStatus,
)
from app.models.payment import (
    LateFeeStatus,
    Payment,
    PaymentAllocation,
    PaymentStatus,
)
from app.models.accounting import AccountingEventType
from app.models.closure import ContractClosure
from app.models.ledger import LedgerEntryType, LedgerRelatedAction
from app.services import accounting
from app.services import allocation as alloc
from app.services import closure as closure_service
from app.services import collections as collections_service
from app.services import ledger as ledger_service
from app.services.errors import DomainError
from app.services.receivable import build_receivable

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass
class PaymentOutcome:
    payment: Payment
    replayed: bool
    closure: ContractClosure | None = None


def _outstanding_installments(contract: InstallmentContract) -> list[Installment]:
    live = [
        i
        for i in sorted(contract.installments, key=lambda x: x.sequence_number)
        if i.principal_outstanding > _ZERO
        or i.profit_outstanding > _ZERO
        or i.late_fee_outstanding > _ZERO
    ]
    return live


def record_payment(
    db: Session,
    contract: InstallmentContract,
    *,
    amount: float,
    external_reference: str,
    actor_id: int | None = None,
) -> PaymentOutcome:
    external_reference = (external_reference or "").strip()
    if not external_reference:
        raise DomainError("external_reference is required")

    existing = db.execute(
        select(Payment).where(
            Payment.contract_id == contract.id,
            Payment.external_reference == external_reference,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return PaymentOutcome(payment=existing, replayed=True)

    if contract.status != ContractStatus.active:
        raise DomainError(
            f"Payments can only be recorded against an active contract "
            f"(current status: {contract.status.value})",
            status_code=409,
        )

    amount_dec = _money(amount)
    if amount_dec <= _ZERO:
        raise DomainError("amount must be greater than zero")

    # Bug fix: reject a payment larger than what's actually owed, rather than
    # accepting the excess as unallocated (which then distorts every
    # downstream figure). Overpayment/credit-balance handling — crediting the
    # excess toward future installments — is a separate feature, not this
    # fix; the correct amount is surfaced so the caller can resubmit it.
    receivable = build_receivable(contract)
    total_outstanding = _money(
        receivable.outstanding_receivable + receivable.outstanding_late_fees
    )
    if amount_dec > total_outstanding:
        raise DomainError(
            f"amount {amount_dec} exceeds the contract's current total "
            f"outstanding of {total_outstanding}",
        )

    live = _outstanding_installments(contract)
    outstanding = [
        alloc.OutstandingInstallment(
            installment_id=i.id,
            sequence_number=i.sequence_number,
            late_fee_outstanding=i.late_fee_outstanding,
            profit_outstanding=i.profit_outstanding,
            principal_outstanding=i.principal_outstanding,
        )
        for i in live
    ]
    plan = alloc.allocate(amount_dec, outstanding)

    payment = Payment(
        contract_id=contract.id,
        amount=amount_dec,
        external_reference=external_reference,
        allocated_amount=plan.allocated_amount,
        unallocated_amount=plan.unallocated_amount,
        status=(
            PaymentStatus.overpaid
            if plan.unallocated_amount > _ZERO
            else PaymentStatus.applied
        ),
    )
    db.add(payment)
    db.flush()

    by_id = {i.id: i for i in live}
    for line in plan.allocations:
        installment = by_id[line.installment_id]

        db.add(
            PaymentAllocation(
                payment_id=payment.id,
                contract_id=contract.id,
                installment_id=installment.id,
                late_fee_amount=line.late_fee,
                profit_amount=line.profit,
                principal_amount=line.principal,
            )
        )

        if line.late_fee > _ZERO:
            _apply_late_fee(installment, line.late_fee)
        if line.profit > _ZERO:
            installment.profit_paid = _money(installment.profit_paid) + line.profit
            contract.unearned_profit_balance = (
                _money(contract.unearned_profit_balance) - line.profit
            )
        if line.principal > _ZERO:
            installment.principal_paid = (
                _money(installment.principal_paid) + line.principal
            )

        # --- dual-write to the immutable ledger (Phase 1) ---
        for entry_type, amount in (
            (LedgerEntryType.late_fee_paid, line.late_fee),
            (LedgerEntryType.profit_recognized, line.profit),
            (LedgerEntryType.principal_paid, line.principal),
        ):
            if amount > _ZERO:
                ledger_service.record_entry(
                    db,
                    contract_id=contract.id,
                    entry_type=entry_type,
                    amount=amount,
                    related_action=LedgerRelatedAction.payment,
                    reference_type="payment",
                    reference_id=payment.id,
                    created_by=actor_id,
                )

        _update_installment_status(installment)

    db.flush()

    # --- accounting-event boundary (additive; never blocks the payment) ---
    # The whole payment received, plus the profit portion actually recognised by
    # this allocation (not the full scheduled profit).
    accounting.emit(
        db,
        event_type=AccountingEventType.payment_received,
        event_reference=f"payment-received-{payment.id}",
        contract=contract,
        amount=payment.amount,
        event_date=payment.received_at,
    )
    profit_recognized = sum((line.profit for line in plan.allocations), _ZERO)
    if profit_recognized > _ZERO:
        accounting.emit(
            db,
            event_type=AccountingEventType.profit_recognized,
            event_reference=f"profit-recognized-{payment.id}",
            contract=contract,
            amount=profit_recognized,
            event_date=payment.received_at,
        )

    # Collections hook: close the open case once no overdue installments remain.
    collections_service.close_case_if_cleared(db, contract, actor_id=actor_id)

    # Bug fix (root cause): this is the one place a contract can reach zero
    # outstanding through normal repayment. Runs once, only on the payment
    # that actually completes the schedule — see close_if_fully_repaid's
    # idempotency guards.
    closure = closure_service.close_if_fully_repaid(db, contract, actor_id=actor_id)

    db.flush()
    return PaymentOutcome(payment=payment, replayed=False, closure=closure)


def _apply_late_fee(installment: Installment, amount: Decimal) -> None:
    remaining = amount
    charges = sorted(
        (c for c in installment.late_fee_charges if c.status != LateFeeStatus.waived),
        key=lambda c: c.assessed_at,
    )
    for charge in charges:
        if remaining <= _ZERO:
            break
        owed = (charge.amount or _ZERO) - (charge.amount_paid or _ZERO)
        take = min(remaining, owed)
        if take <= _ZERO:
            continue
        charge.amount_paid = (charge.amount_paid or _ZERO) + take
        remaining -= take
        if charge.amount_paid >= charge.amount:
            charge.status = LateFeeStatus.paid


def _update_installment_status(installment: Installment) -> None:
    if installment.is_fully_paid:
        installment.status = InstallmentStatus.paid
    elif installment.status == InstallmentStatus.overdue:
        # a partial payment on an overdue installment leaves it overdue
        return
    elif installment.has_any_payment:
        installment.status = InstallmentStatus.partially_paid
