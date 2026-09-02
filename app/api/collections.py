from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    authorize_owner_or_roles,
    contract_owner_customer_id,
    get_current_user,
    require_roles,
)
from app.core.database import get_db
from app.models.collections import CollectionCase, CollectionCaseStatus
from app.models.contract import InstallmentContract
from app.models.user import User, UserRole
from app.schemas.collections import (
    ActivityCreate,
    CollectionActivityOut,
    CollectionCaseDetailOut,
    CollectionCaseOut,
)
from app.services import collections as collections_service
from app.services.errors import DomainError

router = APIRouter(prefix="/collections", tags=["collections"])

_VIEW_ROLES = (UserRole.collections_officer, UserRole.credit_manager, UserRole.admin)
_ACT_ROLES = (UserRole.collections_officer, UserRole.admin)


def _get_case(db: Session, case_id: int) -> CollectionCase:
    case = db.get(CollectionCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Collection case not found")
    return case


_CASE_CSV_FIELDS = [
    "id",
    "contract_id",
    "status",
    "opened_at",
    "opened_reason",
    "closed_at",
]


@router.get("/cases", response_model=list[CollectionCaseOut])
def list_cases(
    db: Session = Depends(get_db),
    status_: CollectionCaseStatus | None = Query(default=None, alias="status"),
    contract_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    format: str | None = Query(default=None),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    """`date_from` / `date_to` filter on `opened_at`; `format=csv` (Step 11)
    returns the same rows as a CSV download."""
    stmt = select(CollectionCase).order_by(CollectionCase.id.desc())
    if status_ is not None:
        stmt = stmt.where(CollectionCase.status == status_)
    if contract_id is not None:
        stmt = stmt.where(CollectionCase.contract_id == contract_id)
    if date_from is not None:
        stmt = stmt.where(
            CollectionCase.opened_at
            >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
        )
    if date_to is not None:
        stmt = stmt.where(
            CollectionCase.opened_at
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        )
    rows = db.execute(stmt).scalars().all()
    if format:
        from app.services import reports as reports_service

        if format not in reports_service.EXPORT_FORMATS:
            raise HTTPException(
                status_code=422,
                detail=f"format must be one of {reports_service.EXPORT_FORMATS}",
            )
        data = [
            {
                "id": c.id,
                "contract_id": c.contract_id,
                "status": c.status.value,
                "opened_at": c.opened_at.isoformat(),
                "opened_reason": c.opened_reason,
                "closed_at": c.closed_at.isoformat() if c.closed_at else "",
            }
            for c in rows
        ]
        content, media_type, ext = reports_service.export(
            format, _CASE_CSV_FIELDS, data, title="Collection cases"
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="collection-cases.{ext}"'
            },
        )
    return rows


@router.get("/cases/{case_id}", response_model=CollectionCaseDetailOut)
def get_case(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = _get_case(db, case_id)
    contract = db.get(InstallmentContract, case.contract_id)
    authorize_owner_or_roles(
        db, user,
        staff_roles=_VIEW_ROLES,
        owner_customer_id=contract_owner_customer_id(db, contract),
    )
    return case


@router.post(
    "/cases/{case_id}/activities",
    response_model=CollectionActivityOut,
    status_code=status.HTTP_201_CREATED,
)
def log_activity(
    case_id: int,
    payload: ActivityCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_ACT_ROLES)),
):
    case = _get_case(db, case_id)
    try:
        activity = collections_service.log_activity(
            db,
            case,
            created_by=actor.id,
            activity_type=payload.activity_type,
            notes=payload.notes,
            promised_amount=payload.promised_amount,
            promised_date=payload.promised_date,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(activity)
    return activity
