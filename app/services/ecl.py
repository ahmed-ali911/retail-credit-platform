"""ECL & provision orchestration.

Responsibilities:
  * gather each contract's inputs (EAD, DPD, corroborating risk signals) and the
    active config into an ``EclContext``;
  * dispatch to the methodology provider chosen by ``ecl_methodology``;
  * persist an ``ECLAssessment`` (append-only history, upsert per contract+date);
  * for a portfolio ``ECLRun``, roll the movements up and emit ONE
    ``ecl_provision_movement`` accounting event through the existing boundary.

ECL is assessed from contract activation onward — ``initial_assessment`` runs at
the same hook as the accounting-event boundary (``confirm_delivery``), so a
brand-new active contract with 0 DPD already carries a provision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import AccountingEventType
from app.models.collections import (
    CollectionActivity,
    CollectionActivityType,
    CollectionCase,
    CollectionCaseStatus,
    PromiseStatus,
)
from app.models.contract import ContractStatus, InstallmentContract
from app.models.credit_application import CreditApplication
from app.models.customer import Customer
from app.models.ecl import ECLAssessment, ECLMethodology, ECLRun
from app.models.sales_order import SalesOrder
from app.services import accounting
from app.services import config_service as cfg
from app.services import ecl_engine
from app.services.config_service import ConfigService
from app.services.receivable import build_receivable
from app.services.reports import risk_band_of

_ZERO = Decimal("0.00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _utcnow().date()


# --------------------------------------------------------------------------- #
# config bundle
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Config:
    methodology: ECLMethodology
    provision_pct_by_bucket: dict
    lifetime_loss_rate: Decimal
    sicr_dpd_threshold: int
    default_dpd_threshold: int
    dpd_buckets: list
    snapshot: dict


def _load_config(db: Session) -> _Config:
    c = ConfigService(db)
    raw_method = str(c.get(cfg.KEY_ECL_METHODOLOGY)).strip()
    try:
        methodology = ECLMethodology(raw_method)
    except ValueError:
        raise ValueError(
            f"ecl_methodology={raw_method!r} is not one of "
            f"{[m.value for m in ECLMethodology]}"
        )
    pct = c.get_json(cfg.KEY_ECL_PROVISION_PCT_BY_BUCKET)
    lifetime = Decimal(str(c.get_float(cfg.KEY_ECL_LIFETIME_LOSS_RATE)))
    sicr = c.get_int(cfg.KEY_ECL_SICR_DPD_THRESHOLD)
    default_dpd = c.get_int(cfg.KEY_ECL_DEFAULT_DPD_THRESHOLD)
    buckets = c.get_json(cfg.KEY_DPD_REPORT_BUCKETS)
    snapshot = {
        cfg.KEY_ECL_METHODOLOGY: methodology.value,
        cfg.KEY_ECL_PROVISION_PCT_BY_BUCKET: pct,
        cfg.KEY_ECL_LIFETIME_LOSS_RATE: float(lifetime),
        cfg.KEY_ECL_SICR_DPD_THRESHOLD: sicr,
        cfg.KEY_ECL_DEFAULT_DPD_THRESHOLD: default_dpd,
        cfg.KEY_DPD_REPORT_BUCKETS: buckets,
    }
    return _Config(
        methodology=methodology,
        provision_pct_by_bucket=pct,
        lifetime_loss_rate=lifetime,
        sicr_dpd_threshold=sicr,
        default_dpd_threshold=default_dpd,
        dpd_buckets=buckets,
        snapshot=snapshot,
    )


# --------------------------------------------------------------------------- #
# per-contract inputs
# --------------------------------------------------------------------------- #
def _ead(contract: InstallmentContract) -> Decimal:
    rec = build_receivable(contract)
    return rec.outstanding_receivable + rec.outstanding_late_fees


def _dpd(contract: InstallmentContract, as_of: date) -> int:
    worst = 0
    for inst in contract.installments:
        if inst.is_fully_paid:
            continue
        if inst.due_date < as_of:
            worst = max(worst, (as_of - inst.due_date).days)
    return worst


def _bucket_label(dpd: int, buckets: list) -> str:
    if dpd <= 0:
        return "current"
    for lo, hi in buckets:
        if dpd >= lo and (hi is None or dpd <= hi):
            return f"{lo}-{hi}" if hi is not None else f"{lo}+"
    # Past the last bounded band with no open-ended catch-all — fall back to the
    # widest label rather than silently dropping the exposure.
    lo, hi = buckets[-1]
    return f"{lo}+" if hi is None else f"{lo}-{hi}"


def _signals(db: Session, contract_id: int) -> tuple[bool, bool]:
    has_open_case = (
        db.execute(
            select(CollectionCase.id).where(
                CollectionCase.contract_id == contract_id,
                CollectionCase.status == CollectionCaseStatus.open,
            )
        ).first()
        is not None
    )
    has_broken_ptp = (
        db.execute(
            select(CollectionActivity.id)
            .join(
                CollectionCase,
                CollectionActivity.collection_case_id == CollectionCase.id,
            )
            .where(
                CollectionCase.contract_id == contract_id,
                CollectionActivity.activity_type
                == CollectionActivityType.promise_to_pay,
                CollectionActivity.promise_status == PromiseStatus.broken,
            )
        ).first()
        is not None
    )
    return has_open_case, has_broken_ptp


def _customer_id_for(db: Session, contract: InstallmentContract) -> int | None:
    so = db.get(SalesOrder, contract.sales_order_id)
    if so is None:
        return None
    app = db.get(CreditApplication, so.application_id)
    return app.customer_id if app else None


# --------------------------------------------------------------------------- #
# assessment
# --------------------------------------------------------------------------- #
def _prior_period_ecl(
    db: Session, contract_id: int, as_of: date
) -> Decimal | None:
    """The ECL from this contract's most recent assessment in an *earlier*
    period — the baseline the provision movement is measured against."""
    row = db.execute(
        select(ECLAssessment)
        .where(
            ECLAssessment.contract_id == contract_id,
            ECLAssessment.as_of_date < as_of,
        )
        .order_by(ECLAssessment.as_of_date.desc(), ECLAssessment.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return row.ecl_amount


def _assess_one(
    db: Session,
    contract: InstallmentContract,
    *,
    as_of: date,
    config: _Config,
    run: ECLRun | None,
) -> ECLAssessment:
    ead = _ead(contract)
    dpd = _dpd(contract, as_of)
    bucket = _bucket_label(dpd, config.dpd_buckets)
    open_case, broken_ptp = _signals(db, contract.id)

    ctx = ecl_engine.EclContext(
        contract_id=contract.id,
        as_of=as_of,
        ead=ead,
        dpd=dpd,
        dpd_bucket=bucket,
        has_open_collections_case=open_case,
        has_broken_promise_to_pay=broken_ptp,
        provision_pct_by_bucket=config.provision_pct_by_bucket,
        lifetime_loss_rate=config.lifetime_loss_rate,
        sicr_dpd_threshold=config.sicr_dpd_threshold,
        default_dpd_threshold=config.default_dpd_threshold,
    )
    outcome = ecl_engine.get_provider(config.methodology).assess(ctx)

    before = _prior_period_ecl(db, contract.id, as_of)
    movement: Decimal | None
    if outcome.ecl_amount is None:
        movement = None
    else:
        movement = outcome.ecl_amount - (before or _ZERO)

    snapshot = {**config.snapshot, "engine_inputs": outcome.inputs}
    if outcome.unpopulated_note:
        snapshot["unpopulated_note"] = outcome.unpopulated_note

    existing = db.execute(
        select(ECLAssessment).where(
            ECLAssessment.contract_id == contract.id,
            ECLAssessment.as_of_date == as_of,
        )
    ).scalar_one_or_none()

    row = existing or ECLAssessment(contract_id=contract.id, as_of_date=as_of)
    row.run_id = run.id if run is not None else row.run_id
    row.customer_id = _customer_id_for(db, contract)
    row.methodology = outcome.methodology
    row.ead = ead
    row.dpd = dpd
    row.dpd_bucket = outcome.dpd_bucket
    row.stage = outcome.stage
    row.stage_reason = outcome.stage_reason
    row.pd = outcome.pd
    row.lgd = outcome.lgd
    row.loss_rate = outcome.loss_rate
    row.ecl_amount = outcome.ecl_amount
    row.provision_before = before
    row.provision_movement = movement
    row.config_snapshot = snapshot
    if existing is None:
        db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
def initial_assessment(
    db: Session, contract: InstallmentContract, *, actor_id: int | None = None
) -> ECLAssessment | None:
    """The "day one" ECL assessment, created at contract activation. Additive —
    never blocks activation; a config problem is swallowed with the contract
    still activated (the portfolio run will surface it loudly later)."""
    try:
        config = _load_config(db)
        return _assess_one(db, contract, as_of=_today(), config=config, run=None)
    except Exception:  # pragma: no cover - defensive, mirrors accounting.emit
        return None


@dataclass
class EclRunSummary:
    run_id: int
    as_of_date: str
    methodology: str
    contracts_assessed: int
    total_ead: float
    total_ecl: float | None
    total_provision_movement: float | None
    accounting_event_id: int | None
    pd_lgd_note: str | None


def run_ecl(
    db: Session, *, as_of: date | None = None, actor_id: int | None = None
) -> EclRunSummary:
    """On-demand portfolio recalculation — the only trigger for now (a real
    schedule is separate infrastructure). Same pattern as
    ``POST /jobs/assess-overdue``."""
    config = _load_config(db)
    as_of = as_of or _today()

    # The provision movement POSTED to accounting is measured at the portfolio
    # level against the previous run's total ECL — not the sum of per-contract
    # period-over-period deltas. That way a same-day re-run posts ~zero instead
    # of double-counting, and contracts activated / closed between runs are
    # captured automatically.
    prior_run = db.execute(
        select(ECLRun).order_by(ECLRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    prior_total: Decimal | None = (
        Decimal(str(prior_run.total_ecl))
        if prior_run is not None and prior_run.total_ecl is not None
        else (_ZERO if prior_run is None else None)
    )

    run = ECLRun(
        as_of_date=as_of,
        methodology=config.methodology,
        created_by=actor_id,
    )
    db.add(run)
    db.flush()

    contracts = list(
        db.execute(
            select(InstallmentContract).where(
                InstallmentContract.status == ContractStatus.active
            )
        ).scalars()
    )

    total_ead = _ZERO
    total_ecl: Decimal | None = _ZERO
    for contract in contracts:
        row = _assess_one(db, contract, as_of=as_of, config=config, run=run)
        total_ead += row.ead
        if row.ecl_amount is None:
            total_ecl = None
        elif total_ecl is not None:
            total_ecl += row.ecl_amount

    if total_ecl is None or prior_total is None:
        total_move: Decimal | None = None
    else:
        total_move = total_ecl - prior_total

    run.contracts_assessed = len(contracts)
    run.total_ead = total_ead
    run.total_ecl = total_ecl
    run.total_provision_movement = total_move

    event_id: int | None = None
    # One accounting event per run — only when there is a real, computed
    # provision movement to post. Under Path B (no PD/LGD) there is no figure,
    # so no event is emitted rather than posting a fabricated zero.
    if total_move is not None:
        event = accounting.emit_unscoped(
            db,
            event_type=AccountingEventType.ecl_provision_movement,
            event_reference=f"ecl-provision-movement-run-{run.id}",
            amount=total_move,
            event_date=_utcnow(),
        )
        run.accounting_event_id = event.id
        event_id = event.id

    db.flush()
    note = (
        ecl_engine.NO_PD_LGD_SOURCE
        if config.methodology == ECLMethodology.three_stage
        else None
    )
    return EclRunSummary(
        run_id=run.id,
        as_of_date=as_of.isoformat(),
        methodology=config.methodology.value,
        contracts_assessed=run.contracts_assessed,
        total_ead=float(total_ead),
        total_ecl=None if total_ecl is None else float(total_ecl),
        total_provision_movement=None if total_move is None else float(total_move),
        accounting_event_id=event_id,
        pd_lgd_note=note,
    )


# --------------------------------------------------------------------------- #
# read models for the API / screen
# --------------------------------------------------------------------------- #
def _latest_assessment_rows(db: Session) -> list[ECLAssessment]:
    """Most recent assessment per contract (max as_of_date, then max id)."""
    rows = db.execute(
        select(ECLAssessment).order_by(
            ECLAssessment.contract_id,
            ECLAssessment.as_of_date.desc(),
            ECLAssessment.id.desc(),
        )
    ).scalars()
    seen: dict[int, ECLAssessment] = {}
    for r in rows:
        seen.setdefault(r.contract_id, r)
    return list(seen.values())


def _f(value) -> float | None:
    return None if value is None else round(float(value), 2)


def _row_dict(db: Session, a: ECLAssessment) -> dict:
    contract = db.get(InstallmentContract, a.contract_id)
    customer = db.get(Customer, a.customer_id) if a.customer_id else None
    c = ConfigService(db)
    auto_min = c.get_int(cfg.KEY_RISK_AUTO_APPROVE_MIN)
    refer_min = c.get_int(cfg.KEY_RISK_REFER_MIN)
    band = risk_band_of(customer.risk_score if customer else None, auto_min, refer_min)
    na = ecl_engine.NO_PD_LGD_SOURCE
    is_three_stage = a.methodology == ECLMethodology.three_stage
    return {
        "contract_id": a.contract_id,
        "customer_id": a.customer_id,
        "customer_name": customer.name if customer else None,
        "product_id": (
            contract.sales_order.product_id if contract and contract.sales_order else None
        ),
        "contract_status": contract.status.value if contract else None,
        "risk_band": band,
        "assessment_date": a.as_of_date.isoformat(),
        "methodology": a.methodology.value,
        "ead": _f(a.ead),
        "dpd": a.dpd,
        "dpd_bucket": a.dpd_bucket,
        "stage": a.stage if is_three_stage else None,
        "stage_reason": a.stage_reason if is_three_stage else None,
        "pd": None if a.pd is None else float(a.pd),
        "lgd": None if a.lgd is None else float(a.lgd),
        "pd_lgd_note": na if (is_three_stage and a.pd is None) else None,
        "loss_rate": None if a.loss_rate is None else float(a.loss_rate),
        "ecl_amount": _f(a.ecl_amount),
        "ecl_note": na if (is_three_stage and a.ecl_amount is None) else None,
        "provision_before": _f(a.provision_before),
        "provision_movement": _f(a.provision_movement),
    }


PORTFOLIO_FIELDS = [
    "contract_id",
    "customer_name",
    "product_id",
    "contract_status",
    "risk_band",
    "assessment_date",
    "methodology",
    "ead",
    "dpd",
    "dpd_bucket",
    "stage",
    "pd",
    "lgd",
    "ecl_amount",
]


def portfolio(
    db: Session,
    *,
    assessment_date: date | None = None,
    product_id: int | None = None,
    risk_band: str | None = None,
    dpd_bucket: str | None = None,
    contract_status: str | None = None,
) -> dict:
    rows = [_row_dict(db, a) for a in _latest_assessment_rows(db)]
    if assessment_date is not None:
        want = assessment_date.isoformat()
        rows = [r for r in rows if r["assessment_date"] == want]
    if product_id is not None:
        rows = [r for r in rows if r["product_id"] == product_id]
    if risk_band is not None:
        rows = [r for r in rows if r["risk_band"] == risk_band]
    if dpd_bucket is not None:
        rows = [r for r in rows if r["dpd_bucket"] == dpd_bucket]
    if contract_status is not None:
        rows = [r for r in rows if r["contract_status"] == contract_status]
    rows.sort(key=lambda r: (-(r["dpd"] or 0), r["contract_id"]))

    total_ead = round(sum(r["ead"] or 0 for r in rows), 2)
    computed = [r["ecl_amount"] for r in rows if r["ecl_amount"] is not None]
    na_count = sum(1 for r in rows if r["ecl_amount"] is None)
    total_ecl = round(sum(computed), 2) if computed or na_count == 0 else None
    return {
        "columns": PORTFOLIO_FIELDS,
        "rows": rows,
        "totals": {
            "row_count": len(rows),
            "ead": total_ead,
            "ecl_amount": round(sum(computed), 2),
            "ecl_not_computable_count": na_count,
        },
        "total_ead": total_ead,
        "total_ecl": total_ecl,
        "ecl_not_computable_count": na_count,
    }


def _latest_run(db: Session) -> ECLRun | None:
    return db.execute(
        select(ECLRun).order_by(ECLRun.id.desc()).limit(1)
    ).scalar_one_or_none()


def dashboard(db: Session) -> dict:
    config = _load_config(db)
    rows = [_row_dict(db, a) for a in _latest_assessment_rows(db)]
    total_ead = round(sum(r["ead"] or 0 for r in rows), 2)
    computed = [r["ecl_amount"] for r in rows if r["ecl_amount"] is not None]
    na_count = sum(1 for r in rows if r["ecl_amount"] is None)
    ecl_balance = round(sum(computed), 2)
    ecl_balance_display: float | str = ecl_balance if na_count == 0 else ecl_balance
    coverage = (
        round(ecl_balance / total_ead, 4) if total_ead and na_count == 0 else None
    )

    three_stage = config.methodology == ECLMethodology.three_stage
    stage_exposure: dict | str
    if three_stage:
        stage_exposure = {"1": 0.0, "2": 0.0, "3": 0.0, "unstaged": 0.0}
        for r in rows:
            key = str(r["stage"]) if r["stage"] in (1, 2, 3) else "unstaged"
            stage_exposure[key] = round(stage_exposure[key] + (r["ead"] or 0), 2)
    else:
        stage_exposure = "n/a"  # staging only applies under the three_stage path

    run = _latest_run(db)
    return {
        "active_methodology": config.methodology.value,
        "methodology_note": _METHODOLOGY_NOTES[config.methodology],
        "total_ead": total_ead,
        "ecl_balance": ecl_balance_display,
        "provision_balance": ecl_balance_display,  # provision == ECL in this slice
        "ecl_coverage_pct": coverage,
        "ecl_not_computable_count": na_count,
        "pd_lgd_note": ecl_engine.NO_PD_LGD_SOURCE if three_stage else None,
        "contracts_assessed": len(rows),
        "stage_exposure": stage_exposure,
        "last_run": _run_dict(run) if run else None,
    }


_METHODOLOGY_NOTES = {
    ECLMethodology.dpd_banded: (
        "Path C — provision % per DPD band. Fully computed. Not a formal IFRS 9 "
        "3-stage model; a pragmatic first implementation pending Finance/Risk sign-off."
    ),
    ECLMethodology.simplified_lifetime: (
        "Path A — lifetime ECL = EAD × a single configured lifetime loss rate, "
        "from day one, no staging. Structurally complete; the loss rate is a placeholder."
    ),
    ECLMethodology.three_stage: (
        "Path B — IFRS 9 general 3-stage model. Stage classification is live; PD, "
        "LGD and the ECL amount are 'n/a — no PD/LGD source configured' and are "
        "NOT fabricated. Pending a real PD/LGD source and Finance/Risk sign-off."
    ),
}


def _run_dict(run: ECLRun) -> dict:
    return {
        "run_id": run.id,
        "as_of_date": run.as_of_date.isoformat(),
        "methodology": run.methodology.value,
        "contracts_assessed": run.contracts_assessed,
        "total_ead": _f(run.total_ead),
        "total_ecl": _f(run.total_ecl),
        "total_provision_movement": _f(run.total_provision_movement),
        "accounting_event_id": run.accounting_event_id,
        "created_at": run.created_at.isoformat(),
    }


def list_runs(db: Session, *, limit: int = 50) -> list[dict]:
    runs = db.execute(
        select(ECLRun).order_by(ECLRun.id.desc()).limit(limit)
    ).scalars()
    return [_run_dict(r) for r in runs]


def contract_detail(db: Session, contract_id: int) -> dict | None:
    a = db.execute(
        select(ECLAssessment)
        .where(ECLAssessment.contract_id == contract_id)
        .order_by(ECLAssessment.as_of_date.desc(), ECLAssessment.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if a is None:
        return None
    history = db.execute(
        select(ECLAssessment)
        .where(ECLAssessment.contract_id == contract_id)
        .order_by(ECLAssessment.as_of_date.desc(), ECLAssessment.id.desc())
    ).scalars()
    row = _row_dict(db, a)
    row["config_snapshot"] = a.config_snapshot
    row["history"] = [
        {
            "assessment_date": h.as_of_date.isoformat(),
            "methodology": h.methodology.value,
            "ead": _f(h.ead),
            "dpd": h.dpd,
            "dpd_bucket": h.dpd_bucket,
            "stage": h.stage,
            "ecl_amount": _f(h.ecl_amount),
            "provision_movement": _f(h.provision_movement),
            "run_id": h.run_id,
        }
        for h in history
    ]
    return row
