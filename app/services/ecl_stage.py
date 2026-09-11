"""Configurable Stage Determination Engine (three-stage methodology).

    evaluate STAGE-3 triggers  →  if any fire → Stage 3
    else evaluate STAGE-2 / SICR triggers  →  if any fire → Stage 2
    else  →  Stage 1

DPD is *one* trigger among several. Thresholds and the whole rule list come from
``ECLConfiguration`` (``stage3_rules`` / ``sicr_rules``) — nothing here is a
hard-coded accounting rule. The result carries a per-rule breakdown so a user
can always answer "why is this customer Stage 2/3?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.models.ecl import ECLConfiguration
from app.services import ecl_risk


@dataclass(frozen=True)
class StageSignals:
    """Boolean risk indicators pulled from elsewhere in the platform."""

    open_collections_case: bool = False
    broken_promise_to_pay: bool = False
    forbearance_flag: bool = False          # no restructuring module yet → always False
    credit_impaired_flag: bool = False      # no impaired flag yet → always False
    unlikely_to_pay_flag: bool = False


@dataclass(frozen=True)
class StageContext:
    dpd: int
    current_pd_12m: Decimal | None
    origination_pd_12m: Decimal | None
    current_rating: str | None
    origination_rating: str | None
    signals: StageSignals


@dataclass
class TriggerResult:
    id: str
    name: str
    category: str
    triggered: bool
    detail: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "triggered": self.triggered,
            "detail": self.detail,
        }


@dataclass
class StageResult:
    stage: int
    reason: str
    stage3_triggers: list[TriggerResult] = field(default_factory=list)
    sicr_triggers: list[TriggerResult] = field(default_factory=list)

    @property
    def all_triggers(self) -> list[dict]:
        return [t.as_dict() for t in (*self.stage3_triggers, *self.sicr_triggers)]


def _flag(ctx: StageContext, source: str) -> tuple[bool, str]:
    val = getattr(ctx.signals, source, False)
    return bool(val), ("present" if val else "not present")


def _eval_rule(ctx: StageContext, rule: dict) -> TriggerResult:
    rid = rule["id"]
    name = rule["name"]
    cat = rule.get("category", "other")
    rtype = rule["type"]

    if rtype == "dpd_gte":
        thr = int(rule["threshold"])
        hit = ctx.dpd >= thr
        detail = f"DPD {ctx.dpd} {'≥' if hit else '<'} threshold {thr}"
        return TriggerResult(rid, name, cat, hit, detail)

    if rtype == "flag":
        hit, state = _flag(ctx, rule["source"])
        return TriggerResult(rid, name, cat, hit, f"{rule['source']} {state}")

    if rtype == "pd_ratio_gte":
        need = Decimal(str(rule["ratio"]))
        if ctx.current_pd_12m and ctx.origination_pd_12m and ctx.origination_pd_12m > 0:
            ratio = ctx.current_pd_12m / ctx.origination_pd_12m
            hit = ratio >= need
            detail = (
                f"current 12M PD {float(ctx.current_pd_12m):.4f} / origination "
                f"{float(ctx.origination_pd_12m):.4f} = {float(ratio):.2f}× "
                f"({'≥' if hit else '<'} {float(need):.2f}×)"
            )
            return TriggerResult(rid, name, cat, hit, detail)
        return TriggerResult(rid, name, cat, False, "origination PD not available")

    if rtype == "rating_notches_gte":
        need = int(rule["notches"])
        if ctx.current_rating and ctx.origination_rating:
            notches = ecl_risk.rating_notches(ctx.origination_rating, ctx.current_rating)
            hit = notches >= need
            detail = (
                f"rating moved {ctx.origination_rating} → {ctx.current_rating} "
                f"({notches} notch{'es' if notches != 1 else ''}; "
                f"{'≥' if hit else '<'} {need})"
            )
            return TriggerResult(rid, name, cat, hit, detail)
        return TriggerResult(rid, name, cat, False, "origination rating not available")

    return TriggerResult(rid, name, cat, False, f"unknown rule type {rtype!r}")


def determine_stage(ctx: StageContext, cfg: ECLConfiguration) -> StageResult:
    stage3 = [_eval_rule(ctx, r) for r in (cfg.stage3_rules or [])]
    sicr = [_eval_rule(ctx, r) for r in (cfg.sicr_rules or [])]

    fired_3 = [t for t in stage3 if t.triggered]
    if fired_3:
        reason = "Credit-impaired / default: " + "; ".join(t.name for t in fired_3)
        return StageResult(3, reason, stage3, sicr)

    fired_2 = [t for t in sicr if t.triggered]
    if fired_2:
        reason = "SICR criteria satisfied: " + "; ".join(t.name for t in fired_2)
        return StageResult(2, reason, stage3, sicr)

    return StageResult(1, "No SICR or default trigger — performing (Stage 1)", stage3, sicr)
