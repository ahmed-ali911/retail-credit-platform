from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.accounting import (
    AccountingEvent,
    AccountingEventType,
    AccountingStatus,
)
from app.models.user import User, UserRole
from app.schemas.accounting import AccountingEventOut, PostAccountingEventsResult
from app.services import accounting as accounting_service
from app.services.audit import record_event

router = APIRouter(tags=["accounting event boundary"])

_VIEW_ROLES = (UserRole.finance_officer, UserRole.admin)


@router.get("/accounting/events", response_model=list[AccountingEventOut])
def list_accounting_events(
    db: Session = Depends(get_db),
    event_type: AccountingEventType | None = Query(default=None),
    accounting_status: AccountingStatus | None = Query(default=None),
    contract_id: int | None = Query(default=None),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    stmt = select(AccountingEvent).order_by(AccountingEvent.id.desc())
    if event_type is not None:
        stmt = stmt.where(AccountingEvent.event_type == event_type)
    if accounting_status is not None:
        stmt = stmt.where(AccountingEvent.accounting_status == accounting_status)
    if contract_id is not None:
        stmt = stmt.where(AccountingEvent.contract_id == contract_id)
    return db.execute(stmt).scalars().all()


@router.post(
    "/jobs/post-accounting-events", response_model=PostAccountingEventsResult
)
def post_accounting_events(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.admin)),
):
    """On-demand posting job (like /jobs/assess-overdue — not a scheduler).

    Hands every non-`posted` event to the mock ERP adapter. Idempotent and safe
    to re-run: an already-`posted` event is never re-posted; `failed` ones are
    re-attempted.
    """
    summary = accounting_service.post_pending(db)
    record_event(
        db,
        user_id=actor.id,
        action="accounting.events_posted",
        entity_type="job",
        entity_id=None,
        after={
            "events_considered": summary.events_considered,
            "posted": summary.posted,
            "failed": summary.failed,
        },
    )
    db.commit()
    return PostAccountingEventsResult(
        events_considered=summary.events_considered,
        posted=summary.posted,
        failed=summary.failed,
    )
