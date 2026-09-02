"""Step 11 — bounded reporting layer.

Every figure here is a real query over real tables. Nothing is estimated,
projected, or hardcoded: if the platform has no way to compute a number today
(true portfolio-at-risk needs ECL, which doesn't exist), it is simply absent.

Scope guards:
  * no BI engine, no scheduled/emailed reports, no saved definitions
  * CSV export only (`to_csv`)
  * DPD buckets are a *display grouping* from `dpd_report_buckets`, never a
    collections-action policy
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.collections import (
    CollectionActivity,
    CollectionCase,
    CollectionCaseStatus,
    PromiseStatus,
)
from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    InstallmentStatus,
)
from app.models.credit_application import ApplicationStatus, CreditApplication
from app.models.customer import Customer
from app.models.closure import ClosureReason
from app.models.payment import LateFeeCharge, LateFeeStatus, Payment
from app.models.product import Product
from app.models.reconciliation import ReconExceptionStatus, ReconciliationException
from app.models.sales_order import SalesOrder
from app.services import config_service as cfg
from app.services import exposure as exposure_service
from app.services.config_service import ConfigService
from app.services.receivable import build_receivable

_ZERO = Decimal("0.00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _utcnow().date()


def _f(value) -> float:
    return round(float(value or 0), 2)


def _as_dt(value: str | date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def to_csv(fieldnames: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# A — general contract list
# --------------------------------------------------------------------------- #
CONTRACT_FIELDS = [
    "contract_id",
    "status",
    "customer_id",
    "customer_name",
    "product_id",
    "product_name",
    "category",
    "tenor_months",
    "installment_sale_price",
    "created_at",
]


@dataclass
class ContractListPage:
    items: list[dict]
    total: int
    limit: int
    offset: int


def _contract_base_query(
    *, status, customer_id, product_id, date_from, date_to
):
    stmt = (
        select(InstallmentContract, SalesOrder, CreditApplication, Product)
        .join(SalesOrder, InstallmentContract.sales_order_id == SalesOrder.id)
        .join(CreditApplication, SalesOrder.application_id == CreditApplication.id)
        .join(Product, SalesOrder.product_id == Product.id)
    )
    if status is not None:
        stmt = stmt.where(InstallmentContract.status == status)
    if customer_id is not None:
        stmt = stmt.where(CreditApplication.customer_id == customer_id)
    if product_id is not None:
        stmt = stmt.where(SalesOrder.product_id == product_id)
    df, dt = _as_dt(date_from), _as_dt(date_to)
    if df is not None:
        stmt = stmt.where(InstallmentContract.created_at >= df)
    if dt is not None:
        stmt = stmt.where(InstallmentContract.created_at <= dt)
    return stmt


def contract_list(
    db: Session,
    *,
    status: ContractStatus | None = None,
    customer_id: int | None = None,
    product_id: int | None = None,
    date_from=None,
    date_to=None,
    limit: int = 50,
    offset: int = 0,
) -> ContractListPage:
    base = _contract_base_query(
        status=status,
        customer_id=customer_id,
        product_id=product_id,
        date_from=date_from,
        date_to=date_to,
    )
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    rows = db.execute(
        base.order_by(InstallmentContract.id.desc()).limit(limit).offset(offset)
    ).all()

    items = []
    for contract, sales_order, application, product in rows:
        items.append(
            {
                "contract_id": contract.id,
                "status": contract.status.value,
                "customer_id": application.customer_id,
                "customer_name": application.customer.name,
                "product_id": product.id,
                "product_name": product.name,
                "category": product.category.value,
                "tenor_months": contract.tenor_months,
                "installment_sale_price": _f(sales_order.sale_price),
                "created_at": contract.created_at.isoformat(),
            }
        )
    return ContractListPage(items=items, total=total, limit=limit, offset=offset)


# --------------------------------------------------------------------------- #
# B — profitability
# --------------------------------------------------------------------------- #
def _recognized_profit_for(contract: InstallmentContract) -> Decimal:
    return sum((i.profit_paid or _ZERO for i in contract.installments), _ZERO)


def profitability(
    db: Session, *, date_from=None, date_to=None, product_id: int | None = None
) -> dict:
    """Contractual / recognized / unearned profit, and the same split by tenor
    and by product category.

    Contracts closed by **cancellation** are excluded — the sale never
    completed, so they generated no profit of any kind. For every other
    contract the identity ``recognized + unearned == contractual`` holds by
    construction (unearned is computed as ``total_profit - recognized``)."""
    stmt = (
        select(InstallmentContract, SalesOrder, Product)
        .join(SalesOrder, InstallmentContract.sales_order_id == SalesOrder.id)
        .join(Product, SalesOrder.product_id == Product.id)
    )
    if product_id is not None:
        stmt = stmt.where(SalesOrder.product_id == product_id)
    df, dt = _as_dt(date_from), _as_dt(date_to)
    if df is not None:
        stmt = stmt.where(InstallmentContract.created_at >= df)
    if dt is not None:
        stmt = stmt.where(InstallmentContract.created_at <= dt)

    contractual = recognized = unearned = _ZERO
    by_tenor: dict[int, dict] = {}
    by_category: dict[str, dict] = {}
    counted = 0

    for contract, _sales_order, product in db.execute(stmt).all():
        if (
            contract.closure is not None
            and contract.closure.reason == ClosureReason.cancellation
        ):
            continue
        counted += 1
        c_total = Decimal(str(contract.total_profit or 0))
        c_recognized = _recognized_profit_for(contract)
        c_unearned = c_total - c_recognized

        contractual += c_total
        recognized += c_recognized
        unearned += c_unearned

        t = by_tenor.setdefault(
            contract.tenor_months,
            {"contractual": _ZERO, "recognized": _ZERO, "unearned": _ZERO, "contracts": 0},
        )
        t["contractual"] += c_total
        t["recognized"] += c_recognized
        t["unearned"] += c_unearned
        t["contracts"] += 1

        cat = by_category.setdefault(
            product.category.value,
            {"contractual": _ZERO, "recognized": _ZERO, "unearned": _ZERO, "contracts": 0},
        )
        cat["contractual"] += c_total
        cat["recognized"] += c_recognized
        cat["unearned"] += c_unearned
        cat["contracts"] += 1

    def _clean(d: dict) -> dict:
        return {
            "contractual_profit": _f(d["contractual"]),
            "recognized_profit": _f(d["recognized"]),
            "unearned_profit": _f(d["unearned"]),
            "contracts": d["contracts"],
        }

    return {
        "contracts_counted": counted,
        "total_contractual_profit": _f(contractual),
        "total_recognized_profit": _f(recognized),
        "total_unearned_profit": _f(unearned),
        "by_tenor": {str(k): _clean(v) for k, v in sorted(by_tenor.items())},
        "by_category": {k: _clean(v) for k, v in sorted(by_category.items())},
    }


# --------------------------------------------------------------------------- #
# shared helpers for the summaries
# --------------------------------------------------------------------------- #
def _all_contracts(db: Session) -> list[InstallmentContract]:
    return list(db.execute(select(InstallmentContract)).scalars().all())


def _application_status_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(CreditApplication.status, func.count()).group_by(
            CreditApplication.status
        )
    ).all()
    return {s.value: n for s, n in rows}


def _contract_max_dpd(contract: InstallmentContract, as_of: date) -> int:
    worst = 0
    for inst in contract.installments:
        if inst.is_fully_paid:
            continue
        if inst.due_date < as_of:
            worst = max(worst, (as_of - inst.due_date).days)
    return worst


def _dpd_distribution(db: Session) -> dict:
    buckets = ConfigService(db).get_json(cfg.KEY_DPD_REPORT_BUCKETS)
    as_of = _today()
    labels = []
    for lo, hi in buckets:
        labels.append(f"{lo}-{hi}" if hi is not None else f"{lo}+")
    counts = {label: 0 for label in labels}
    current = 0
    for contract in db.execute(
        select(InstallmentContract).where(
            InstallmentContract.status == ContractStatus.active
        )
    ).scalars():
        dpd = _contract_max_dpd(contract, as_of)
        if dpd <= 0:
            current += 1
            continue
        for (lo, hi), label in zip(buckets, labels):
            if dpd >= lo and (hi is None or dpd <= hi):
                counts[label] += 1
                break
    return {"current": current, "buckets": counts, "as_of": as_of.isoformat()}


# --------------------------------------------------------------------------- #
# D — five tab summaries
# --------------------------------------------------------------------------- #
def summary_executive(db: Session) -> dict:
    total_customers = db.execute(
        select(func.count()).select_from(Customer)
    ).scalar_one()

    active = [
        c for c in _all_contracts(db) if c.status == ContractStatus.active
    ]
    outstanding = sum(
        (build_receivable(c).outstanding_receivable for c in active), _ZERO
    )
    recognized = sum(
        (_recognized_profit_for(c) for c in _all_contracts(db)), _ZERO
    )

    sc = _application_status_counts(db)
    decided = (
        sc.get("approved", 0) + sc.get("rejected", 0) + sc.get("referred", 0)
    )
    approval_rate = (
        round(sc.get("approved", 0) / decided, 4) if decided else None
    )

    return {
        "total_customers": total_customers,
        "active_contracts": len(active),
        "total_outstanding_receivable": _f(outstanding),
        "total_profit_recognized": _f(recognized),
        "approval_rate": approval_rate,  # all-time: approved / (approved+rejected+referred)
        "decisions_considered": decided,
    }


def summary_operations(db: Session) -> dict:
    today = _today()

    pay_rows = [
        p
        for p in db.execute(select(Payment)).scalars()
        if p.received_at.date() == today
    ]
    # No dedicated submitted_at column — the submit flow requires `draft` status
    # and runs assessment synchronously, so a non-draft application created today
    # was also submitted today.
    apps_today = sum(
        1
        for a in db.execute(select(CreditApplication)).scalars()
        if a.created_at.date() == today
        and a.status != ApplicationStatus.draft
    )

    overdue = 0
    for contract in db.execute(
        select(InstallmentContract).where(
            InstallmentContract.status == ContractStatus.active
        )
    ).scalars():
        for inst in contract.installments:
            if not inst.is_fully_paid and inst.due_date < today:
                overdue += 1

    open_exc = db.execute(
        select(func.count())
        .select_from(ReconciliationException)
        .where(ReconciliationException.status == ReconExceptionStatus.open)
    ).scalar_one()

    return {
        "payments_today_count": len(pay_rows),
        "payments_today_amount": _f(sum((p.amount for p in pay_rows), _ZERO)),
        "applications_submitted_today": apps_today,
        "overdue_installments": overdue,
        "open_reconciliation_exceptions": open_exc,
        "as_of": today.isoformat(),
    }


def summary_portfolio(db: Session) -> dict:
    status_counts = {s.value: 0 for s in ContractStatus}
    sizes: list[Decimal] = []
    for contract, sales_order in db.execute(
        select(InstallmentContract, SalesOrder).join(
            SalesOrder, InstallmentContract.sales_order_id == SalesOrder.id
        )
    ).all():
        status_counts[contract.status.value] += 1
        sizes.append(Decimal(str(sales_order.sale_price or 0)))

    avg_size = _f(sum(sizes, _ZERO) / len(sizes)) if sizes else 0.0

    return {
        "contracts_by_status": status_counts,
        "dpd_distribution": _dpd_distribution(db),
        "average_contract_size": avg_size,
    }


def summary_collections(db: Session) -> dict:
    open_cases = db.execute(
        select(func.count())
        .select_from(CollectionCase)
        .where(CollectionCase.status == CollectionCaseStatus.open)
    ).scalar_one()

    promise_counts = {
        s.value: 0 for s in (PromiseStatus.kept, PromiseStatus.broken)
    }
    for row in db.execute(
        select(CollectionActivity.promise_status, func.count())
        .where(CollectionActivity.promise_status.is_not(None))
        .group_by(CollectionActivity.promise_status)
    ).all():
        ps, n = row
        if ps in (PromiseStatus.kept, PromiseStatus.broken):
            promise_counts[ps.value] = n

    charges = list(db.execute(select(LateFeeCharge)).scalars())
    waived = [c for c in charges if c.status == LateFeeStatus.waived]

    return {
        "open_cases": open_cases,
        "promise_to_pay_kept": promise_counts["kept"],
        "promise_to_pay_broken": promise_counts["broken"],
        "late_fees_charged_count": len(charges),
        "late_fees_charged_amount": _f(sum((c.amount for c in charges), _ZERO)),
        "late_fees_waived_count": len(waived),
        "late_fees_waived_amount": _f(sum((c.amount for c in waived), _ZERO)),
    }


def summary_credit_risk(db: Session) -> dict:
    config = ConfigService(db)
    auto_min = config.get_int(cfg.KEY_RISK_AUTO_APPROVE_MIN)
    refer_min = config.get_int(cfg.KEY_RISK_REFER_MIN)

    bands = {"low": 0, "medium": 0, "high": 0, "unscored": 0}
    customers = list(db.execute(select(Customer)).scalars())
    for c in customers:
        if c.risk_score is None:
            bands["unscored"] += 1
        elif c.risk_score >= auto_min:
            bands["low"] += 1
        elif c.risk_score >= refer_min:
            bands["medium"] += 1
        else:
            bands["high"] += 1

    exposures = []
    for c in customers:
        total = exposure_service.compute_exposure(db, c.id).total_outstanding
        if total > _ZERO:
            exposures.append(
                {"customer_id": c.id, "name": c.name, "total_outstanding": _f(total)}
            )
    exposures.sort(key=lambda e: e["total_outstanding"], reverse=True)

    sc = _application_status_counts(db)
    decided = (
        sc.get("approved", 0) + sc.get("rejected", 0) + sc.get("referred", 0)
    )
    return {
        "customers_by_risk_band": bands,
        "risk_band_thresholds": {
            "low_min": auto_min,
            "medium_min": refer_min,
        },
        "top_customers_by_exposure": exposures[:10],
        "rejection_rate": (
            round(sc.get("rejected", 0) / decided, 4) if decided else None
        ),
        "referral_rate": (
            round(sc.get("referred", 0) / decided, 4) if decided else None
        ),
        "decisions_considered": decided,
    }
