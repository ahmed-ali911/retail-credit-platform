"""ECL & Provision — the FINANCE / RISK module.

Separate from Credit Assessment, Pricing and Collections. Roles:
``finance_officer`` / ``credit_manager`` / ``admin`` (never sales_employee,
credit_officer or collections_officer).

The engine (three-stage primary; simplified-lifetime and dpd-banded retained)
is driven by a **versioned** ``ECLConfiguration``. Every ``ECLAssessment``
stores the automated result, any manual override, and the final approved
result **separately** — the automated result is never mutated. Manual overrides
(stage / parameter) and configuration changes are MANDATORY maker-checker.

Nothing here touches pricing, payment allocation, late-fee or profit-recognition
logic — ECL reads the receivable and DPD, and writes only its own records +
Accounting Events.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.approval import ACTION_ECL_CONFIG_UPDATE
from app.models.ecl import ECLOverride, ECLOverrideStatus, ECLRun
from app.models.user import User, UserRole
from app.schemas.approval import ApprovalRequestOut, DecisionRequest
from app.schemas.ecl import (
    EclConfigUpdateRequest,
    EclRunRequest,
    EclRunResult,
    ParameterOverrideRequest,
    StageOverrideRequest,
)
from app.services import approvals as approval_service
from app.services import ecl as ecl_service
from app.services import ecl_config as ecl_config_service
from app.services import ecl_override as ecl_override_service
from app.services import reports as reports_service
from app.services.audit import record_event
from app.services.errors import DomainError

router = APIRouter(prefix="/ecl", tags=["ecl & provision"])

_VIEW_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)
_RUN_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)
_MAKER_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)
_view = Depends(require_roles(*_VIEW_ROLES))


def _domain(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


# --------------------------------------------------------------------------- #
# Read models
# --------------------------------------------------------------------------- #
@router.get("/dashboard")
def ecl_dashboard(db: Session = Depends(get_db), _: User = _view):
    try:
        result = ecl_service.dashboard(db)
        db.commit()  # persist the config v1 seed on first ever call
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/assessments")
def ecl_assessments(
    db: Session = Depends(get_db),
    stage: int | None = Query(default=None, ge=1, le=3),
    dpd_band: str | None = Query(default=None),
    risk_rating: str | None = Query(default=None),
    risk_segment: str | None = Query(default=None),
    override_status: str | None = Query(default=None),
    run_id: int | None = Query(default=None),
    contract_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    format: str | None = Query(default=None),
    _: User = _view,
):
    try:
        result = ecl_service.portfolio(
            db,
            stage=stage,
            dpd_band=dpd_band,
            risk_rating=risk_rating,
            risk_segment=risk_segment,
            override_status=override_status,
            run_id=run_id,
            contract_id=contract_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if format:
        if format not in reports_service.EXPORT_FORMATS:
            raise HTTPException(
                status_code=422,
                detail=f"format must be one of {reports_service.EXPORT_FORMATS}",
            )
        content, media_type, ext = reports_service.export(
            format,
            result["columns"],
            result["rows"],
            title="ECL & Provision — portfolio",
            totals=result["totals"],
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="ecl-portfolio.{ext}"'
            },
        )
    return result


@router.get("/runs")
def ecl_runs(db: Session = Depends(get_db), _: User = _view):
    return ecl_service.list_runs(db)


@router.get("/runs/{run_id}")
def ecl_run_detail(run_id: int, db: Session = Depends(get_db), _: User = _view):
    run = db.get(ECLRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ECL run not found")
    return {
        **ecl_service._run_dict(run),
        "assessments": ecl_service.portfolio(db, run_id=run_id, limit=500)["rows"],
    }


@router.get("/contracts/{contract_id}")
def ecl_contract_detail(
    contract_id: int, db: Session = Depends(get_db), _: User = _view
):
    detail = ecl_service.contract_detail(db, contract_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No ECL assessment for this contract yet — it is created at "
                "contract activation, or run the recalculation job."
            ),
        )
    return detail


# --------------------------------------------------------------------------- #
# Runs — calculate / post
# --------------------------------------------------------------------------- #
@router.post("/run", response_model=EclRunResult)
def run_ecl(
    payload: EclRunRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_RUN_ROLES)),
):
    """On-demand portfolio ECL recalculation (same pattern as
    ``POST /jobs/assess-overdue`` — not a scheduler). Produces a COMPLETED run
    (never overwrites a prior run). Pass ``post: true`` to also finalise it and
    emit the accounting events in one call."""
    as_of = None
    if payload and payload.as_of:
        try:
            as_of = date.fromisoformat(payload.as_of)
        except ValueError:
            raise HTTPException(status_code=422, detail="as_of must be an ISO date")

    try:
        summary = ecl_service.run_ecl(db, as_of=as_of, actor_id=actor.id)
        if payload and payload.post:
            run = db.get(ECLRun, summary.run_id)
            summary = ecl_service.post_run(db, run, actor_id=actor.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    record_event(
        db,
        user_id=actor.id,
        action="ecl.run",
        entity_type="ecl_run",
        entity_id=summary.run_id,
        after={
            "run_ref": summary.run_ref,
            "status": summary.status,
            "as_of_date": summary.as_of_date,
            "methodology": summary.methodology,
            "ecl_config_version": summary.ecl_config_version,
            "contracts_assessed": summary.contracts_assessed,
            "total_ecl": summary.total_ecl,
            "total_provision_movement": summary.total_provision_movement,
        },
    )
    db.commit()
    return EclRunResult(**summary.__dict__)


@router.post("/runs/{run_id}/post", response_model=EclRunResult)
def post_run(
    run_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_RUN_ROLES)),
):
    """Finalise a COMPLETED run: emit one accounting event per contract
    provision movement + one portfolio roll-up, and lock the provisions.
    Historical runs are immutable — a POSTED run cannot be re-posted or changed."""
    run = db.get(ECLRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ECL run not found")
    try:
        summary = ecl_service.post_run(db, run, actor_id=actor.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    record_event(
        db,
        user_id=actor.id,
        action="ecl.run_posted",
        entity_type="ecl_run",
        entity_id=run_id,
        after={"run_ref": summary.run_ref, "accounting_event_id": summary.accounting_event_id},
    )
    db.commit()
    return EclRunResult(**summary.__dict__)


# --------------------------------------------------------------------------- #
# Manual overrides — MANDATORY maker-checker
# --------------------------------------------------------------------------- #
@router.post(
    "/assessments/{contract_id}/request-stage-override",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_stage_override(
    contract_id: int,
    payload: StageOverrideRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_MAKER_ROLES)),
):
    try:
        req = ecl_override_service.request_stage_override(
            db, contract_id=contract_id, actor_id=actor.id, payload=payload
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


@router.post(
    "/assessments/{contract_id}/request-parameter-override",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_parameter_override(
    contract_id: int,
    payload: ParameterOverrideRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_MAKER_ROLES)),
):
    try:
        req = ecl_override_service.request_parameter_override(
            db, contract_id=contract_id, actor_id=actor.id, payload=payload
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    db.refresh(req)
    return req


@router.get("/overrides")
def list_overrides(
    db: Session = Depends(get_db),
    status_: str | None = Query(default=None, alias="status"),
    contract_id: int | None = Query(default=None),
    _: User = _view,
):
    stmt = select(ECLOverride).order_by(ECLOverride.id.desc())
    if status_ is not None:
        try:
            stmt = stmt.where(ECLOverride.status == ECLOverrideStatus(status_))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown override status '{status_}'")
    if contract_id is not None:
        stmt = stmt.where(ECLOverride.contract_id == contract_id)
    return [ecl_service._override_dict(o) for o in db.execute(stmt).scalars().all()]


@router.post("/overrides/{override_id}/cancel")
def cancel_override(
    override_id: int,
    payload: DecisionRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_MAKER_ROLES)),
):
    try:
        ov = ecl_override_service.cancel_override(
            db, override_id, actor_id=actor.id, reason=payload.reason if payload else None
        )
    except DomainError as exc:
        raise _domain(exc)
    db.commit()
    return ecl_service._override_dict(ov)


@router.post("/jobs/expire-overrides")
def expire_overrides(
    db: Session = Depends(get_db),
    as_of: str | None = Query(default=None),
    _: User = Depends(require_roles(*_RUN_ROLES)),
):
    d = None
    if as_of:
        try:
            d = date.fromisoformat(as_of)
        except ValueError:
            raise HTTPException(status_code=422, detail="as_of must be an ISO date")
    result = ecl_override_service.expire_due(db, as_of=d)
    db.commit()
    return result


# --------------------------------------------------------------------------- #
# Versioned configuration — MANDATORY maker-checker
# --------------------------------------------------------------------------- #
@router.get("/config")
def get_config(db: Session = Depends(get_db), _: User = _view):
    cfg_row = ecl_config_service.get_active(db)
    snap = ecl_config_service.as_snapshot(cfg_row)
    db.commit()
    return {
        "active": snap,
        "versions": [
            {
                "version": v.version,
                "is_active": v.is_active,
                "methodology": v.methodology.value,
                "notes": v.notes,
                "created_by": v.created_by,
                "activated_at": v.activated_at.isoformat() if v.activated_at else None,
            }
            for v in ecl_config_service.list_versions(db)
        ],
    }


@router.put("/config", response_model=ApprovalRequestOut, status_code=status.HTTP_201_CREATED)
def update_config(
    payload: EclConfigUpdateRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_MAKER_ROLES)),
):
    """Propose a new ECL configuration version. Applied only when a *different*
    user approves the request; on approval a new immutable ``ECLConfiguration``
    version is activated (the prior version stays for reproducing past runs)."""
    cfg_row = ecl_config_service.get_active(db)
    bad = [k for k in payload.changes if k not in ecl_config_service._MUTABLE_FIELDS]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"not ECL-configurable: {bad} (allowed: {list(ecl_config_service._MUTABLE_FIELDS)})",
        )
    req = approval_service.create_request(
        db,
        action_type=ACTION_ECL_CONFIG_UPDATE,
        entity_type="ecl_configuration",
        entity_id=cfg_row.version,
        requested_by=actor.id,
        payload={"changes": payload.changes, "notes": payload.notes, "from_version": cfg_row.version},
    )
    db.commit()
    db.refresh(req)
    return req
