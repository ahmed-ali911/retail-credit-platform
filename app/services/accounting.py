"""Accounting-event generation + the on-demand posting job.

`emit(...)` is called from the existing financial flows (contract activation,
payment allocation, late-fee assess/waive, closure). It is **additive** — it
adds one `AccountingEvent` row and changes nothing else. It is idempotent: the
unique `event_reference` means firing the same hook twice is a no-op.

`post_pending(...)` is the job behind `POST /jobs/post-accounting-events`: it
walks every `pending` (and previously `failed`) event, hands it to the mock ERP
adapter, and records the outcome. It never touches business data and is safe to
re-run — an already-`posted` event is skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import (
    AccountingEvent,
    AccountingEventType,
    AccountingStatus,
)
from app.models.contract import InstallmentContract
from app.models.credit_application import CreditApplication
from app.models.gl import GLJournal, JournalStatus
from app.models.sales_order import SalesOrder
from app.services import erp_adapter
from app.services import gl_journal
from app.services.errors import DomainError

_CENTS = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _customer_id_for(db: Session, contract: InstallmentContract) -> int | None:
    """contract -> sales_order -> application -> customer_id."""
    sales_order = db.get(SalesOrder, contract.sales_order_id)
    if sales_order is None:
        return None
    application = db.get(CreditApplication, sales_order.application_id)
    return application.customer_id if application else None


def emit(
    db: Session,
    *,
    event_type: AccountingEventType,
    event_reference: str,
    contract: InstallmentContract,
    amount,
    event_date: datetime | None = None,
    currency: str = "KWD",
    source_table: str | None = None,
    source_id: int | None = None,
) -> AccountingEvent:
    """Create one pending AccountingEvent, or return the existing one.

    Idempotent on `event_reference`. Callers pass the domain object's own
    timestamp as `event_date` where one exists.

    `source_table`/`source_id` (Chart of Accounts feature — see
    `app/models/accounting.py`'s column docstring) name the exact domain row
    that caused this event, for journal generation's amount-source
    resolvers. Journal generation (`gl_journal.generate_journal`) runs ONLY
    on the branch that creates a genuinely new row — never on an idempotent
    replay of an existing `event_reference` — so retrying the same business
    action can never cause a mapping approved afterwards to reach back and
    generate a journal for an event that predates it.
    """
    existing = db.execute(
        select(AccountingEvent).where(
            AccountingEvent.event_reference == event_reference
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    event = AccountingEvent(
        event_type=event_type,
        event_reference=event_reference,
        contract_id=contract.id,
        customer_id=_customer_id_for(db, contract),
        amount=_money(amount),
        currency=currency,
        event_date=event_date or _utcnow(),
        accounting_status=AccountingStatus.pending,
        source_table=source_table,
        source_id=source_id,
    )
    db.add(event)
    db.flush()
    gl_journal.generate_journal(db, event)
    return event


def emit_unscoped(
    db: Session,
    *,
    event_type: AccountingEventType,
    event_reference: str,
    amount,
    event_date: datetime | None = None,
    currency: str = "KWD",
    source_table: str | None = None,
    source_id: int | None = None,
) -> AccountingEvent:
    """Like :func:`emit` but for a **portfolio-level** event that has no single
    contract (the ECL slice's ``ecl_provision_movement``, one per recalculation
    run). Same idempotency on ``event_reference``; ``contract_id`` /
    ``customer_id`` stay NULL.
    """
    existing = db.execute(
        select(AccountingEvent).where(
            AccountingEvent.event_reference == event_reference
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    event = AccountingEvent(
        event_type=event_type,
        event_reference=event_reference,
        contract_id=None,
        customer_id=None,
        amount=_money(amount),
        currency=currency,
        event_date=event_date or _utcnow(),
        accounting_status=AccountingStatus.pending,
        source_table=source_table,
        source_id=source_id,
    )
    db.add(event)
    db.flush()
    gl_journal.generate_journal(db, event)
    return event


@dataclass
class PostingSummary:
    events_considered: int = 0
    posted: int = 0
    failed: int = 0


def _post_one_journal(db: Session, journal: GLJournal) -> bool:
    """Hands one journal to the (mock) ERP adapter and records the outcome
    on both the journal and its underlying `AccountingEvent` (kept in sync —
    see `post_pending`'s docstring). Returns whether it was accepted."""
    event = db.get(AccountingEvent, journal.accounting_event_id)
    result = erp_adapter.post_journal(event, journal, list(journal.lines))
    if result.ok:
        journal.journal_status = JournalStatus.posted
        journal.external_gl_reference = result.external_gl_reference
        journal.error_message = None
        journal.posting_date = _utcnow()
        journal.posted_at = _utcnow()
        event.accounting_status = AccountingStatus.posted
        event.external_gl_reference = result.external_gl_reference
        event.error_message = None
    else:
        journal.journal_status = JournalStatus.failed
        journal.error_message = result.error_message or "post_journal returned not-ok"
        journal.retry_count += 1
        event.accounting_status = AccountingStatus.failed
        event.error_message = journal.error_message
        event.retry_count += 1
    return result.ok


def post_pending(db: Session) -> PostingSummary:
    """Attempt to post every READY `GLJournal` to the (mock) ERP adapter.
    Idempotent — a `POSTED` journal is never reconsidered.

    Only a journal that actually balanced (`JournalStatus.ready`) is ever
    handed to the provider — an `UNMAPPED` or `FAILED` journal (or an event
    with no journal at all: `SUMMARY_ONLY`/`RESERVED`) is left exactly where
    it is; there is nothing valid to post. `AccountingEvent.accounting_status`
    (the pre-existing boundary this feature builds on top of, never
    replaced) is kept in sync with its journal's outcome, so every
    existing consumer of that field keeps working unchanged.
    """
    journals = (
        db.execute(
            select(GLJournal)
            .where(GLJournal.journal_status == JournalStatus.ready)
            .order_by(GLJournal.id)
        )
        .scalars()
        .all()
    )

    summary = PostingSummary()
    for journal in journals:
        summary.events_considered += 1
        if _post_one_journal(db, journal):
            summary.posted += 1
        else:
            summary.failed += 1

    db.flush()
    return summary


def retry_failed_posting(db: Session, journal: GLJournal) -> bool:
    """Re-attempts EXTERNAL posting for a journal that reached READY and was
    genuinely balanced, but whose posting attempt was rejected by the GL
    provider (`journal_status == FAILED` and `is_balanced == True`).

    Deliberately narrower than re-running `generate_journal` — a journal
    that never balanced in the first place (a generation-time failure: no
    lines, `is_balanced == False`) or was never mapped at all is NOT
    retried here; regenerating those is a separate, explicit, role-gated
    operation not implemented in this checkpoint (see
    docs/chart-of-accounts/FSD.md §8).
    """
    if journal.journal_status != JournalStatus.failed or not journal.is_balanced:
        raise DomainError(
            f"Journal {journal.id} is not eligible for a posting retry "
            f"(status={journal.journal_status.value}, is_balanced={journal.is_balanced})",
            status_code=409,
        )
    ok = _post_one_journal(db, journal)
    db.flush()
    return ok
