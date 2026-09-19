"""Chart of Accounts — Checkpoint 3: read/propose endpoints for accounts and
mappings, journal read endpoints, a posting retry, and the five GL reports
(reusing reports.py's export infrastructure — see app/services/gl_reports.py).

Approval and rejection reuse the EXISTING generic
``POST /approvals/{id}/approve`` / ``/reject`` — no new approve/reject
endpoint, mirroring every other maker-checker feature in this codebase.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.accounting import AccountingEvent, AccountingEventType
from app.models.gl import GLJournal, GLJournalLine, JournalStatus
from app.models.user import User, UserRole
from app.schemas.approval import ApprovalRequestOut
from app.schemas.gl import (
    ChartOfAccountOut,
    EventAccountMappingOut,
    GLJournalDetailOut,
    GLJournalOut,
    ProposeAccountCreateIn,
    ProposeAccountDeactivateIn,
    ProposeAccountUpdateIn,
    ProposeMappingChangeIn,
    ProposeMappingDeactivateIn,
)
from app.services import coa as coa_service
from app.services import gl_reports as gl_reports_service
from app.services.accounting import retry_failed_posting
from app.services.errors import DomainError
from app.services.reports import EXPORT_FORMATS, export as export_service

router = APIRouter(prefix="/gl", tags=["chart of accounts & general ledger"])

_VIEW_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)
_PROPOSE_ROLES = (UserRole.finance_officer, UserRole.admin)


def _domain(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _export_or_json(fmt: str | None, result, base: str):
    if not fmt:
        return result.data
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {EXPORT_FORMATS}")
    content, media_type, ext = export_service(
        fmt, result.fieldnames, result.rows, title=result.title, totals=result.totals
    )
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{base}.{ext}"'},
    )


# --------------------------------------------------------------------------- #
# Chart of Accounts
# --------------------------------------------------------------------------- #
@router.get("/accounts", response_model=list[ChartOfAccountOut])
def list_accounts(
    is_active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    return coa_service.list_accounts(db, is_active=is_active)


@router.post(
    "/accounts/propose-create", response_model=ApprovalRequestOut, status_code=201
)
def propose_account_create(
    payload: ProposeAccountCreateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_PROPOSE_ROLES)),
):
    try:
        req = coa_service.propose_account_create(
            db, actor_id=actor.id, payload=payload.model_dump(exclude_none=True)
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


@router.post(
    "/accounts/{account_id}/propose-update", response_model=ApprovalRequestOut, status_code=201
)
def propose_account_update(
    account_id: int,
    payload: ProposeAccountUpdateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_PROPOSE_ROLES)),
):
    try:
        req = coa_service.propose_account_update(
            db, actor_id=actor.id, account_id=account_id,
            payload=payload.model_dump(exclude_none=True),
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


@router.post(
    "/accounts/{account_id}/propose-deactivate", response_model=ApprovalRequestOut, status_code=201
)
def propose_account_deactivate(
    account_id: int,
    payload: ProposeAccountDeactivateIn | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_PROPOSE_ROLES)),
):
    try:
        req = coa_service.propose_account_deactivate(
            db, actor_id=actor.id, account_id=account_id,
            reason=payload.reason if payload else None,
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


# --------------------------------------------------------------------------- #
# Event Account Mappings
# --------------------------------------------------------------------------- #
@router.get("/mappings", response_model=list[EventAccountMappingOut])
def list_active_mappings(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    return [EventAccountMappingOut.from_orm_mapping(m) for m in coa_service.list_active_mappings(db)]


@router.get("/mappings/{event_type}/versions", response_model=list[EventAccountMappingOut])
def list_mapping_versions(
    event_type: AccountingEventType,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    return [
        EventAccountMappingOut.from_orm_mapping(m)
        for m in coa_service.list_mapping_versions(db, event_type)
    ]


@router.post("/mappings/propose-change", response_model=ApprovalRequestOut, status_code=201)
def propose_mapping_change(
    payload: ProposeMappingChangeIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_PROPOSE_ROLES)),
):
    try:
        req = coa_service.propose_mapping_change(
            db, actor_id=actor.id, event_type=payload.account_event_type,
            classification=payload.classification.value,
            lines=[l.model_dump() for l in payload.lines],
            effective_from=payload.effective_from,
            description=payload.description,
            change_reason=payload.change_reason,
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


@router.post(
    "/mappings/{event_type}/propose-deactivate", response_model=ApprovalRequestOut, status_code=201
)
def propose_mapping_deactivate(
    event_type: AccountingEventType,
    payload: ProposeMappingDeactivateIn | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_PROPOSE_ROLES)),
):
    try:
        req = coa_service.propose_mapping_deactivate(
            db, actor_id=actor.id, event_type=event_type,
            reason=payload.reason if payload else None,
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


# --------------------------------------------------------------------------- #
# GL Journals
# --------------------------------------------------------------------------- #
@router.get("/journals", response_model=list[GLJournalOut])
def list_journals(
    journal_status: JournalStatus | None = Query(default=None),
    event_type: AccountingEventType | None = Query(default=None),
    contract_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    stmt = select(GLJournal).order_by(GLJournal.id.desc())
    if journal_status is not None:
        stmt = stmt.where(GLJournal.journal_status == journal_status)
    if event_type is not None:
        stmt = stmt.join(AccountingEvent, GLJournal.accounting_event_id == AccountingEvent.id).where(
            AccountingEvent.event_type == event_type
        )
    if contract_id is not None:
        stmt = stmt.join(GLJournalLine, GLJournalLine.journal_id == GLJournal.id).where(
            GLJournalLine.contract_id == contract_id
        ).distinct()
    return db.execute(stmt).scalars().all()


def _journal_detail(db: Session, journal: GLJournal) -> GLJournalDetailOut:
    event = db.get(AccountingEvent, journal.accounting_event_id)
    return GLJournalDetailOut(
        **GLJournalOut.model_validate(journal).model_dump(),
        lines=sorted(journal.lines, key=lambda l: l.line_sequence),
        event=event,
    )


@router.get("/journals/{journal_id}", response_model=GLJournalDetailOut)
def get_journal(
    journal_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    journal = db.get(GLJournal, journal_id)
    if journal is None:
        raise HTTPException(status_code=404, detail="Journal not found")
    return _journal_detail(db, journal)


@router.get("/journals/by-event/{accounting_event_id}", response_model=GLJournalDetailOut)
def get_journal_by_event(
    accounting_event_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    """Drill-down helper: `journal_reference` is deterministically
    `f"GLJ-{accounting_event_id}"` (see gl_journal.py::generate_journal) —
    the frontend parses that suffix straight out of a General Ledger row
    rather than needing a separate lookup-by-reference endpoint."""
    journal = db.execute(
        select(GLJournal).where(GLJournal.accounting_event_id == accounting_event_id)
    ).scalar_one_or_none()
    if journal is None:
        raise HTTPException(status_code=404, detail="No journal exists for that accounting event")
    return _journal_detail(db, journal)


@router.post("/journals/{journal_id}/retry", response_model=GLJournalOut)
def retry_journal_posting(
    journal_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.admin)),
):
    """Retries external posting for a journal that reached READY and was
    balanced, but whose posting attempt failed (never reachable with the
    mock GL, which always succeeds — the mechanism exists for when a real
    provider is connected). Does NOT regenerate an UNMAPPED or
    generation-FAILED journal — see docs/chart-of-accounts/FSD.md §8."""
    journal = db.get(GLJournal, journal_id)
    if journal is None:
        raise HTTPException(status_code=404, detail="Journal not found")
    try:
        retry_failed_posting(db, journal)
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(journal)
    return journal


# --------------------------------------------------------------------------- #
# Reports (reuse reports.py's export infrastructure)
# --------------------------------------------------------------------------- #
_REPORT_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)


@router.get("/reports/chart-of-accounts")
def report_chart_of_accounts(
    format: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_REPORT_ROLES)),
):
    result = gl_reports_service.chart_of_accounts_report(db)
    return _export_or_json(format, result, "chart-of-accounts")


@router.get("/reports/mappings")
def report_mapping_register(
    format: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_REPORT_ROLES)),
):
    result = gl_reports_service.event_mapping_register_report(db)
    return _export_or_json(format, result, "event-mapping-register")


@router.get("/reports/ledger")
def report_general_ledger(
    account_id: int = Query(...),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    journal_status: JournalStatus | None = Query(default=None),
    event_type: AccountingEventType | None = Query(default=None),
    contract_id: int | None = Query(default=None),
    format: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_REPORT_ROLES)),
):
    try:
        result = gl_reports_service.general_ledger_report(
            db, account_id=account_id, date_from=date_from, date_to=date_to,
            journal_status=journal_status, event_type=event_type, contract_id=contract_id,
        )
    except DomainError as exc:
        raise _domain(exc)
    return _export_or_json(format, result, f"general-ledger-{account_id}")


@router.get("/reports/trial-balance")
def report_trial_balance(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    format: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_REPORT_ROLES)),
):
    result = gl_reports_service.trial_balance_report(db, date_from=date_from, date_to=date_to)
    return _export_or_json(format, result, "trial-balance")


@router.get("/reports/unmapped-failed")
def report_unmapped_failed(
    format: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_REPORT_ROLES)),
):
    result = gl_reports_service.unmapped_and_failed_report(db)
    return _export_or_json(format, result, "unmapped-failed-events")
