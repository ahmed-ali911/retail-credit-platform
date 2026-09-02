from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.contract import ContractStatus
from app.models.user import User, UserRole
from app.schemas.reports import ContractReportPage
from app.services import reports as reports_service

router = APIRouter(prefix="/reports", tags=["reports & dashboards"])

_REPORT_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)
_roles = Depends(require_roles(*_REPORT_ROLES))


def _csv_response(fieldnames: list[str], rows: list[dict], filename: str) -> Response:
    return Response(
        content=reports_service.to_csv(fieldnames, rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# A — general contract list
# --------------------------------------------------------------------------- #
@router.get("/contracts")
def contracts_report(
    db: Session = Depends(get_db),
    status: ContractStatus | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    product_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    page = reports_service.contract_list(
        db,
        status=status,
        customer_id=customer_id,
        product_id=product_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    if format == "csv":
        return _csv_response(
            reports_service.CONTRACT_FIELDS, page.items, "contracts.csv"
        )
    return ContractReportPage(
        items=page.items, total=page.total, limit=page.limit, offset=page.offset
    )


# --------------------------------------------------------------------------- #
# B — profitability
# --------------------------------------------------------------------------- #
@router.get("/profitability")
def profitability_report(
    db: Session = Depends(get_db),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    product_id: int | None = Query(default=None),
    _: User = _roles,
):
    return reports_service.profitability(
        db, date_from=date_from, date_to=date_to, product_id=product_id
    )


# --------------------------------------------------------------------------- #
# D — five dashboard-tab summaries
# --------------------------------------------------------------------------- #
@router.get("/summary/executive")
def summary_executive(db: Session = Depends(get_db), _: User = _roles):
    return reports_service.summary_executive(db)


@router.get("/summary/operations")
def summary_operations(db: Session = Depends(get_db), _: User = _roles):
    return reports_service.summary_operations(db)


@router.get("/summary/portfolio")
def summary_portfolio(db: Session = Depends(get_db), _: User = _roles):
    return reports_service.summary_portfolio(db)


@router.get("/summary/collections")
def summary_collections(db: Session = Depends(get_db), _: User = _roles):
    return reports_service.summary_collections(db)


@router.get("/summary/credit-risk")
def summary_credit_risk(db: Session = Depends(get_db), _: User = _roles):
    return reports_service.summary_credit_risk(db)
