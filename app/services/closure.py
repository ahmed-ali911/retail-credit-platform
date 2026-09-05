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

from app.models.accounting import AccountingEventType
from app.models.closure import ClosureReason, ContractClosure
from app.models.contract import ContractStatus, InstallmentContract, InstallmentStatus
from app.models.ledger import LedgerEntryType, LedgerRelatedAction
from app.models.payment import LateFeeStatus, Payment, PaymentStatus
from app.models.product import Product
from app.models.sales_order import SalesOrder
from app.services import accounting
from app.services import config_service as cfg
from app.services import ledger as ledger_service
from app.services.config_service import ConfigService
from app.services.errors import DomainError
from app.services.receivable import build_receivable

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_CLOSURE_EVENT_REF_PREFIX = {
    AccountingEventType.early_settlement: "early-settlement",
    AccountingEventType.cancellation: "cancellation",
    AccountingEventType.return_: "return",
    AccountingEventType.contract_closed: "normal-closure",
}


def _emit_closure_event(
    db: Session,
    contract: InstallmentContract,
    closure: ContractClosure,
    event_type: AccountingEventType,
) -> None:
    """One accounting event per closure. ``amount`` is the closure's signed
    ``financial_adjustment`` (same convention). For a plain early settlement the
    adjustment is NULL — the payoff was collected in full via ``/settle`` — so
    the event amount is 0.00 and the money detail lives on the settlement
    Payment and ledger entries."""
    prefix = _CLOSURE_EVENT_REF_PREFIX[event_type]
    accounting.emit(
        db,
        event_type=event_type,
        event_reference=f"{prefix}-{closure.id}",
        contract=contract,
        amount=closure.financial_adjustment if closure.financial_adjustment is not None else _ZERO,
        event_date=closure.closed_at,
    )


def _release_stock(db: Session, contract: InstallmentContract) -> None:
    """Step 10 — a cancelled or returned unit goes back on the shelf. Additive;
    never blocks the closure itself."""
    sales_order = db.get(SalesOrder, contract.sales_order_id)
    if sales_order is None:
        return
    product = db.get(Product, sales_order.product_id)
    if product is not None:
        product.stock_quantity = (product.stock_quantity or 0) + 1


def _guard_not_closed(contract: InstallmentContract) -> None:
    if contract.status == ContractStatus.closed:
        reason = contract.closure.reason.value if contract.closure else "unknown"
        raise DomainError(
            f"Contract {contract.id} is already closed (reason: {reason})",
            status_code=409,
        )


# --------------------------------------------------------------------------- #
# Normal maturity — reaching zero outstanding through ordinary repayment
# --------------------------------------------------------------------------- #
def close_if_fully_repaid(
    db: Session,
    contract: InstallmentContract,
    *,
    actor_id: int | None = None,
) -> ContractClosure | None:
    """Bug fix: a contract that reaches zero outstanding purely through normal
    installment repayment (no settlement/cancellation/return) never got a
    ``ContractClosure`` — nothing called into this module for that path, so
    ``contract.closure`` stayed ``None`` forever and the Step 9 "hide
    Return/Settle once a closure exists" rule was never reached.

    Call this once after every payment allocation (`app.services.payments`).
    It is the exact same closure entity/creation path as settlement,
    cancellation and return: one ``ContractClosure`` (``reason="normal"``,
    ``financial_adjustment=0`` — nothing is owed either way), the contract set
    to ``closed``, and the same accounting-event hook.

    Idempotent and never retroactive:
      * a no-op if the contract is already closed (``contract.closure`` set)
        or isn't ``active`` in the first place
      * a no-op unless every installment is ``paid`` AND the Receivable
        (principal + profit) AND outstanding late fees are all zero *right
        now* — this only fires on the one payment that actually completes the
        contract, never re-evaluated for a contract that was already fully
        paid before this call (there is nothing "retroactive" to rewrite: a
        contract can only pick up its one allowed closure once).
    """
    if contract.status != ContractStatus.active or contract.closure is not None:
        return None
    if any(i.status != InstallmentStatus.paid for i in contract.installments):
        return None
    receivable = build_receivable(contract)
    if receivable.outstanding_receivable > _ZERO or receivable.outstanding_late_fees > _ZERO:
        return None

    closure = ContractClosure(
        contract_id=contract.id,
        reason=ClosureReason.normal,
        financial_adjustment=_ZERO,
        notes="Reached zero outstanding balance through normal installment repayment.",
    )
    db.add(closure)
    contract.status = ContractStatus.closed
    db.flush()

    # --- accounting-event boundary (additive) — same hook every other
    # closure path fires; the ledger already carries every payment's
    # principal/profit/late-fee entries, so there is nothing further to
    # dual-write here (financial_adjustment is 0 by definition).
    _emit_closure_event(db, contract, closure, AccountingEventType.contract_closed)
    db.flush()
    return closure


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
    # BDR item #7 — True when the effective rebate differs from the config
    # default (`early_settlement_profit_rebate_pct`, default 0.0). A deviation
    # means the settlement must go through maker-checker approval before it
    # finalises; a non-deviation settles immediately, exactly as before.
    is_deviation: bool = False


def build_settlement_quote(
    db: Session,
    contract: InstallmentContract,
    *,
    requested_rebate_pct: float | Decimal | None = None,
    requested_rebate_amount: float | Decimal | None = None,
) -> SettlementQuote:
    _guard_not_closed(contract)
    if contract.status != ContractStatus.active:
        raise DomainError(
            f"A settlement quote is only available for an active (delivered) "
            f"contract (current status: {contract.status.value})",
            status_code=409,
        )
    if requested_rebate_pct is not None and requested_rebate_amount is not None:
        raise DomainError(
            "Supply requested_rebate_pct OR requested_rebate_amount, not both.",
            status_code=422,
        )

    config = ConfigService(db)
    default_pct = Decimal(str(config.get_float(cfg.KEY_EARLY_SETTLEMENT_REBATE_PCT)))
    validity_days = config.get_int(cfg.KEY_SETTLEMENT_QUOTE_VALIDITY_DAYS)

    outstanding_principal = sum(
        (i.principal_outstanding for i in contract.installments), _ZERO
    )
    outstanding_late_fees = sum(
        (c.outstanding for c in contract.late_fee_charges), _ZERO
    )
    unearned_profit_total = _money(contract.unearned_profit_balance)

    # Resolve the effective rebate: an explicit staff request wins over the
    # config default; nothing requested → the config default (0.0 by BDR #7).
    if requested_rebate_pct is not None:
        rebate_pct = Decimal(str(requested_rebate_pct))
        if rebate_pct < 0 or rebate_pct > 1:
            raise DomainError(
                "requested_rebate_pct must be between 0 and 1.", status_code=422
            )
        profit_rebate_amount = _money(unearned_profit_total * rebate_pct)
    elif requested_rebate_amount is not None:
        profit_rebate_amount = _money(requested_rebate_amount)
        if profit_rebate_amount < _ZERO or profit_rebate_amount > unearned_profit_total:
            raise DomainError(
                f"requested_rebate_amount must be between 0 and the unearned "
                f"profit ({unearned_profit_total}).",
                status_code=422,
            )
        rebate_pct = (
            (profit_rebate_amount / unearned_profit_total)
            if unearned_profit_total > _ZERO
            else _ZERO
        )
    else:
        rebate_pct = default_pct
        profit_rebate_amount = _money(unearned_profit_total * default_pct)

    default_rebate_amount = _money(unearned_profit_total * default_pct)
    is_deviation = profit_rebate_amount != default_rebate_amount

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
        is_deviation=is_deviation,
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
    actor_id: int | None = None,
    requested_rebate_pct: float | Decimal | None = None,
    requested_rebate_amount: float | Decimal | None = None,
) -> ContractClosure:
    # Always recompute the quote server-side (never trust a stale client value),
    # applying the same staff-requested rebate the quote was built with.
    quote = build_settlement_quote(
        db,
        contract,
        requested_rebate_pct=requested_rebate_pct,
        requested_rebate_amount=requested_rebate_amount,
    )  # also runs the guards

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

    # --- dual-write to the immutable ledger (Phase 1) ---
    # What was actually collected at settlement, plus the waived profit.
    for entry_type, value in (
        (LedgerEntryType.principal_paid, quote.outstanding_principal),
        (LedgerEntryType.late_fee_paid, quote.outstanding_late_fees),
        (LedgerEntryType.profit_recognized, quote.profit_still_charged),
        (LedgerEntryType.profit_rebated, quote.profit_rebate_amount),
    ):
        if value > _ZERO:
            ledger_service.record_entry(
                db,
                contract_id=contract.id,
                entry_type=entry_type,
                amount=value,
                related_action=LedgerRelatedAction.settlement,
                reference_type="contract_closure",
                reference_id=closure.id,
                created_by=actor_id,
            )

    # --- accounting-event boundary (additive) ---
    _emit_closure_event(db, contract, closure, AccountingEventType.early_settlement)
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
    db: Session,
    contract: InstallmentContract,
    *,
    notes: str | None = None,
    actor_id: int | None = None,
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

    # --- dual-write to the immutable ledger (Phase 1) ---
    if refund > _ZERO:
        ledger_service.record_entry(
            db,
            contract_id=contract.id,
            entry_type=LedgerEntryType.refund_issued,
            amount=refund,  # positive: cash owed to the customer
            related_action=LedgerRelatedAction.cancellation,
            reference_type="contract_closure",
            reference_id=closure.id,
            created_by=actor_id,
        )

    # --- accounting-event boundary (additive) ---
    _emit_closure_event(db, contract, closure, AccountingEventType.cancellation)
    _release_stock(db, contract)
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
    db: Session,
    contract: InstallmentContract,
    *,
    notes: str | None = None,
    actor_id: int | None = None,
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

    # --- dual-write to the immutable ledger (Phase 1) ---
    # One signed entry for the net adjustment (same sign convention as
    # ContractClosure.financial_adjustment: >0 owed to customer, <0 owed by).
    if net_adjustment != _ZERO:
        ledger_service.record_entry(
            db,
            contract_id=contract.id,
            entry_type=LedgerEntryType.refund_issued,
            amount=net_adjustment,
            related_action=LedgerRelatedAction.return_,
            reference_type="contract_closure",
            reference_id=closure.id,
            created_by=actor_id,
        )

    # --- accounting-event boundary (additive) ---
    _emit_closure_event(db, contract, closure, AccountingEventType.return_)
    _release_stock(db, contract)
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
