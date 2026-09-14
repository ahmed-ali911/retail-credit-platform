"""Mock Payment Gateway — reversal of a previously SETTLED payment.

Triggered only by a verified webhook moving a PaymentIntent from SETTLED to
REVERSED, REFUNDED, or CHARGEBACK (see gateway_webhooks.py). This is
genuinely new capability — the platform has never reversed a payment before
— but it deliberately reuses everything it can: the original
`PaymentAllocation` rows it is undoing, the existing ledger dual-write
(`services/ledger.py`), the existing accounting-event boundary
(`services/accounting.py`), and the existing collections case-reopen
primitive (`services/collections.py::open_case_if_needed`). It does **not**
re-implement `services/allocation.py::allocate()` — reversal is the exact
inverse of a *specific* payment's own recorded allocations, not a new
waterfall.

Never deletes or edits the original `Payment` / `PaymentAllocation` rows
beyond flipping `Payment.status` and setting
`PaymentAllocation.reversed_by_allocation_id` — every compensating figure is
a NEW row, so the full financial history stays intact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import AccountingEventType
from app.models.contract import Installment, InstallmentStatus
from app.models.ledger import LedgerEntryType, LedgerRelatedAction
from app.models.payment import (
    LateFeeStatus,
    Payment,
    PaymentAllocation,
    PaymentSource,
    PaymentStatus,
)
from app.models.payment_gateway import PaymentIntent, PaymentIntentStatus
from app.services import accounting
from app.services import collections as collections_service
from app.services import ledger as ledger_service
from app.services.errors import DomainError

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")

# Which accounting event a reversal emits depends on the status that caused
# it — REVERSED/CHARGEBACK share the generic "money taken back" event;
# REFUNDED (a customer-initiated refund, as opposed to a chargeback/void)
# gets its own event type so Finance can tell the two apart downstream.
_ACCOUNTING_EVENT_BY_STATUS = {
    PaymentIntentStatus.reversed: AccountingEventType.payment_reversed,
    PaymentIntentStatus.chargeback: AccountingEventType.payment_reversed,
    PaymentIntentStatus.refunded: AccountingEventType.refund_completed,
}


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _reverse_late_fee(installment: Installment, amount: Decimal) -> None:
    """Inverse of payments.py::_apply_late_fee — walks the installment's
    charges MOST-recently-assessed first (undoing the last thing that was
    paid first) and decrements amount_paid, never below zero. Charge-level
    attribution of exactly which charge(s) an original payment covered isn't
    stored, so this is a documented best-effort approximation: correct in
    total amount reversed, and correct for the common case of a single
    outstanding charge per installment; TBD — Business Approval Required if
    per-charge reversal precision is ever needed for multi-charge installments."""
    remaining = amount
    charges = sorted(
        (c for c in installment.late_fee_charges if (c.amount_paid or _ZERO) > _ZERO),
        key=lambda c: c.assessed_at,
        reverse=True,
    )
    for charge in charges:
        if remaining <= _ZERO:
            break
        take = min(remaining, charge.amount_paid)
        charge.amount_paid = _money((charge.amount_paid or _ZERO) - take)
        remaining -= take
        if charge.status == LateFeeStatus.paid and charge.amount_paid < charge.amount:
            charge.status = LateFeeStatus.assessed


def _update_status_after_reversal(installment: Installment) -> None:
    if installment.is_fully_paid:
        installment.status = InstallmentStatus.paid
    elif installment.has_any_payment:
        installment.status = InstallmentStatus.partially_paid
    else:
        # Whether this is now overdue again is for the existing overdue-
        # assessment job to decide (job-driven, not synchronous elsewhere in
        # this codebase either) — reversal only restores the raw balance.
        installment.status = InstallmentStatus.pending


def reverse_settled_payment(
    db: Session,
    intent: PaymentIntent,
    *,
    final_status: PaymentIntentStatus,
    actor_id: int,
) -> Payment:
    original = db.execute(
        select(Payment).where(
            Payment.payment_intent_id == intent.id,
            Payment.source == PaymentSource.gateway,
            Payment.status != PaymentStatus.reversed,
        )
    ).scalar_one_or_none()
    if original is None:
        # Idempotency guard: already reversed (or, if this ever fires for an
        # intent whose settlement never actually recorded a Payment —
        # shouldn't happen given the transition graph only allows this from
        # SETTLED — there's nothing to undo). Either way, never invent one.
        existing_reversal = db.execute(
            select(Payment).where(
                Payment.payment_intent_id == intent.id,
                Payment.status == PaymentStatus.reversed,
                Payment.external_reference.like("%-REV"),
            )
        ).scalar_one_or_none()
        if existing_reversal is not None:
            return existing_reversal
        raise DomainError(
            f"No settled gateway payment found to reverse for intent "
            f"{intent.payment_reference}",
            status_code=409,
        )

    contract = original.contract
    original_allocations = list(
        db.execute(
            select(PaymentAllocation).where(
                PaymentAllocation.payment_id == original.id,
                PaymentAllocation.reversed_by_allocation_id.is_(None),
            )
        ).scalars()
    )

    reversal_payment = Payment(
        contract_id=original.contract_id,
        amount=original.amount,
        external_reference=f"{original.external_reference}-REV",
        status=PaymentStatus.reversed,
        source=PaymentSource.gateway,
        payment_intent_id=intent.id,
        allocated_amount=original.allocated_amount,
        unallocated_amount=_ZERO,
    )
    db.add(reversal_payment)
    db.flush()

    installments_by_id = {
        a.installment_id: db.get(Installment, a.installment_id) for a in original_allocations
    }

    for line in original_allocations:
        installment = installments_by_id[line.installment_id]

        compensating = PaymentAllocation(
            payment_id=reversal_payment.id,
            contract_id=line.contract_id,
            installment_id=line.installment_id,
            late_fee_amount=-line.late_fee_amount,
            profit_amount=-line.profit_amount,
            principal_amount=-line.principal_amount,
        )
        db.add(compensating)
        db.flush()
        line.reversed_by_allocation_id = compensating.id

        if line.late_fee_amount > _ZERO:
            _reverse_late_fee(installment, line.late_fee_amount)
        if line.profit_amount > _ZERO:
            installment.profit_paid = _money(installment.profit_paid - line.profit_amount)
            contract.unearned_profit_balance = _money(
                contract.unearned_profit_balance + line.profit_amount
            )
        if line.principal_amount > _ZERO:
            installment.principal_paid = _money(
                installment.principal_paid - line.principal_amount
            )

        for entry_type, amount in (
            (LedgerEntryType.late_fee_paid, -line.late_fee_amount),
            (LedgerEntryType.profit_recognized, -line.profit_amount),
            (LedgerEntryType.principal_paid, -line.principal_amount),
        ):
            if amount != _ZERO:
                ledger_service.record_entry(
                    db,
                    contract_id=contract.id,
                    entry_type=entry_type,
                    amount=amount,
                    related_action=LedgerRelatedAction.reversal,
                    reference_type="payment",
                    reference_id=reversal_payment.id,
                    created_by=actor_id,
                )

        _update_status_after_reversal(installment)

    original.status = PaymentStatus.reversed
    db.flush()

    # --- accounting-event boundary (additive; never blocks the reversal) ---
    accounting.emit(
        db,
        event_type=_ACCOUNTING_EVENT_BY_STATUS.get(final_status, AccountingEventType.payment_reversed),
        event_reference=f"gateway-reversal-{reversal_payment.id}",
        contract=contract,
        amount=original.amount,
        event_date=_utcnow(),
    )

    # --- Collections: reopen (or open fresh) a case — a case that was closed
    # because this payment cleared the last overdue installment is now, again,
    # a contract with unpaid-for amounts. `open_case_if_needed` is idempotent
    # (no-op if a case is already open) and creates a NEW case row rather than
    # mutating the closed one — the closed case stays intact history.
    collections_service.open_case_if_needed(
        db, contract,
        reason=(
            f"Reopened: settled payment {original.external_reference} was "
            f"{final_status.value.lower()} (gateway reference "
            f"{intent.payment_reference})"
        ),
        actor_id=actor_id,
    )

    db.flush()
    return reversal_payment
