"""ECL & Provision orchestration.

Per contract, per run:
  1. gather inputs — EAD, DPD, risk rating → segment → PD/LGD, origination PD,
     qualitative signals;
  2. run the configurable Stage Determination Engine (Stage-3 first, then SICR);
  3. apply stage curing (anti-flip-flop) — a downgrade needs the configured
     cure conditions;
  4. compute the AUTOMATED ECL (methodology-dependent);
  5. apply any ACTIVE manual override on top → FINAL stage / PD / LGD / ECL —
     the automated result is never mutated;
  6. compute the movement-based provision (opening / calculated / adjustment /
     closing);
  7. persist an immutable ``ECLAssessment`` stamped with every model/config
     version.

A run has a status lifecycle (DRAFT → COMPLETED → POSTED). POSTing emits the
accounting events and locks the provisions. Runs are never overwritten.

ECL never feeds back into customer pricing — it reads the receivable and DPD
only, and writes nothing to the offer / contract price.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounting import AccountingEvent, AccountingEventType
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
from app.models.ecl import (
    ECLAssessment,
    ECLConfiguration,
    ECLMethodology,
    ECLMovementType,
    ECLOverride,
    ECLOverrideStatus,
    ECLRun,
    ECLRunStatus,
)
from app.models.payment import Payment
from app.models.sales_order import SalesOrder
from app.services import accounting
from app.services import config_service as cfg
from app.services import ecl_config, ecl_engine, ecl_risk, ecl_stage
from app.services.config_service import ConfigService
from app.services.receivable import build_receivable

_ZERO = Decimal("0.00")
_CENTS = Decimal("0.01")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _utcnow().date()


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _f(v) -> float | None:
    return None if v is None else round(float(v), 2)


def _rate(v) -> float | None:
    return None if v is None else round(float(v), 6)


# --------------------------------------------------------------------------- #
# per-contract inputs
# --------------------------------------------------------------------------- #
def _ead(contract: InstallmentContract) -> Decimal:
    rec = build_receivable(contract)
    return _money(rec.outstanding_receivable + rec.outstanding_late_fees)


def _dpd(contract: InstallmentContract, as_of: date) -> int:
    worst = 0
    for inst in contract.installments:
        if inst.is_fully_paid:
            continue
        if inst.due_date < as_of:
            worst = max(worst, (as_of - inst.due_date).days)
    return worst


def _customer(db: Session, contract: InstallmentContract) -> Customer | None:
    so = db.get(SalesOrder, contract.sales_order_id)
    if so is None:
        return None
    app = db.get(CreditApplication, so.application_id)
    if app is None:
        return None
    return db.get(Customer, app.customer_id)


def _signals(db: Session, contract_id: int) -> ecl_stage.StageSignals:
    open_case = (
        db.execute(
            select(CollectionCase.id).where(
                CollectionCase.contract_id == contract_id,
                CollectionCase.status == CollectionCaseStatus.open,
            )
        ).first()
        is not None
    )
    broken_ptp = (
        db.execute(
            select(CollectionActivity.id)
            .join(CollectionCase, CollectionActivity.collection_case_id == CollectionCase.id)
            .where(
                CollectionCase.contract_id == contract_id,
                CollectionActivity.activity_type == CollectionActivityType.promise_to_pay,
                CollectionActivity.promise_status == PromiseStatus.broken,
            )
        ).first()
        is not None
    )
    # No restructuring / credit-impaired / UTP flag exists in the platform yet —
    # the configured rules for them stay dormant (BDR-11, BDR-48).
    return ecl_stage.StageSignals(
        open_collections_case=open_case,
        broken_promise_to_pay=broken_ptp,
        forbearance_flag=False,
        credit_impaired_flag=False,
        unlikely_to_pay_flag=False,
    )


def _origination(db: Session, contract_id: int) -> tuple[str | None, Decimal | None]:
    """The contract's earliest assessment carries the origination rating/PD."""
    row = db.execute(
        select(ECLAssessment)
        .where(ECLAssessment.contract_id == contract_id)
        .order_by(ECLAssessment.as_of_date.asc(), ECLAssessment.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None, None
    orig_rating = row.origination_rating or row.risk_rating
    orig_pd = row.origination_pd_12m if row.origination_pd_12m is not None else row.automated_pd_12m
    return orig_rating, orig_pd


def _prior_assessment(
    db: Session, contract_id: int, before_as_of: date, exclude_run_id: int | None
) -> ECLAssessment | None:
    stmt = (
        select(ECLAssessment)
        .where(
            ECLAssessment.contract_id == contract_id,
            ECLAssessment.as_of_date <= before_as_of,
        )
        .order_by(ECLAssessment.as_of_date.desc(), ECLAssessment.id.desc())
    )
    for row in db.execute(stmt).scalars():
        if exclude_run_id is not None and row.run_id == exclude_run_id:
            continue
        return row
    return None


def _peak_stage_history(db: Session, contract_id: int) -> tuple[int | None, date | None]:
    """Highest automated stage ever assigned + the earliest date it was hit."""
    rows = db.execute(
        select(ECLAssessment.automated_stage, ECLAssessment.as_of_date)
        .where(
            ECLAssessment.contract_id == contract_id,
            ECLAssessment.automated_stage.is_not(None),
        )
    ).all()
    if not rows:
        return None, None
    peak = max(r[0] for r in rows)
    first_seen = min(r[1] for r in rows if r[0] == peak)
    return peak, first_seen


def _payments_since(db: Session, contract_id: int, since: date) -> int:
    return db.execute(
        select(func.count(Payment.id)).where(
            Payment.contract_id == contract_id,
            func.date(Payment.received_at) > since,
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
# stage curing (anti-flip-flop)
# --------------------------------------------------------------------------- #
def _apply_curing(
    db: Session,
    contract_id: int,
    raw_stage: int,
    dpd: int,
    as_of: date,
    cfg_row: ECLConfiguration,
) -> tuple[int, str | None]:
    """A downgrade (raw_stage below the contract's peak automated stage) is only
    allowed when the configured cure conditions hold; otherwise the higher
    stage is held with an explanatory note."""
    rules = cfg_row.cure_rules or {}
    if not rules.get("enabled", True):
        return raw_stage, None

    peak, first_seen = _peak_stage_history(db, contract_id)
    if peak is None or raw_stage >= peak:
        return raw_stage, None  # escalation or no history — nothing to cure

    # want to go down from `peak` toward `raw_stage`; check the transition rule
    key = "stage_3_to_2" if peak == 3 else "stage_2_to_1"
    rule = rules.get(key, {})
    need_payments = int(rule.get("min_qualifying_payments", 0))
    max_dpd = int(rule.get("max_dpd", 0))
    need_days = int(rule.get("min_observation_days", 0))

    payments = _payments_since(db, contract_id, first_seen) if first_seen else 0
    obs_days = (as_of - first_seen).days if first_seen else 0

    ok = dpd <= max_dpd and payments >= need_payments and obs_days >= need_days
    if ok:
        return raw_stage, (
            f"Cure complete: DPD {dpd} ≤ {max_dpd}, {payments} qualifying "
            f"payment(s) ≥ {need_payments}, {obs_days} day(s) observed ≥ {need_days}. "
            f"Downgraded {peak} → {raw_stage}."
        )
    # hold the peak stage (or one notch down if the raw stage is only one below)
    held = max(raw_stage, peak)
    return held, (
        f"Cure not yet complete (holding Stage {held}): DPD {dpd} vs ≤{max_dpd}, "
        f"{payments}/{need_payments} qualifying payments, {obs_days}/{need_days} "
        f"observation days."
    )


# --------------------------------------------------------------------------- #
# one assessment
# --------------------------------------------------------------------------- #
@dataclass
class _Assessed:
    row: ECLAssessment
    movement: Decimal


def _load_cfg(db: Session) -> ECLConfiguration:
    return ecl_config.get_active(db)


def _dpd_buckets(db: Session) -> list:
    return ConfigService(db).get_json(cfg.KEY_DPD_REPORT_BUCKETS)


def _active_override(db: Session, contract_id: int, as_of: date):
    rows = db.execute(
        select(ECLOverride).where(
            ECLOverride.contract_id == contract_id,
            ECLOverride.status.in_(
                [ECLOverrideStatus.active, ECLOverrideStatus.approved]
            ),
        ).order_by(ECLOverride.id.desc())
    ).scalars().all()
    for ov in rows:
        if ov.effective_from <= as_of and (ov.effective_to is None or as_of <= ov.effective_to):
            return ov
    return None


def _assess_one(
    db: Session,
    contract: InstallmentContract,
    *,
    as_of: date,
    run: ECLRun | None,
    cfg_row: ECLConfiguration,
    buckets: list,
) -> _Assessed:
    methodology = cfg_row.methodology
    customer = _customer(db, contract)
    score = customer.risk_score if customer else None
    ead = _ead(contract)
    dpd = _dpd(contract, as_of)

    rating = ecl_risk.resolve_rating(score, cfg_row)
    segment = ecl_risk.resolve_segment(rating, cfg_row)
    pd_set = ecl_risk.resolve_pd(segment, cfg_row)
    lgd_set = ecl_risk.resolve_lgd(segment, cfg_row)
    orig_rating, orig_pd = _origination(db, contract.id)
    if orig_rating is None:  # this IS the origination assessment
        orig_rating, orig_pd = rating, pd_set.pd_12m

    snapshot = ecl_config.as_snapshot(cfg_row)

    # --- stage (three_stage only) + curing --------------------------------
    if methodology == ECLMethodology.three_stage:
        signals = _signals(db, contract.id)
        stage_ctx = ecl_stage.StageContext(
            dpd=dpd,
            current_pd_12m=pd_set.pd_12m,
            origination_pd_12m=orig_pd,
            current_rating=rating,
            origination_rating=orig_rating,
            signals=signals,
        )
        raw = ecl_stage.determine_stage(stage_ctx, cfg_row)
        automated_stage, cure_note = _apply_curing(
            db, contract.id, raw.stage, dpd, as_of, cfg_row
        )
        triggers = raw.all_triggers
        stage_reason = raw.reason if automated_stage == raw.stage else (
            f"{raw.reason}  |  {cure_note}"
        )
        a_pd_12m = pd_set.pd_12m
        a_pd_life = pd_set.pd_lifetime_stage_3 if automated_stage == 3 else pd_set.pd_lifetime
        a_lgd = lgd_set.lgd_stage_3 if automated_stage == 3 else lgd_set.lgd
        automated_ecl, eff_pd, _formula = ecl_engine.calc_three_stage(
            ead=ead, stage=automated_stage, pd_12m=a_pd_12m, pd_lifetime=a_pd_life, lgd=a_lgd
        )
        dpd_bucket = None
        loss_rate = eff_pd * a_lgd
    elif methodology == ECLMethodology.simplified_lifetime:
        automated_stage, cure_note, triggers, stage_reason = None, None, [], None
        a_pd_12m = a_pd_life = a_lgd = None
        automated_ecl, _formula = ecl_engine.calc_simplified_lifetime(
            ead=ead, lifetime_loss_rate=cfg_row.lifetime_loss_rate
        )
        dpd_bucket = None
        loss_rate = Decimal(str(cfg_row.lifetime_loss_rate))
    else:  # dpd_banded
        automated_stage, cure_note, triggers, stage_reason = None, None, [], None
        a_pd_12m = a_pd_life = a_lgd = None
        dpd_bucket = ecl_engine.dpd_band_label(dpd, buckets)
        band_pct = (cfg_row.dpd_provision_pct or {}).get(dpd_bucket)
        if band_pct is None:
            raise ValueError(
                f"ECL config has no dpd_provision_pct entry for band {dpd_bucket!r}"
            )
        automated_ecl, _formula = ecl_engine.calc_dpd_banded(ead=ead, band_pct=band_pct)
        loss_rate = Decimal(str(band_pct))

    # --- override -----------------------------------------------------
    ov = _active_override(db, contract.id, as_of)
    override_stage = override_pd_12m = override_pd_life = override_lgd = None
    if ov is not None:
        ap = ov.approved_value or {}
        override_stage = ap.get("stage")
        override_pd_12m = _dec(ap.get("pd_12m"))
        override_pd_life = _dec(ap.get("pd_lifetime"))
        override_lgd = _dec(ap.get("lgd"))

    final_stage = override_stage if override_stage is not None else automated_stage
    final_pd_12m = override_pd_12m if override_pd_12m is not None else a_pd_12m
    final_lgd = override_lgd if override_lgd is not None else a_lgd
    final_ead = ead
    if ov is not None and (ov.approved_value or {}).get("ead") is not None:
        final_ead = _dec((ov.approved_value or {}).get("ead"))

    if methodology == ECLMethodology.three_stage:
        # recompute the final ECL with the final params
        base_life = pd_set.pd_lifetime_stage_3 if final_stage == 3 else pd_set.pd_lifetime
        final_pd_life = override_pd_life if override_pd_life is not None else base_life
        final_ecl, _eff, _fmt = ecl_engine.calc_three_stage(
            ead=final_ead,
            stage=final_stage or 1,
            pd_12m=final_pd_12m,
            pd_lifetime=final_pd_life,
            lgd=final_lgd,
        )
    else:
        final_pd_life = None
        final_ecl = automated_ecl  # no parameter override for the non-staged paths

    # --- provision movement ----------------------------------------
    prior = _prior_assessment(
        db, contract.id, as_of, exclude_run_id=run.id if run else None
    )
    opening = _money(prior.closing_provision) if prior and prior.closing_provision is not None else _ZERO
    calculated = _money(automated_ecl)
    override_adj = _money(final_ecl) - calculated
    closing = _money(final_ecl)
    movement = closing - opening
    if override_adj != _ZERO:
        movement_type = ECLMovementType.override_adjustment
    elif opening == _ZERO and closing > _ZERO:
        movement_type = ECLMovementType.created
    elif closing > opening:
        movement_type = ECLMovementType.increased
    elif closing < opening:
        movement_type = ECLMovementType.released
    else:
        movement_type = ECLMovementType.unchanged

    existing = None
    if run is not None:
        existing = db.execute(
            select(ECLAssessment).where(
                ECLAssessment.run_id == run.id,
                ECLAssessment.contract_id == contract.id,
            )
        ).scalar_one_or_none()

    row = existing or ECLAssessment(contract_id=contract.id, as_of_date=as_of)
    row.run_id = run.id if run is not None else None
    row.customer_id = customer.id if customer else None
    row.as_of_date = as_of
    row.methodology = methodology
    row.ead = ead
    row.dpd = dpd
    row.dpd_bucket = dpd_bucket
    row.risk_rating = rating
    row.risk_segment = segment
    row.origination_rating = orig_rating
    row.origination_pd_12m = orig_pd
    row.automated_stage = automated_stage
    row.automated_pd_12m = a_pd_12m
    row.automated_pd_lifetime = a_pd_life
    row.automated_lgd = a_lgd
    row.automated_ecl = calculated
    row.stage_triggers = triggers
    row.stage_reason = stage_reason
    row.cure_note = cure_note
    row.override_id = ov.id if ov else None
    row.override_stage = override_stage
    row.override_pd_12m = override_pd_12m
    row.override_pd_lifetime = override_pd_life
    row.override_lgd = override_lgd
    row.final_stage = final_stage
    row.final_pd_12m = final_pd_12m
    row.final_pd_lifetime = final_pd_life
    row.final_lgd = final_lgd
    row.final_ead = final_ead
    row.final_ecl = closing
    row.opening_provision = opening
    row.calculated_ecl = calculated
    row.override_adjustment = override_adj
    row.closing_provision = closing
    row.movement_type = movement_type
    # legacy / compat
    row.stage = final_stage
    row.pd = final_pd_12m if final_stage == 1 else (final_pd_life or final_pd_12m)
    row.lgd = final_lgd
    row.loss_rate = loss_rate
    row.ecl_amount = closing
    row.provision_before = opening
    row.provision_movement = movement
    # versions
    row.ecl_config_version = cfg_row.version
    row.stage_rule_version = ecl_config.STAGE_RULE_VERSION
    row.pd_model_version = ecl_config.PD_MODEL_VERSION
    row.lgd_model_version = ecl_config.LGD_MODEL_VERSION
    row.calculation_version = ecl_engine.CALCULATION_VERSION
    row.config_snapshot = snapshot
    row.calculated_at = _utcnow()
    if existing is None:
        db.add(row)
    db.flush()
    return _Assessed(row=row, movement=movement)


def _dec(v):
    return None if v is None else Decimal(str(v))


# --------------------------------------------------------------------------- #
# day-one
# --------------------------------------------------------------------------- #
def initial_assessment(
    db: Session, contract: InstallmentContract, *, actor_id: int | None = None
) -> ECLAssessment | None:
    """Origination assessment at contract activation. Additive — never blocks
    activation; a config problem is logged, not raised."""
    try:
        existing = db.execute(
            select(ECLAssessment).where(
                ECLAssessment.contract_id == contract.id,
                ECLAssessment.run_id.is_(None),
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        cfg_row = _load_cfg(db)
        buckets = _dpd_buckets(db)
        assessed = _assess_one(
            db, contract, as_of=_today(), run=None, cfg_row=cfg_row, buckets=buckets
        )
        row = assessed.row
        opening = _money(row.opening_provision)
        closing = _money(row.closing_provision)
        if closing - opening != _ZERO:
            ev = accounting.emit(
                db,
                event_type=AccountingEventType.ecl_provision_created,
                event_reference=f"ecl-orig-{contract.id}",
                contract=contract,
                amount=closing - opening,
                event_date=_utcnow(),
            )
            row.accounting_event_id = ev.id
            db.flush()
        return row
    except ValueError:
        # a genuine config error — surfaced loudly by the next portfolio run
        return None
    except Exception:  # pragma: no cover - last-resort guard, never blocks delivery
        return None


# --------------------------------------------------------------------------- #
# portfolio run
# --------------------------------------------------------------------------- #
@dataclass
class EclRunSummary:
    run_id: int
    run_ref: str
    status: str
    as_of_date: str
    methodology: str
    ecl_config_version: int
    contracts_assessed: int
    contracts_by_stage: dict
    total_ead: float
    total_ecl: float
    total_ecl_by_stage: dict
    total_provision_movement: float
    accounting_event_id: int | None
    posted: bool


def _next_run_ref(db: Session, as_of: date) -> str:
    prefix = f"ECL-RUN-{as_of.strftime('%Y-%m')}"
    n = db.execute(
        select(func.count(ECLRun.id)).where(
            ECLRun.run_ref.is_not(None), ECLRun.run_ref.like(f"{prefix}-%")
        )
    ).scalar_one()
    return f"{prefix}-{n + 1:03d}"


def run_ecl(
    db: Session, *, as_of: date | None = None, actor_id: int | None = None
) -> EclRunSummary:
    """Compute an ECL run (status COMPLETED — provisions not yet posted).
    Never overwrites a prior run."""
    cfg_row = _load_cfg(db)
    buckets = _dpd_buckets(db)
    as_of = as_of or _today()

    run = ECLRun(
        run_ref=_next_run_ref(db, as_of),
        as_of_date=as_of,
        methodology=cfg_row.methodology,
        status=ECLRunStatus.calculating,
        kind="portfolio",
        ecl_config_version=cfg_row.version,
        stage_rule_version=ecl_config.STAGE_RULE_VERSION,
        pd_model_version=ecl_config.PD_MODEL_VERSION,
        lgd_model_version=ecl_config.LGD_MODEL_VERSION,
        calculation_version=ecl_engine.CALCULATION_VERSION,
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
    total_ecl = _ZERO
    total_movement = _ZERO
    by_stage_count = {"1": 0, "2": 0, "3": 0, "none": 0}
    by_stage_ecl = {"1": _ZERO, "2": _ZERO, "3": _ZERO, "none": _ZERO}
    try:
        for contract in contracts:
            a = _assess_one(db, contract, as_of=as_of, run=run, cfg_row=cfg_row, buckets=buckets)
            total_ead += _money(a.row.ead)
            total_ecl += _money(a.row.final_ecl)
            total_movement += _money(a.row.closing_provision) - _money(a.row.opening_provision)
            key = str(a.row.final_stage) if a.row.final_stage in (1, 2, 3) else "none"
            by_stage_count[key] += 1
            by_stage_ecl[key] += _money(a.row.final_ecl)
    except ValueError as exc:
        run.status = ECLRunStatus.failed
        run.error_message = str(exc)
        db.flush()
        raise

    run.status = ECLRunStatus.completed
    run.contracts_assessed = len(contracts)
    run.contracts_by_stage = by_stage_count
    run.total_ead = total_ead
    run.total_ecl = total_ecl
    run.total_ecl_stage_1 = by_stage_ecl["1"]
    run.total_ecl_stage_2 = by_stage_ecl["2"]
    run.total_ecl_stage_3 = by_stage_ecl["3"]
    run.total_provision_movement = total_movement
    db.flush()

    return _run_summary(run)


def post_run(db: Session, run: ECLRun, *, actor_id: int | None = None) -> EclRunSummary:
    """Finalise a COMPLETED run: emit the accounting events (one per contract
    movement + one portfolio roll-up) and lock the provisions."""
    if run.status == ECLRunStatus.posted:
        return _run_summary(run)
    if run.status != ECLRunStatus.completed:
        raise ValueError(f"Only a COMPLETED run can be posted (status: {run.status.value})")

    for a in run.assessments:
        contract = db.get(InstallmentContract, a.contract_id)
        if contract is None:
            continue
        opening = _money(a.opening_provision)
        calculated = _money(a.calculated_ecl)
        closing = _money(a.closing_provision)

        # (1) the model-driven movement: opening -> calculated ECL
        base_move = calculated - opening
        if base_move != _ZERO:
            if opening == _ZERO:
                etype = AccountingEventType.ecl_provision_created
            elif base_move > _ZERO:
                etype = AccountingEventType.ecl_provision_increased
            else:
                etype = AccountingEventType.ecl_provision_released
            ev = accounting.emit(
                db,
                event_type=etype,
                event_reference=f"ecl-{run.id}-{a.contract_id}",
                contract=contract,
                amount=base_move,
                event_date=_utcnow(),
            )
            a.accounting_event_id = ev.id

        # (2) the manual-override delta: calculated ECL -> closing provision
        override_adj = closing - calculated
        if override_adj != _ZERO:
            ev = accounting.emit(
                db,
                event_type=AccountingEventType.ecl_provision_override_adjustment,
                event_reference=f"ecl-ovr-{run.id}-{a.contract_id}",
                contract=contract,
                amount=override_adj,
                event_date=_utcnow(),
            )
            if a.accounting_event_id is None:
                a.accounting_event_id = ev.id

    roll_up = _money(run.total_provision_movement or _ZERO)
    ev = accounting.emit_unscoped(
        db,
        event_type=AccountingEventType.ecl_provision_movement,
        event_reference=f"ecl-provision-movement-run-{run.id}",
        amount=roll_up,
        event_date=_utcnow(),
    )
    run.accounting_event_id = ev.id
    run.status = ECLRunStatus.posted
    run.posted_at = _utcnow()
    run.posted_by = actor_id
    db.flush()
    return _run_summary(run)


def _run_summary(run: ECLRun) -> EclRunSummary:
    return EclRunSummary(
        run_id=run.id,
        run_ref=run.run_ref or "",
        status=run.status.value,
        as_of_date=run.as_of_date.isoformat(),
        methodology=run.methodology.value,
        ecl_config_version=run.ecl_config_version or 0,
        contracts_assessed=run.contracts_assessed,
        contracts_by_stage=run.contracts_by_stage or {},
        total_ead=float(run.total_ead or 0),
        total_ecl=float(run.total_ecl or 0),
        total_ecl_by_stage={
            "1": _f(run.total_ecl_stage_1) or 0.0,
            "2": _f(run.total_ecl_stage_2) or 0.0,
            "3": _f(run.total_ecl_stage_3) or 0.0,
        },
        total_provision_movement=float(run.total_provision_movement or 0),
        accounting_event_id=run.accounting_event_id,
        posted=run.status == ECLRunStatus.posted,
    )


# --------------------------------------------------------------------------- #
# read models
# --------------------------------------------------------------------------- #
def _latest_per_contract(db: Session) -> list[ECLAssessment]:
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


def _prev_for_contract(db: Session, a: ECLAssessment) -> ECLAssessment | None:
    return db.execute(
        select(ECLAssessment)
        .where(
            ECLAssessment.contract_id == a.contract_id,
            ECLAssessment.id != a.id,
            ECLAssessment.as_of_date <= a.as_of_date,
        )
        .order_by(ECLAssessment.as_of_date.desc(), ECLAssessment.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _row_dict(db: Session, a: ECLAssessment) -> dict:
    contract = db.get(InstallmentContract, a.contract_id)
    customer = db.get(Customer, a.customer_id) if a.customer_id else None
    prev = _prev_for_contract(db, a)
    has_override = a.override_id is not None
    return {
        "assessment_id": a.id,
        "run_id": a.run_id,
        "contract_id": a.contract_id,
        "customer_id": a.customer_id,
        "customer_name": customer.name if customer else None,
        "product_id": (
            contract.sales_order.product_id if contract and contract.sales_order else None
        ),
        "contract_status": contract.status.value if contract else None,
        "assessment_date": a.as_of_date.isoformat(),
        "methodology": a.methodology.value,
        "ecl_config_version": a.ecl_config_version,
        "ead": _f(a.ead),
        "dpd": a.dpd,
        "dpd_bucket": a.dpd_bucket,
        "risk_rating": a.risk_rating,
        "risk_segment": a.risk_segment,
        "origination_rating": a.origination_rating,
        "origination_pd_12m": _rate(a.origination_pd_12m),
        "automated_stage": a.automated_stage,
        "automated_pd_12m": _rate(a.automated_pd_12m),
        "automated_pd_lifetime": _rate(a.automated_pd_lifetime),
        "automated_lgd": _rate(a.automated_lgd),
        "automated_ecl": _f(a.automated_ecl),
        "override_stage": a.override_stage,
        "override_pd_12m": _rate(a.override_pd_12m),
        "override_pd_lifetime": _rate(a.override_pd_lifetime),
        "override_lgd": _rate(a.override_lgd),
        "override_status": "active" if has_override else None,
        "final_stage": a.final_stage,
        "final_pd_12m": _rate(a.final_pd_12m),
        "final_pd_lifetime": _rate(a.final_pd_lifetime),
        "final_lgd": _rate(a.final_lgd),
        "final_ead": _f(a.final_ead),
        "final_ecl": _f(a.final_ecl),
        "previous_ecl": _f(prev.final_ecl) if prev else None,
        "opening_provision": _f(a.opening_provision),
        "calculated_ecl": _f(a.calculated_ecl),
        "override_adjustment": _f(a.override_adjustment),
        "closing_provision": _f(a.closing_provision),
        "provision_movement": _f(a.provision_movement),
        "movement_type": a.movement_type.value if a.movement_type else None,
        "stage_reason": a.stage_reason,
        "cure_note": a.cure_note,
        # legacy aliases kept for the current frontend during the transition
        "stage": a.final_stage,
        "pd": _rate(a.pd),
        "lgd": _rate(a.lgd),
        "loss_rate": _rate(a.loss_rate),
        "ecl_amount": _f(a.final_ecl),
        "provision_before": _f(a.opening_provision),
    }


PORTFOLIO_FIELDS = [
    "contract_id", "customer_name", "ead", "dpd", "risk_rating", "risk_segment",
    "automated_stage", "override_stage", "final_stage",
    "automated_pd_12m", "final_pd_12m", "final_pd_lifetime", "final_lgd",
    "final_ead", "previous_ecl", "final_ecl", "provision_movement",
    "override_status", "assessment_date",
]

_DPD_BANDS = [("current", lambda d: d <= 0),
              ("1-30", lambda d: 1 <= d <= 30),
              ("31-60", lambda d: 31 <= d <= 60),
              ("61-90", lambda d: 61 <= d <= 90),
              ("91+", lambda d: d >= 91)]


def _dpd_band(dpd: int) -> str:
    for label, test in _DPD_BANDS:
        if test(dpd):
            return label
    return "current"


def portfolio(
    db: Session,
    *,
    stage: int | None = None,
    dpd_band: str | None = None,
    risk_rating: str | None = None,
    risk_segment: str | None = None,
    override_status: str | None = None,
    run_id: int | None = None,
    contract_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if run_id is not None:
        rows = db.execute(
            select(ECLAssessment)
            .where(ECLAssessment.run_id == run_id)
            .order_by(ECLAssessment.contract_id)
        ).scalars().all()
    else:
        rows = _latest_per_contract(db)
    dicts = [_row_dict(db, a) for a in rows]

    if stage is not None:
        dicts = [r for r in dicts if r["final_stage"] == stage]
    if dpd_band is not None:
        dicts = [r for r in dicts if _dpd_band(r["dpd"]) == dpd_band]
    if risk_rating is not None:
        dicts = [r for r in dicts if r["risk_rating"] == risk_rating]
    if risk_segment is not None:
        dicts = [r for r in dicts if r["risk_segment"] == risk_segment]
    if override_status is not None:
        want = None if override_status in ("none", "") else override_status
        dicts = [r for r in dicts if r["override_status"] == want]
    if contract_id is not None:
        dicts = [r for r in dicts if r["contract_id"] == contract_id]

    dicts.sort(key=lambda r: (-(r["dpd"] or 0), r["contract_id"]))
    total = len(dicts)
    page = dicts[offset : offset + limit]

    total_ead = round(sum(r["ead"] or 0 for r in dicts), 2)
    total_ecl = round(sum(r["final_ecl"] or 0 for r in dicts), 2)
    return {
        "columns": PORTFOLIO_FIELDS,
        "rows": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "totals": {
            "row_count": total,
            "final_ead": total_ead,
            "final_ecl": total_ecl,
        },
        "total_ead": total_ead,
        "total_ecl": total_ecl,
    }


def dashboard(db: Session) -> dict:
    cfg_row = _load_cfg(db)
    rows = [_row_dict(db, a) for a in _latest_per_contract(db)]
    total_ead = round(sum(r["ead"] or 0 for r in rows), 2)
    total_ecl = round(sum(r["final_ecl"] or 0 for r in rows), 2)
    total_movement = round(sum(r["provision_movement"] or 0 for r in rows), 2)
    coverage = round(total_ecl / total_ead, 4) if total_ead else None

    stage_ead = {"1": 0.0, "2": 0.0, "3": 0.0, "unstaged": 0.0}
    stage_ecl = {"1": 0.0, "2": 0.0, "3": 0.0, "unstaged": 0.0}
    for r in rows:
        key = str(r["final_stage"]) if r["final_stage"] in (1, 2, 3) else "unstaged"
        stage_ead[key] = round(stage_ead[key] + (r["ead"] or 0), 2)
        stage_ecl[key] = round(stage_ecl[key] + (r["final_ecl"] or 0), 2)

    default_exposure = round(sum(r["ead"] or 0 for r in rows if r["final_stage"] == 3), 2)
    overrides_active = sum(1 for r in rows if r["override_status"] == "active")

    overrides_pending = db.execute(
        select(func.count(ECLOverride.id)).where(
            ECLOverride.status == ECLOverrideStatus.pending
        )
    ).scalar_one()

    last_run = db.execute(
        select(ECLRun)
        .where(ECLRun.kind == "portfolio")
        .order_by(ECLRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    migration = _stage_migration(db)

    return {
        "active_methodology": cfg_row.methodology.value,
        "ecl_config_version": cfg_row.version,
        "total_exposure": total_ead,
        "total_ecl": total_ecl,
        "total_provision": total_ecl,   # provision == ECL in this engine
        "provision_movement": total_movement,
        "coverage_ratio": coverage,
        "contracts_assessed": len(rows),
        "stage_exposure": stage_ead,
        "stage_ecl": stage_ecl,
        "default_exposure": default_exposure,
        "overrides_pending": overrides_pending,
        "overrides_active": overrides_active,
        "stage_migration": migration,
        "last_run": _run_dict(last_run) if last_run else None,
    }


def _stage_migration(db: Session) -> dict:
    """Count contracts whose final stage moved between their two latest runs."""
    latest = _latest_per_contract(db)
    counts = {"upgraded": 0, "downgraded": 0, "unchanged": 0, "new": 0}
    for a in latest:
        prev = _prev_for_contract(db, a)
        if prev is None or prev.final_stage is None or a.final_stage is None:
            counts["new"] += 1
        elif a.final_stage > prev.final_stage:
            counts["upgraded"] += 1
        elif a.final_stage < prev.final_stage:
            counts["downgraded"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def _run_dict(run: ECLRun) -> dict:
    return {
        "run_id": run.id,
        "run_ref": run.run_ref,
        "status": run.status.value,
        "as_of_date": run.as_of_date.isoformat(),
        "methodology": run.methodology.value,
        "ecl_config_version": run.ecl_config_version,
        "contracts_assessed": run.contracts_assessed,
        "contracts_by_stage": run.contracts_by_stage,
        "total_ead": _f(run.total_ead),
        "total_ecl": _f(run.total_ecl),
        "total_ecl_stage_1": _f(run.total_ecl_stage_1),
        "total_ecl_stage_2": _f(run.total_ecl_stage_2),
        "total_ecl_stage_3": _f(run.total_ecl_stage_3),
        "total_provision_movement": _f(run.total_provision_movement),
        "accounting_event_id": run.accounting_event_id,
        "posted_at": run.posted_at.isoformat() if run.posted_at else None,
        "created_at": run.created_at.isoformat(),
    }


def list_runs(db: Session, *, limit: int = 50) -> list[dict]:
    runs = db.execute(
        select(ECLRun)
        .where(ECLRun.kind == "portfolio")
        .order_by(ECLRun.id.desc())
        .limit(limit)
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
    ).scalars().all()

    overrides = db.execute(
        select(ECLOverride)
        .where(ECLOverride.contract_id == contract_id)
        .order_by(ECLOverride.id.desc())
    ).scalars().all()

    acct_events = db.execute(
        select(AccountingEvent)
        .where(
            AccountingEvent.contract_id == contract_id,
            AccountingEvent.event_type.in_([
                AccountingEventType.ecl_provision_created,
                AccountingEventType.ecl_provision_increased,
                AccountingEventType.ecl_provision_released,
                AccountingEventType.ecl_provision_override_adjustment,
            ]),
        )
        .order_by(AccountingEvent.id.desc())
    ).scalars().all()

    row = _row_dict(db, a)
    row["stage_triggers"] = a.stage_triggers
    row["config_snapshot"] = a.config_snapshot
    row["versions"] = {
        "ecl_config_version": a.ecl_config_version,
        "stage_rule_version": a.stage_rule_version,
        "pd_model_version": a.pd_model_version,
        "lgd_model_version": a.lgd_model_version,
        "calculation_version": a.calculation_version,
    }
    row["history"] = [
        {
            "assessment_date": h.as_of_date.isoformat(),
            "run_id": h.run_id,
            "methodology": h.methodology.value,
            "ead": _f(h.ead),
            "dpd": h.dpd,
            "automated_stage": h.automated_stage,
            "final_stage": h.final_stage,
            "automated_ecl": _f(h.automated_ecl),
            "final_ecl": _f(h.final_ecl),
            "opening_provision": _f(h.opening_provision),
            "closing_provision": _f(h.closing_provision),
            "provision_movement": _f(h.provision_movement),
            "movement_type": h.movement_type.value if h.movement_type else None,
            "override_id": h.override_id,
        }
        for h in history
    ]
    row["stage_migration_history"] = [
        {"date": h.as_of_date.isoformat(), "automated": h.automated_stage, "final": h.final_stage}
        for h in reversed(history)
    ]
    row["overrides"] = [_override_dict(o) for o in overrides]
    row["accounting_events"] = [
        {
            "id": e.id,
            "event_type": e.event_type.value,
            "amount": _f(e.amount),
            "status": e.accounting_status.value,
            "event_date": e.event_date.isoformat(),
            "external_gl_reference": e.external_gl_reference,
        }
        for e in acct_events
    ]
    return row


def _override_dict(o) -> dict:
    return {
        "id": o.id,
        "override_type": o.override_type.value,
        "status": o.status.value,
        "reason_code": o.reason_code.value,
        "justification": o.justification,
        "evidence_ref": o.evidence_ref,
        "comments": o.comments,
        "automated_value": o.automated_value,
        "approved_value": o.approved_value,
        "ecl_before": _f(o.ecl_before),
        "ecl_after": _f(o.ecl_after),
        "financial_impact": _f(o.financial_impact),
        "effective_from": o.effective_from.isoformat(),
        "effective_to": o.effective_to.isoformat() if o.effective_to else None,
        "review_date": o.review_date.isoformat() if o.review_date else None,
        "approval_request_id": o.approval_request_id,
        "requested_by": o.requested_by,
        "approved_by": o.approved_by,
        "created_at": o.created_at.isoformat(),
    }


def manual_reassessment(db: Session, contract_id: int) -> dict | None:
    """The full automated assessment package for the override screen."""
    detail = contract_detail(db, contract_id)
    if detail is None:
        return None
    cfg_row = _load_cfg(db)
    detail["override_rules"] = cfg_row.override_rules
    return detail
