from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.approval import (
    ACTION_LATE_FEE_WAIVE,
    ApprovalRequest,
    ApprovalStatus,
)
from app.models.payment import LateFeeCharge, LateFeeStatus
from app.models.user import User, UserRole
from app.schemas.approval import (
    ApprovalRequestOut,
    DecisionRequest,
    WaiverRequest,
)
from app.services import approvals as approval_service
from app.services.errors import DomainError

router = APIRouter(tags=["maker-checker approvals"])

_REQUEST_WAIVER_ROLES = (
    UserRole.finance_officer,
    UserRole.credit_manager,
    UserRole.admin,
)
# finance_officer added in P0-5 so they can approve reconciliation.manual_match
# requests; the maker != checker rule still applies to every action type.
_DECIDE_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)


@router.post(
    "/late-fees/{late_fee_id}/request-waiver",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_waiver(
    late_fee_id: int,
    payload: WaiverRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_REQUEST_WAIVER_ROLES)),
):
    charge = db.get(LateFeeCharge, late_fee_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="Late fee charge not found")
    if charge.status != LateFeeStatus.assessed:
        raise HTTPException(
            status_code=409,
            detail=f"Only an 'assessed' late fee can be waived (current: {charge.status.value})",
        )
    if approval_service.pending_request_for(db, ACTION_LATE_FEE_WAIVE, charge.id):
        raise HTTPException(
            status_code=409,
            detail="A waiver request is already pending for this late fee",
        )

    req = approval_service.create_request(
        db,
        action_type=ACTION_LATE_FEE_WAIVE,
        entity_type="late_fee_charge",
        entity_id=charge.id,
        requested_by=actor.id,
        payload={"reason": payload.reason},
    )
    db.commit()
    db.refresh(req)
    return req


@router.get("/approvals", response_model=list[ApprovalRequestOut])
def list_approvals(
    db: Session = Depends(get_db),
    status_: ApprovalStatus | None = Query(default=None, alias="status"),
    action_type: str | None = Query(default=None),
    _: User = Depends(require_roles(*_DECIDE_ROLES)),
):
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.id.desc())
    if status_ is not None:
        stmt = stmt.where(ApprovalRequest.status == status_)
    if action_type is not None:
        stmt = stmt.where(ApprovalRequest.action_type == action_type)
    return db.execute(stmt).scalars().all()


def _get_pending(db: Session, approval_id: int) -> ApprovalRequest:
    req = db.get(ApprovalRequest, approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return req


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRequestOut)
def approve(
    approval_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_DECIDE_ROLES)),
):
    req = _get_pending(db, approval_id)
    try:
        approval_service.decide(db, req, decider_id=actor.id, approve=True)
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(req)
    return req


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalRequestOut)
def reject(
    approval_id: int,
    payload: DecisionRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_DECIDE_ROLES)),
):
    req = _get_pending(db, approval_id)
    try:
        approval_service.decide(
            db, req, decider_id=actor.id, approve=False,
            notes=payload.reason if payload else None,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(req)
    return req
