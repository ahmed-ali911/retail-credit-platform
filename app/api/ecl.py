"""ECL & Provision — the FINANCE / RISK module.

Separate from Credit Assessment, Pricing and Collections. Roles:
``finance_officer`` / ``credit_manager`` / ``admin`` (never sales_employee or
collections_officer).

Every figure is a live read of persisted ``ECLAssessment`` rows — the
methodology, the DPD, the bucket/stage and the config values that were in force
are all snapshotted per assessment (same principle as Credit Assessment).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.user import User, UserRole
from app.schemas.ecl import EclRunRequest, EclRunResult
from app.services import ecl as ecl_service
from app.services import reports as reports_service
from app.services.audit import record_event

router = APIRouter(prefix="/ecl", tags=["ecl & provision"])

_VIEW_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)
_RUN_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)
_view = Depends(require_roles(*_VIEW_ROLES))


@router.get("/dashboard")
def ecl_dashboard(db: Session = Depends(get_db), _: User = _view):
    try:
        return ecl_service.dashboard(db)
    except ValueError as exc:  # e.g. an invalid ecl_methodology in config
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/assessments")
def ecl_assessments(
    db: Session = Depends(get_db),
    assessment_date: date | None = Query(default=None),
    product_id: int | None = Query(default=None),
    risk_band: str | None = Query(default=None),
    dpd_bucket: str | None = Query(default=None),
    contract_status: str | None = Query(default=None),
    format: str | None = Query(default=None),
    _: User = _view,
):
    result = ecl_service.portfolio(
        db,
        assessment_date=assessment_date,
        product_id=product_id,
        risk_band=risk_band,
        dpd_bucket=dpd_bucket,
        contract_status=contract_status,
    )
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


@router.post("/run", response_model=EclRunResult)
def run_ecl(
    payload: EclRunRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_RUN_ROLES)),
):
    """On-demand portfolio ECL recalculation (same pattern as
    ``POST /jobs/assess-overdue`` — not a scheduler). Re-assesses every active
    contract as of the given date and emits ONE ``ecl_provision_movement``
    accounting event for the portfolio's provision movement."""
    as_of = None
    if payload and payload.as_of:
        try:
            as_of = date.fromisoformat(payload.as_of)
        except ValueError:
            raise HTTPException(status_code=422, detail="as_of must be an ISO date")

    try:
        summary = ecl_service.run_ecl(db, as_of=as_of, actor_id=actor.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    record_event(
        db,
        user_id=actor.id,
        action="ecl.run",
        entity_type="ecl_run",
        entity_id=summary.run_id,
        after={
            "as_of_date": summary.as_of_date,
            "methodology": summary.methodology,
            "contracts_assessed": summary.contracts_assessed,
            "total_ecl": summary.total_ecl,
            "total_provision_movement": summary.total_provision_movement,
            "accounting_event_id": summary.accounting_event_id,
        },
    )
    db.commit()
    return EclRunResult(**summary.__dict__)
