from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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


def _maybe_export(
    fmt: str | None,
    fieldnames: list[str],
    rows: list[dict],
    *,
    title: str,
    base: str,
) -> Response | None:
    """Return a file Response for csv/xlsx/pdf, or None to fall through to JSON."""
    if not fmt:
        return None
    if fmt not in reports_service.EXPORT_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"format must be one of {reports_service.EXPORT_FORMATS}",
        )
    content, media_type, ext = reports_service.export(
        fmt, fieldnames, rows, title=title
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{base}.{ext}"'},
    )


def _result(fmt: str | None, result, base: str):
    exp = _maybe_export(
        fmt, result.fieldnames, result.rows, title=result.title, base=base
    )
    return exp if exp is not None else result.data


# --------------------------------------------------------------------------- #
# Step 11 — general contract list (now with xlsx/pdf too)
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
    exp = _maybe_export(
        format,
        reports_service.CONTRACT_FIELDS,
        page.items,
        title="Contracts",
        base="contracts",
    )
    if exp is not None:
        return exp
    return ContractReportPage(
        items=page.items, total=page.total, limit=page.limit, offset=page.offset
    )


# --------------------------------------------------------------------------- #
# Step 11 profitability — Step 12 adds the `level` drill-down
# --------------------------------------------------------------------------- #
@router.get("/profitability")
def profitability_report(
    db: Session = Depends(get_db),
    level: str = Query(default="portfolio"),
    category: str | None = Query(default=None),
    product_id: int | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    if level not in reports_service.PROFITABILITY_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"level must be one of {reports_service.PROFITABILITY_LEVELS}",
        )
    _required = {"category": category, "product": product_id, "customer": customer_id}
    if level in _required and _required[level] is None:
        name = "category" if level == "category" else f"{level}_id"
        raise HTTPException(
            status_code=422, detail=f"level={level} requires the '{name}' parameter"
        )

    report = reports_service.profitability(
        db,
        level=level,
        category=category,
        product_id=product_id,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
    )
    if format:
        fields, rows = reports_service.profitability_table(report)
        exp = _maybe_export(
            format, fields, rows, title="Profitability", base="profitability"
        )
        if exp is not None:
            return exp
    return report


# --------------------------------------------------------------------------- #
# Step 11 — five dashboard-tab summaries (JSON only — tiles, not tables)
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


# --------------------------------------------------------------------------- #
# Step 13 — per-category sub-reports
# --------------------------------------------------------------------------- #
@router.get("/customers/by-risk")
def customers_by_risk(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(format, reports_service.customers_by_risk(db), "customers-by-risk")


@router.get("/customers/by-exposure")
def customers_by_exposure(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    if format:
        # export the full ranked list, not just the current page
        full = reports_service.customers_by_exposure(db, limit=10**9, offset=0)
        return _result(format, full, "customers-by-exposure")
    return reports_service.customers_by_exposure(
        db, limit=limit, offset=offset
    ).data


@router.get("/products/by-availability")
def products_by_availability(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(
        format,
        reports_service.products_by_availability(db),
        "products-by-availability",
    )


@router.get("/products/by-category")
def products_by_category(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(
        format, reports_service.products_by_category(db), "products-by-category"
    )


@router.get("/contracts/by-status")
def contracts_by_status(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(
        format, reports_service.contracts_by_status(db), "contracts-by-status"
    )


@router.get("/contracts/by-channel")
def contracts_by_channel(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(
        format, reports_service.contracts_by_channel(db), "contracts-by-channel"
    )


@router.get("/collections/status-summary")
def collections_status_summary(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(
        format,
        reports_service.collections_status_summary(db),
        "collections-status-summary",
    )


@router.get("/collections/promise-performance")
def collections_promise_performance(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(
        format,
        reports_service.collections_promise_performance(db),
        "collections-promise-performance",
    )


@router.get("/collections/late-fees-summary")
def collections_late_fees_summary(
    db: Session = Depends(get_db),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    return _result(
        format,
        reports_service.collections_late_fees_summary(db),
        "collections-late-fees-summary",
    )


# --------------------------------------------------------------------------- #
# Step 13 — NEW: Aging
# --------------------------------------------------------------------------- #
@router.get("/aging")
def aging_report(
    db: Session = Depends(get_db),
    bucket: int | None = Query(default=None),
    format: str | None = Query(default=None),
    _: User = _roles,
):
    if bucket is None:
        return _result(format, reports_service.aging_report(db), "aging")
    try:
        result = reports_service.aging_bucket_detail(db, bucket)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _result(format, result, f"aging-bucket-{bucket}")
