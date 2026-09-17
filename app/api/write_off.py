"""Write-off & Recovery — eligibility + write-off REQUEST endpoints.

Financial execution is a later checkpoint (see services/write_off.py's
module docstring) — there is deliberately no "execute" endpoint here yet.
Approval itself happens through the EXISTING generic
``POST /approvals/{id}/approve`` / ``/reject`` — no new approve/reject
endpoint, mirroring exactly how ECL stage/parameter overrides work.

Roles: Collections / Finance / Credit Manager / Admin — never
sales_employee or credit_officer (this is a Collections/Finance decision,
not an origination one). Maker and checker are the SAME role set, exactly
like the ECL override precedent — the `decided_by != requested_by` rule
(enforced in services/approvals.py) is what actually separates them, not a
role split.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.contract import InstallmentContract
from app.models.user import User, UserRole
from app.models.write_off import WriteOffRequest, WriteOffRequestStatus
from app.schemas.approval import ApprovalRequestOut
from app.schemas.write_off import (
    EligibilityResultOut,
    WriteOffRequestCancelIn,
    WriteOffRequestCreate,
    WriteOffRequestOut,
)
from app.services import write_off as write_off_service
from app.services.errors import DomainError

router = APIRouter(prefix="/write-offs", tags=["write-off & recovery"])

_VIEW_ROLES = (
    UserRole.collections_officer,
    UserRole.finance_officer,
    UserRole.credit_manager,
    UserRole.admin,
)
_MAKER_ROLES = _VIEW_ROLES


def _domain(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _get_contract(db: Session, contract_id: int) -> InstallmentContract:
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.get("/eligibility/{contract_id}", response_model=EligibilityResultOut)
def get_eligibility(
    contract_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    """Read-only — never audit-logged (matches this codebase's convention
    that audit records mutations, not views). See
    writeoff.eligibility_evaluated for the audited version, fired inside
    request creation."""
    contract = _get_contract(db, contract_id)
    result = write_off_service.evaluate_eligibility(db, contract)
    return EligibilityResultOut(
        contract_id=contract_id,
        status=result.status,
        indicators=[
            {
                "id": i.id, "name": i.name, "category": i.category,
                "result": i.result, "detail": i.detail,
            }
            for i in result.indicators
        ],
        dpd=result.dpd,
        ecl_stage=result.ecl_stage,
        ecl_amount=float(result.ecl_amount) if result.ecl_amount is not None else None,
        provision_amount=float(result.provision_amount) if result.provision_amount is not None else None,
        collections_case_id=result.collections_case_id,
        collections_case_status=result.collections_case_status,
    )


@router.post(
    "/contracts/{contract_id}/requests",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def create_write_off_request(
    contract_id: int,
    payload: WriteOffRequestCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_MAKER_ROLES)),
):
    try:
        req = write_off_service.request_write_off(
            db, contract_id=contract_id, actor_id=actor.id, payload=payload
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


@router.get("/requests", response_model=list[WriteOffRequestOut])
def list_write_off_requests(
    db: Session = Depends(get_db),
    status_: WriteOffRequestStatus | None = Query(default=None, alias="status"),
    contract_id: int | None = Query(default=None),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    stmt = select(WriteOffRequest).order_by(WriteOffRequest.id.desc())
    if status_ is not None:
        stmt = stmt.where(WriteOffRequest.status == status_)
    if contract_id is not None:
        stmt = stmt.where(WriteOffRequest.contract_id == contract_id)
    return db.execute(stmt).scalars().all()


@router.get("/requests/{request_id}", response_model=WriteOffRequestOut)
def get_write_off_request(
    request_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    wo = db.get(WriteOffRequest, request_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="Write-off request not found")
    return wo


@router.post("/requests/{request_id}/cancel", response_model=WriteOffRequestOut)
def cancel_write_off_request(
    request_id: int,
    payload: WriteOffRequestCancelIn | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_MAKER_ROLES)),
):
    try:
        wo = write_off_service.cancel_request(
            db, request_id, actor_id=actor.id, reason=payload.reason if payload else None
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    return wo
