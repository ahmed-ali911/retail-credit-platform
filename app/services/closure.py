"""Contract closure — the three non-maturity endings.

  * early settlement  (contract must be ``active``)
  * cancellation      (contract must be ``created`` — pre-delivery)
  * return            (contract must be ``active`` — post-delivery)

Every path writes exactly one ``ContractClosure`` and sets the contract to
``closed``. A ``closed`` contract cannot be settled / cancelled / returned again.

Every formula here is driven by a FICTIONAL PLACEHOLDER config value — see
``config/business_rules.yaml``. None of this is confirmed commercial policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models.closure import ClosureReason, ContractClosure
from app.models.contract import ContractStatus, InstallmentContract, InstallmentStatus
from app.models.payment import LateFeeStatus, Payment, PaymentStatus
from app.services import config_service as cfg
from app.services.config_service import ConfigService
from app.services.errors import DomainError

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _guard_not_closed(contract: InstallmentContract) -> None:
    if contract.status == ContractStatus.closed:
        reason = contract.closure.reason.value if contract.closure else "unknown"
        raise DomainError(
            f"Contract {contract.id} is already closed (reason: {reason})",
            status_code=409,
        )


# --------------------------------------------------------------------------- #
# Early settlement
# --------------------------------------------------------------------------- #
@dataclass
class SettlementQuote:
    contract_id: int
    outstanding_principal: Decimal
    outstanding_late_fees: Decimal
    unearned_profit_total: Decimal
    profit_rebate_pct: Decimal
    profit_rebate_amount: Decimal
    profit_still_charged: Decimal
    final_payoff_amount: Decimal
    quote_expiry: datetime


def build_settlement_quote(db: Session, contract: InstallmentContract) -> SettlementQuote:
    _guard_not_closed(contract)
    if contract.status != ContractStatus.active:
        raise DomainError(
            f"A settlement quote is only available for an active (delivered) "
            f"contract (current status: {contract.status.value})",
            status_code=409,
        )

    config = ConfigService(db)
    rebate_pct = Decimal(str(config.get_float(cfg.KEY_EARLY_SETTLEMENT_REBATE_PCT)))
    validity_days = config.get_int(cfg.KEY_SETTLEMENT_QUOTE_VALIDITY_DAYS)

    outstanding_principal = sum(
        (i.principal_outstanding for i in contract.installments), _ZERO
    )
    outstanding_late_fees = sum(
        (c.outstanding for c in contract.late_fee_charges), _ZERO
    )
    unearned_profit_total = _money(contract.unearned_profit_balance)

    profit_rebate_amount = _money(unearned_profit_total * rebate_pct)
    profit_still_charged = unearned_profit_total - profit_rebate_amount
    final_payoff_amount = (
        _money(outstanding_principal)
        + _money(outstanding_late_fees)
        + profit_still_charged
    )

    return SettlementQuote(
        contract_id=contract.id,
        outstanding_principal=_money(outstanding_principal),
        outstanding_late_fees=_money(outstanding_late_fees),
        unearned_profit_total=unearned_profit_total,
        profit_rebate_pct=rebate_pct,
        profit_rebate_amount=profit_rebate_amount,
        profit_still_charged=profit_still_charged,
        final_payoff_amount=final_payoff_amount,
        quote_expiry=_utcnow() + timedelta(days=validity_days),
    )


def _close_out_schedule(contract: InstallmentContract) -> None:
    """Mark every remaining installment paid and every late fee settled, and
    zero the unearned-profit balance. Used by settlement and return."""
    for inst in contract.installments:
        inst.principal_paid = _money(inst.principal_component)
        inst.profit_paid = _money(inst.profit_component)
        inst.status = InstallmentStatus.paid
    for charge in contract.late_fee_charges:
        if charge.status != LateFeeStatus.waived:
            charge.amount_paid = _money(charge.amount)
            charge.status = LateFeeStatus.paid
    contract.unearned_profit_balance = _ZERO


def settle_contract(
    db: Session,
    contract: InstallmentContract,
    *,
    amount: float,
    external_reference: str,
) -> ContractClosure:
    quote = build_settlement_quote(db, contract)  # also runs the guards

    if _money(amount) != quote.final_payoff_amount:
        raise DomainError(
            f"amount {_money(amount)} does not match the current payoff quote "
            f"of {quote.final_payoff_amount}. Fetch a fresh settlement quote.",
            status_code=422,
        )

    payment = Payment(
        contract_id=contract.id,
        amount=_money(amount),
        external_reference=(external_reference or "").strip(),
        status=PaymentStatus.applied,
        allocated_amount=_money(amount),
        unallocated_amount=_ZERO,
    )
    db.add(payment)

    _close_out_schedule(contract)

    closure = ContractClosure(
        contract_id=contract.id,
        reason=ClosureReason.early_settlement,
        financial_adjustment=None,
        notes=(
            f"Early settlement. Payoff {quote.final_payoff_amount} "
            f"(principal {quote.outstanding_principal} + late fees "
            f"{quote.outstanding_late_fees} + profit still charged "
            f"{quote.profit_still_charged}; profit rebate waived "
            f"{quote.profit_rebate_amount})."
        ),
    )
    db.add(closure)
    contract.status = ContractStatus.closed
    db.flush()
    return closure


# --------------------------------------------------------------------------- #
# Cancellation (pre-delivery)
# --------------------------------------------------------------------------- #
@dataclass
class CancellationResult:
    closure: ContractClosure
    down_payment_amount: Decimal
    refund_pct: Decimal
    down_payment_refund: Decimal


def cancel_contract(
    db: Session, contract: InstallmentContract, *, notes: str | None = None
) -> CancellationResult:
    _guard_not_closed(contract)
    if contract.status == ContractStatus.active:
        raise DomainError(
            "Contract is already delivered (active); cancellation is pre-delivery "
            "only. Use POST /contracts/{id}/return instead.",
            status_code=409,
        )
    if contract.status != ContractStatus.created:
        raise DomainError(
            f"Cannot cancel a contract in status {contract.status.value}",
            status_code=409,
        )

    config = ConfigService(db)
    refund_pct = Decimal(str(config.get_float(cfg.KEY_DP_REFUND_PCT_CANCELLATION)))
    down_payment = _money(contract.sales_order.down_payment_amount)
    refund = _money(down_payment * refund_pct)

    contract.unearned_profit_balance = _ZERO
    closure = ContractClosure(
        contract_id=contract.id,
        reason=ClosureReason.cancellation,
        financial_adjustment=refund,  # positive: refund owed to the customer
        notes=notes
        or (
            f"Pre-delivery cancellation. Down payment {down_payment} x "
            f"refund pct {refund_pct} = refund {refund} to customer."
        ),
    )
    db.add(closure)
    contract.status = ContractStatus.closed
    db.flush()
    return CancellationResult(
        closure=closure,
        down_payment_amount=down_payment,
        refund_pct=refund_pct,
        down_payment_refund=refund,
    )


# --------------------------------------------------------------------------- #
# Return (post-delivery)
# --------------------------------------------------------------------------- #
@dataclass
class ReturnResult:
    closure: ContractClosure
    quote: SettlementQuote
    down_payment_amount: Decimal
    refund_pct: Decimal
    down_payment_refund: Decimal
    net_adjustment: Decimal
    ownership_transfers_on_delivery: bool


def return_contract(
    db: Session, contract: InstallmentContract, *, notes: str | None = None
) -> ReturnResult:
    _guard_not_closed(contract)
    if contract.status == ContractStatus.created:
        raise DomainError(
            "Contract is not yet delivered (created); return is post-delivery "
            "only. Use POST /contracts/{id}/cancel instead.",
            status_code=409,
        )

    # Same shape as the settlement quote for the principal/profit/late-fee side.
    quote = build_settlement_quote(db, contract)

    config = ConfigService(db)
    refund_pct = Decimal(str(config.get_float(cfg.KEY_DP_REFUND_PCT_RETURN)))
    ownership = bool(config.get(cfg.KEY_OWNERSHIP_TRANSFERS_ON_DELIVERY))
    down_payment = _money(contract.sales_order.down_payment_amount)
    down_payment_refund = _money(down_payment * refund_pct)

    # signed, from the customer's point of view:
    #   positive -> net refund owed to the customer
    #   negative -> customer still owes this to the company
    net_adjustment = down_payment_refund - quote.final_payoff_amount

    _close_out_schedule(contract)

    closure = ContractClosure(
        contract_id=contract.id,
        reason=ClosureReason.return_,
        financial_adjustment=net_adjustment,
        notes=notes
        or (
            f"Post-delivery return. Settlement-shape payoff "
            f"{quote.final_payoff_amount} vs down-payment refund "
            f"{down_payment_refund} (pct {refund_pct}); net adjustment "
            f"{net_adjustment} (>0 refund to customer, <0 owed by customer). "
            f"ownership_transfers_on_delivery={ownership}."
        ),
    )
    db.add(closure)
    contract.status = ContractStatus.closed
    db.flush()
    return ReturnResult(
        closure=closure,
        quote=quote,
        down_payment_amount=down_payment,
        refund_pct=refund_pct,
        down_payment_refund=down_payment_refund,
        net_adjustment=net_adjustment,
        ownership_transfers_on_delivery=ownership,
    )
