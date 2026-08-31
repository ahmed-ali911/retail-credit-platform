from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.approval import ACTION_RECON_MANUAL_MATCH
from app.models.payment import Payment, PaymentReconciliationStatus
from app.models.reconciliation import (
    BankStatementLine,
    ReconExceptionStatus,
    ReconciliationException,
)
from app.models.user import User, UserRole
from app.schemas.approval import ApprovalRequestOut
from app.schemas.reconciliation import (
    BankLineCreate,
    BankStatementLineOut,
    ManualMatchRequest,
    MatchRunResult,
    ReconciliationExceptionOut,
    ReconciliationStatusOut,
)
from app.services import approvals as approval_service
from app.services import reconciliation as recon_service

router = APIRouter(prefix="/reconciliation", tags=["bank reconciliation"])

_INGEST_ROLES = (UserRole.finance_officer, UserRole.admin)
_VIEW_EXCEPTION_ROLES = (
    UserRole.finance_officer,
    UserRole.credit_manager,
    UserRole.admin,
)
_REQUEST_MATCH_ROLES = _VIEW_EXCEPTION_ROLES


@router.post(
    "/bank-lines",
    response_model=BankStatementLineOut,
    status_code=status.HTTP_201_CREATED,
)
def ingest_bank_line(
    payload: BankLineCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_INGEST_ROLES)),
):
    """Mock bank-feed import — one line at a time (no real bank integration)."""
    line = recon_service.ingest_bank_line(
        db,
        bank_reference=payload.bank_reference,
        amount=payload.amount,
        value_date=payload.value_date,
        actor_id=actor.id,
    )
    db.commit()
    db.refresh(line)
    return line


@router.post("/run", response_model=MatchRunResult)
def run_matching(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_INGEST_ROLES)),
):
    summary = recon_service.run_matching(db, actor_id=actor.id)
    db.commit()
    return MatchRunResult(
        lines_processed=summary.lines_processed,
        matched=summary.matched,
        exceptions_created=summary.exceptions_created,
    )


@router.get("/exceptions", response_model=list[ReconciliationExceptionOut])
def list_exceptions(
    db: Session = Depends(get_db),
    status_: ReconExceptionStatus | None = Query(default=None, alias="status"),
    _: User = Depends(require_roles(*_VIEW_EXCEPTION_ROLES)),
):
    stmt = select(ReconciliationException).order_by(ReconciliationException.id.desc())
    if status_ is not None:
        stmt = stmt.where(ReconciliationException.status == status_)
    return db.execute(stmt).scalars().all()


@router.post(
    "/exceptions/{exception_id}/request-match",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_manual_match(
    exception_id: int,
    payload: ManualMatchRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_REQUEST_MATCH_ROLES)),
):
    """Ask for a bank line to be manually matched to a specific payment.

    Restricted — reuses the generic maker-checker: a *different*
    finance_officer / credit_manager / admin must approve via
    `POST /approvals/{id}/approve`, which then performs the match.
    """
    exception = db.get(ReconciliationException, exception_id)
    if exception is None:
        raise HTTPException(status_code=404, detail="Reconciliation exception not found")
    if exception.status != ReconExceptionStatus.open:
        raise HTTPException(
            status_code=409,
            detail=f"Exception {exception_id} is already {exception.status.value}",
        )
    payment = db.get(Payment, payload.payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Target payment not found")
    if payment.reconciliation_status == PaymentReconciliationStatus.reconciled:
        raise HTTPException(
            status_code=409, detail="That payment is already reconciled"
        )
    if approval_service.pending_request_for(
        db, ACTION_RECON_MANUAL_MATCH, exception.id
    ):
        raise HTTPException(
            status_code=409,
            detail="A manual-match request is already pending for this exception",
        )

    req = approval_service.create_request(
        db,
        action_type=ACTION_RECON_MANUAL_MATCH,
        entity_type="reconciliation_exception",
        entity_id=exception.id,
        requested_by=actor.id,
        payload={"payment_id": payment.id, "reason": payload.reason},
    )
    db.commit()
    db.refresh(req)
    return req


@router.get("/status", response_model=ReconciliationStatusOut)
def reconciliation_status(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.finance_officer, UserRole.admin)),
):
    def count(model, *where):
        return db.execute(
            select(func.count()).select_from(model).where(*where)
        ).scalar_one()

    return ReconciliationStatusOut(
        unreconciled_payments=count(
            Payment,
            Payment.reconciliation_status == PaymentReconciliationStatus.unreconciled,
        ),
        reconciled_payments=count(
            Payment,
            Payment.reconciliation_status == PaymentReconciliationStatus.reconciled,
        ),
        exception_payments=count(
            Payment,
            Payment.reconciliation_status == PaymentReconciliationStatus.exception,
        ),
        open_exceptions=count(
            ReconciliationException,
            ReconciliationException.status == ReconExceptionStatus.open,
        ),
        resolved_exceptions=count(
            ReconciliationException,
            ReconciliationException.status == ReconExceptionStatus.resolved,
        ),
        unmatched_bank_lines=count(
            BankStatementLine, BankStatementLine.matched_payment_id.is_(None)
        ),
    )
