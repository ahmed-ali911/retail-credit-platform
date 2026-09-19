"""Journal generation — turns one `AccountingEvent` + the `EventAccountMapping`
effective at its `event_date` into a balanced `GLJournal` (Checkpoint 2 of the
Chart of Accounts feature; see `app/models/gl.py`'s module docstring for the
five-table shape this completes).

`generate_journal()` is called from `services/accounting.py::emit()` /
`emit_unscoped()`, ONLY on the branch where a NEW `AccountingEvent` row is
created — never on an idempotent replay of an existing one. This is
deliberate: replaying the same business action must never cause a mapping
that was approved *after* the event first occurred to reach back and
generate a journal for it (`app/models/gl.py`'s "never update a historical
posting" rule, extended to "never conjure one either"). Regenerating a
journal for a historical UNMAPPED/FAILED event on purpose is a separate,
explicit, role-gated operation — not implemented in this checkpoint (see
docs/chart-of-accounts/FSD.md §8, a documented remaining gap).

Amount-source resolution never recomputes a business calculation — every
resolver below reads straight from the authoritative row the event's
`source_table`/`source_id` (or, where unambiguous, `contract_id`) points at.
If a resolver can't find that row, it returns `None` — never a fabricated
zero — and `generate_journal` marks the journal `FAILED` with an explainable
`error_message`, per the brief's explicit rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.accounting import AccountingEvent
from app.models.contract import InstallmentContract
from app.models.gl import (
    AmountSource,
    ChartOfAccount,
    EventAccountMapping,
    EventClassification,
    GLJournal,
    GLJournalLine,
    JournalStatus,
    PostingSide,
)
from app.models.ledger import LedgerEntry, LedgerEntryType
from app.models.payment import PaymentAllocation
from app.models.sales_order import SalesOrder
from app.models.write_off import WriteOffExecution

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Amount-source resolvers — each returns a Decimal (possibly signed, possibly
# zero) or None ("cannot find the source data at all" — a genuine gap, never
# to be confused with a legitimate zero, which every resolver below returns
# normally when its authoritative source genuinely nets to nothing).
# --------------------------------------------------------------------------- #
def _contract(db: Session, event: AccountingEvent) -> InstallmentContract | None:
    return db.get(InstallmentContract, event.contract_id) if event.contract_id else None


def _sales_order(db: Session, event: AccountingEvent) -> SalesOrder | None:
    contract = _contract(db, event)
    return db.get(SalesOrder, contract.sales_order_id) if contract else None


def _resolve_event_amount(db: Session, event: AccountingEvent) -> Decimal | None:
    return _money(event.amount)


def _resolve_absolute_event_amount(db: Session, event: AccountingEvent) -> Decimal | None:
    return abs(_money(event.amount))


def _resolve_cash_price(db: Session, event: AccountingEvent) -> Decimal | None:
    contract, so = _contract(db, event), _sales_order(db, event)
    if contract is None or so is None:
        return None
    return _money(so.sale_price) - _money(contract.total_profit)


def _resolve_down_payment(db: Session, event: AccountingEvent) -> Decimal | None:
    so = _sales_order(db, event)
    return _money(so.down_payment_amount) if so is not None else None


def _resolve_financed_principal(db: Session, event: AccountingEvent) -> Decimal | None:
    cash, dp = _resolve_cash_price(db, event), _resolve_down_payment(db, event)
    return None if cash is None or dp is None else cash - dp


def _resolve_total_contractual_profit(db: Session, event: AccountingEvent) -> Decimal | None:
    contract = _contract(db, event)
    return _money(contract.total_profit) if contract is not None else None


def _resolve_gross_installment_receivable(db: Session, event: AccountingEvent) -> Decimal | None:
    fp = _resolve_financed_principal(db, event)
    tp = _resolve_total_contractual_profit(db, event)
    return None if fp is None or tp is None else fp + tp


def _payment_allocation_sum(db: Session, event: AccountingEvent, field: str) -> Decimal | None:
    if event.source_table != "payment" or event.source_id is None:
        return None
    total = db.execute(
        select(func.coalesce(func.sum(getattr(PaymentAllocation, field)), 0)).where(
            PaymentAllocation.payment_id == event.source_id
        )
    ).scalar_one()
    return _money(total)


def _resolve_payment_principal(db: Session, event: AccountingEvent) -> Decimal | None:
    return _payment_allocation_sum(db, event, "principal_amount")


def _resolve_payment_profit(db: Session, event: AccountingEvent) -> Decimal | None:
    return _payment_allocation_sum(db, event, "profit_amount")


def _resolve_payment_late_fee(db: Session, event: AccountingEvent) -> Decimal | None:
    return _payment_allocation_sum(db, event, "late_fee_amount")


def _closure_ledger_sum(db: Session, event: AccountingEvent, *entry_types: LedgerEntryType) -> Decimal | None:
    if event.source_table != "contract_closure" or event.source_id is None:
        return None
    total = db.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.reference_type == "contract_closure",
            LedgerEntry.reference_id == event.source_id,
            LedgerEntry.entry_type.in_(entry_types),
        )
    ).scalar_one()
    return _money(total)


def _resolve_closure_ledger_principal(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.principal_paid)


def _resolve_closure_ledger_late_fee(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.late_fee_paid)


def _resolve_closure_ledger_profit_recognized(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.profit_recognized)


def _resolve_closure_ledger_profit_rebated(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.profit_rebated)


def _resolve_closure_ledger_payoff_total(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(
        db, event,
        LedgerEntryType.principal_paid, LedgerEntryType.late_fee_paid, LedgerEntryType.profit_recognized,
    )


def _resolve_closure_ledger_return_principal(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.return_principal_cleared)


def _resolve_closure_ledger_return_late_fee(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.return_late_fee_cleared)


def _resolve_closure_ledger_return_profit_retained(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.return_profit_retained)


def _resolve_closure_ledger_return_profit_waived(db: Session, event: AccountingEvent) -> Decimal | None:
    return _closure_ledger_sum(db, event, LedgerEntryType.return_profit_waived)


def _write_off_execution(db: Session, event: AccountingEvent) -> WriteOffExecution | None:
    if event.source_table != "write_off_execution" or event.source_id is None:
        return None
    return db.get(WriteOffExecution, event.source_id)


def _resolve_written_off_principal(db: Session, event: AccountingEvent) -> Decimal | None:
    e = _write_off_execution(db, event)
    return _money(e.executed_principal) if e is not None else None


def _resolve_written_off_profit(db: Session, event: AccountingEvent) -> Decimal | None:
    e = _write_off_execution(db, event)
    return _money(e.executed_profit) if e is not None else None


def _resolve_written_off_late_fee(db: Session, event: AccountingEvent) -> Decimal | None:
    e = _write_off_execution(db, event)
    return _money(e.executed_late_fee) if e is not None else None


def _resolve_provision_used(db: Session, event: AccountingEvent) -> Decimal | None:
    e = _write_off_execution(db, event)
    if e is None:
        return None
    return min(_money(e.provision_amount_snapshot or 0), _money(e.total_written_off))


def _resolve_write_off_expense_excess(db: Session, event: AccountingEvent) -> Decimal | None:
    e = _write_off_execution(db, event)
    if e is None:
        return None
    return max(_ZERO, _money(e.total_written_off) - _money(e.provision_amount_snapshot or 0))


_RESOLVERS = {
    AmountSource.event_amount: _resolve_event_amount,
    AmountSource.absolute_event_amount: _resolve_absolute_event_amount,
    AmountSource.cash_price: _resolve_cash_price,
    AmountSource.down_payment: _resolve_down_payment,
    AmountSource.financed_principal: _resolve_financed_principal,
    AmountSource.total_contractual_profit: _resolve_total_contractual_profit,
    AmountSource.gross_installment_receivable: _resolve_gross_installment_receivable,
    AmountSource.payment_principal: _resolve_payment_principal,
    AmountSource.payment_profit: _resolve_payment_profit,
    AmountSource.payment_late_fee: _resolve_payment_late_fee,
    AmountSource.closure_ledger_principal: _resolve_closure_ledger_principal,
    AmountSource.closure_ledger_late_fee: _resolve_closure_ledger_late_fee,
    AmountSource.closure_ledger_profit_recognized: _resolve_closure_ledger_profit_recognized,
    AmountSource.closure_ledger_profit_rebated: _resolve_closure_ledger_profit_rebated,
    AmountSource.closure_ledger_payoff_total: _resolve_closure_ledger_payoff_total,
    AmountSource.closure_ledger_return_principal: _resolve_closure_ledger_return_principal,
    AmountSource.closure_ledger_return_late_fee: _resolve_closure_ledger_return_late_fee,
    AmountSource.closure_ledger_return_profit_retained: _resolve_closure_ledger_return_profit_retained,
    AmountSource.closure_ledger_return_profit_waived: _resolve_closure_ledger_return_profit_waived,
    AmountSource.written_off_principal: _resolve_written_off_principal,
    AmountSource.written_off_profit: _resolve_written_off_profit,
    AmountSource.written_off_late_fee: _resolve_written_off_late_fee,
    AmountSource.provision_used: _resolve_provision_used,
    AmountSource.write_off_expense_excess: _resolve_write_off_expense_excess,
}


# --------------------------------------------------------------------------- #
# Journal generation
# --------------------------------------------------------------------------- #
@dataclass
class _ResolvedLine:
    posting_side: PostingSide
    account: ChartOfAccount
    amount: Decimal
    amount_source: AmountSource
    description: str | None


def _resolve_mapping_for(db: Session, event: AccountingEvent) -> EventAccountMapping | None:
    """The mapping version effective for THIS event's own `event_date` — not
    necessarily the currently active one. Historically correct by
    construction: an approved version's `effective_from`/`effective_to`
    window is closed off prospectively (services/approvals.py), so this
    query finds the right version whether it is the current one or an older,
    since-superseded one."""
    event_date = event.event_date.date() if hasattr(event.event_date, "date") else event.event_date
    return db.execute(
        select(EventAccountMapping)
        .where(
            EventAccountMapping.account_event_type == event.event_type,
            EventAccountMapping.effective_from <= event_date,
            or_(
                EventAccountMapping.effective_to.is_(None),
                EventAccountMapping.effective_to >= event_date,
            ),
        )
        .order_by(EventAccountMapping.version.desc())
    ).scalars().first()


def _build_lines(
    db: Session, event: AccountingEvent, mapping: EventAccountMapping
) -> tuple[list[_ResolvedLine], str | None]:
    resolved: list[_ResolvedLine] = []
    for line in sorted((l for l in mapping.lines if l.is_active), key=lambda l: l.line_sequence):
        resolver = _RESOLVERS.get(line.amount_source)
        if resolver is None:
            return [], f"No resolver implemented for amount_source={line.amount_source.value}"
        raw = resolver(db, event)
        if raw is None:
            return [], (
                f"Could not resolve amount_source={line.amount_source.value} for mapping line "
                f"{line.line_sequence} — the source record this event points at is missing"
            )
        amount = _money(raw * line.multiplier)
        side = line.posting_side
        if amount < _ZERO:
            if not line.reverse_on_negative:
                return [], (
                    f"Mapping line {line.line_sequence} resolved a negative amount ({amount}) but "
                    "is not configured to reverse sides on a negative value"
                )
            amount = -amount
            side = PostingSide.credit if side == PostingSide.debit else PostingSide.debit
        if amount == _ZERO:
            continue  # a legitimately zero line is dropped, never posted
        resolved.append(
            _ResolvedLine(
                posting_side=side, account=line.account, amount=amount,
                amount_source=line.amount_source, description=line.description,
            )
        )
    return resolved, None


def generate_journal(db: Session, event: AccountingEvent) -> GLJournal | None:
    """Idempotent: an existing journal for this event is returned unchanged.
    Returns `None` (no row at all) for a SUMMARY_ONLY/RESERVED classification
    — see the module docstring and app/models/gl.py's EventClassification."""
    existing = db.execute(
        select(GLJournal).where(GLJournal.accounting_event_id == event.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    mapping = _resolve_mapping_for(db, event)
    if mapping is None:
        journal = GLJournal(
            journal_reference=f"GLJ-{event.id}",
            accounting_event_id=event.id,
            mapping_version_id=None,
            journal_status=JournalStatus.unmapped,
            event_date=event.event_date,
            currency=event.currency,
            error_message=(
                f"No EventAccountMapping is effective for {event.event_type.value} at "
                f"{event.event_date}"
            ),
        )
        db.add(journal)
        db.flush()
        return journal

    if mapping.classification != EventClassification.postable:
        return None

    resolved_lines, error = _build_lines(db, event, mapping)
    debit_total = sum((l.amount for l in resolved_lines if l.posting_side == PostingSide.debit), _ZERO)
    credit_total = sum((l.amount for l in resolved_lines if l.posting_side == PostingSide.credit), _ZERO)
    has_debit = any(l.posting_side == PostingSide.debit for l in resolved_lines)
    has_credit = any(l.posting_side == PostingSide.credit for l in resolved_lines)

    journal = GLJournal(
        journal_reference=f"GLJ-{event.id}",
        accounting_event_id=event.id,
        mapping_version_id=mapping.id,
        event_date=event.event_date,
        currency=event.currency,
        accounting_period=event.event_date.strftime("%Y-%m"),
    )

    if error is not None:
        journal.journal_status = JournalStatus.failed
        journal.error_message = error
    elif not has_debit or not has_credit:
        journal.journal_status = JournalStatus.failed
        journal.error_message = (
            "Resolved amounts left no valid debit/credit pair to post "
            "(every line resolved to zero, or only one side survived)"
        )
    elif debit_total != credit_total:
        journal.journal_status = JournalStatus.failed
        journal.error_message = f"Journal does not balance: debit {debit_total} != credit {credit_total}"
    else:
        journal.journal_status = JournalStatus.ready
        journal.total_debit = debit_total
        journal.total_credit = credit_total
        journal.is_balanced = True

    db.add(journal)
    db.flush()

    if journal.journal_status == JournalStatus.ready:
        for i, l in enumerate(resolved_lines, start=1):
            db.add(
                GLJournalLine(
                    journal_id=journal.id,
                    line_sequence=i,
                    posting_side=l.posting_side,
                    account_id=l.account.id,
                    account_code_snapshot=l.account.account_code,
                    account_name_snapshot=l.account.account_name,
                    amount=l.amount,
                    currency=event.currency,
                    amount_source=l.amount_source,
                    contract_id=event.contract_id,
                    customer_id=event.customer_id,
                    description=l.description,
                )
            )
        db.flush()

    return journal
