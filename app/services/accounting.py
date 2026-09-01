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
from app.models.sales_order import SalesOrder
from app.services import erp_adapter

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
) -> AccountingEvent:
    """Create one pending AccountingEvent, or return the existing one.

    Idempotent on `event_reference`. Callers pass the domain object's own
    timestamp as `event_date` where one exists.
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
    )
    db.add(event)
    db.flush()
    return event


@dataclass
class PostingSummary:
    events_considered: int = 0
    posted: int = 0
    failed: int = 0


def post_pending(db: Session) -> PostingSummary:
    """Attempt to post every event not already `posted`. Idempotent."""
    rows = (
        db.execute(
            select(AccountingEvent)
            .where(AccountingEvent.accounting_status != AccountingStatus.posted)
            .order_by(AccountingEvent.id)
        )
        .scalars()
        .all()
    )

    summary = PostingSummary()
    for event in rows:
        summary.events_considered += 1
        result = erp_adapter.post_event(event)
        if result.ok:
            event.accounting_status = AccountingStatus.posted
            event.external_gl_reference = result.external_gl_reference
            event.error_message = None
            summary.posted += 1
        else:
            event.accounting_status = AccountingStatus.failed
            event.error_message = result.error_message or "post_event returned not-ok"
            event.retry_count += 1
            summary.failed += 1

    db.flush()
    return summary
