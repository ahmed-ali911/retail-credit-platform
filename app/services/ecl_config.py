"""Versioned ECL model calibration.

An ``ECLConfiguration`` row is immutable. A change activates a **new version**
(clone the active row, apply the changes, bump ``version``, swap ``is_active``).
Every ``ECLAssessment`` and ``ECLRun`` stamps the version that produced it, so a
historical run stays reproducible after Finance revises the model.

*** Every value in the seeded v1 is a PLACEHOLDER — BUSINESS / RISK MODEL
DECISION REQUIRED (audit BDR-43 … BDR-52). None is an IFRS 9 requirement. ***
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ecl import ECLConfiguration, ECLMethodology

STAGE_RULE_VERSION = "1.0"
PD_MODEL_VERSION = "1.0"
LGD_MODEL_VERSION = "1.0"
CALCULATION_VERSION = "1.0"

# The fields a new version may override.
_MUTABLE_FIELDS = (
    "methodology",
    "rating_mapping",
    "risk_segments",
    "pd_term_structure",
    "lgd_model",
    "stage3_rules",
    "sicr_rules",
    "cure_rules",
    "override_rules",
    "accounting_mapping",
    "dpd_provision_pct",
    "lifetime_loss_rate",
)


# --------------------------------------------------------------------------- #
# Placeholder v1 — matches the audit's BDR-43..52 demo values and the prompt's
# §6 worked scenario (Rating B: 12M PD 2%, Lifetime PD 12%, LGD 40%; Stage 3
# elevated to Lifetime PD 45% / LGD 50%).
# --------------------------------------------------------------------------- #
def _placeholder_v1() -> dict:
    return {
        "methodology": ECLMethodology.three_stage.value,
        # BDR-43 — risk_score band -> internal rating grade
        "rating_mapping": [
            {"min": 750, "max": 1000, "rating": "A"},
            {"min": 700, "max": 749, "rating": "B"},
            {"min": 650, "max": 699, "rating": "C"},
            {"min": 600, "max": 649, "rating": "D"},
            {"min": 0, "max": 599, "rating": "E"},
        ],
        # BDR-44 — rating grade -> risk segment
        "risk_segments": {
            "A": "retail_prime",
            "B": "retail_prime",
            "C": "retail_standard",
            "D": "retail_subprime",
            "E": "retail_subprime",
            "UNRATED": "retail_unrated",
        },
        # BDR-45 — segment -> PD term structure (12m, lifetime, stage-3 lifetime)
        "pd_term_structure": {
            "retail_prime": {"pd_12m": 0.02, "pd_lifetime": 0.12, "pd_lifetime_stage_3": 0.45},
            "retail_standard": {"pd_12m": 0.04, "pd_lifetime": 0.18, "pd_lifetime_stage_3": 0.55},
            "retail_subprime": {"pd_12m": 0.09, "pd_lifetime": 0.30, "pd_lifetime_stage_3": 0.70},
            "retail_unrated": {"pd_12m": 0.05, "pd_lifetime": 0.20, "pd_lifetime_stage_3": 0.55},
        },
        # BDR-46 — segment -> LGD (base + stage-3 override)
        "lgd_model": {
            "_default": {"lgd": 0.40, "lgd_stage_3": 0.50},
            "retail_prime": {"lgd": 0.40, "lgd_stage_3": 0.50},
            "retail_standard": {"lgd": 0.45, "lgd_stage_3": 0.55},
            "retail_subprime": {"lgd": 0.55, "lgd_stage_3": 0.65},
            "retail_unrated": {"lgd": 0.45, "lgd_stage_3": 0.55},
        },
        # BDR-48 — Stage-3 (default / credit-impaired) triggers, evaluated FIRST
        "stage3_rules": [
            {"id": "dpd_default", "name": "DPD ≥ default threshold",
             "category": "dpd", "type": "dpd_gte", "threshold": 90},
            {"id": "broken_ptp", "name": "Broken promise-to-pay (unlikely to pay)",
             "category": "utp", "type": "flag", "source": "broken_promise_to_pay"},
            {"id": "credit_impaired_flag", "name": "Credit-impaired flag",
             "category": "impairment", "type": "flag", "source": "credit_impaired_flag"},
        ],
        # BDR-47 — Stage-2 / SICR triggers, evaluated after Stage-3
        "sicr_rules": [
            {"id": "dpd_sicr", "name": "DPD ≥ SICR threshold",
             "category": "dpd", "type": "dpd_gte", "threshold": 30},
            {"id": "collections_case", "name": "Open collections case",
             "category": "qualitative", "type": "flag", "source": "open_collections_case"},
            {"id": "pd_deterioration", "name": "Significant PD deterioration vs origination",
             "category": "quantitative", "type": "pd_ratio_gte", "ratio": 2.0},
            {"id": "rating_deterioration", "name": "Significant rating deterioration vs origination",
             "category": "quantitative", "type": "rating_notches_gte", "notches": 2},
            {"id": "forbearance", "name": "Restructuring / forbearance due to financial difficulty",
             "category": "qualitative", "type": "flag", "source": "forbearance_flag"},
        ],
        # BDR-49 — stage curing (anti-flip-flop) — placeholder thresholds
        "cure_rules": {
            "enabled": True,
            "stage_3_to_2": {"min_qualifying_payments": 3, "max_dpd": 0, "min_observation_days": 90},
            "stage_2_to_1": {"min_qualifying_payments": 3, "max_dpd": 0, "min_observation_days": 30},
        },
        # BDR-50 — manual override policy
        "override_rules": {
            "allowed_parameters": ["stage", "pd_12m", "pd_lifetime", "lgd", "ead", "risk_rating"],
            "default_validity_days": 90,
            "review_after_days": 90,
            "require_evidence": True,
        },
        # BDR-31 — GL mapping per ECL event type (unmapped)
        "accounting_mapping": {},
        # retained dpd_banded provision-% per band
        "dpd_provision_pct": {
            "current": 0.005, "1-30": 0.03, "31-60": 0.15, "61-90": 0.40, "91+": 0.75,
        },
        # retained simplified_lifetime single rate
        "lifetime_loss_rate": 0.10,
    }


def _apply(row: ECLConfiguration, values: dict) -> None:
    for field in _MUTABLE_FIELDS:
        if field not in values:
            continue
        v = values[field]
        if field == "methodology":
            row.methodology = ECLMethodology(v) if not isinstance(v, ECLMethodology) else v
        elif field == "lifetime_loss_rate":
            row.lifetime_loss_rate = Decimal(str(v))
        else:
            setattr(row, field, v)


def get_active(db: Session) -> ECLConfiguration:
    """The active configuration. Seeds v1 (all placeholders) if none exists."""
    row = db.execute(
        select(ECLConfiguration).where(ECLConfiguration.is_active.is_(True))
    ).scalar_one_or_none()
    if row is not None:
        return row

    row = ECLConfiguration(version=1, is_active=True)
    _apply(row, _placeholder_v1())
    row.activated_at = datetime.now(timezone.utc)
    row.notes = "Seeded placeholder v1 — every value is BUSINESS/RISK MODEL DECISION REQUIRED."
    db.add(row)
    db.flush()
    return row


def as_snapshot(cfg: ECLConfiguration) -> dict:
    """The full config values, for stamping onto an assessment (reproducibility)."""
    return {
        "ecl_config_version": cfg.version,
        "methodology": cfg.methodology.value,
        "rating_mapping": cfg.rating_mapping,
        "risk_segments": cfg.risk_segments,
        "pd_term_structure": cfg.pd_term_structure,
        "lgd_model": cfg.lgd_model,
        "stage3_rules": cfg.stage3_rules,
        "sicr_rules": cfg.sicr_rules,
        "cure_rules": cfg.cure_rules,
        "override_rules": cfg.override_rules,
        "dpd_provision_pct": cfg.dpd_provision_pct,
        "lifetime_loss_rate": float(cfg.lifetime_loss_rate),
        "stage_rule_version": STAGE_RULE_VERSION,
        "pd_model_version": PD_MODEL_VERSION,
        "lgd_model_version": LGD_MODEL_VERSION,
        "calculation_version": CALCULATION_VERSION,
    }


def activate_version(
    db: Session, *, changes: dict, actor_id: int | None = None, notes: str | None = None
) -> ECLConfiguration:
    """Clone the active config, apply ``changes``, bump the version, make it
    active. The previous version stays in the table, just inactive."""
    current = get_active(db)
    for field in changes:
        if field not in _MUTABLE_FIELDS:
            raise ValueError(f"'{field}' is not an ECL-configurable field")
    if "methodology" in changes:
        try:
            ECLMethodology(str(changes["methodology"]))
        except ValueError:
            raise ValueError(
                f"methodology={changes['methodology']!r} is not one of "
                f"{[m.value for m in ECLMethodology]}"
            )

    next_version = (
        db.execute(select(ECLConfiguration.version).order_by(ECLConfiguration.version.desc()))
        .scalars()
        .first()
        or 0
    ) + 1

    new_row = ECLConfiguration(version=next_version, is_active=True)
    _apply(new_row, {**_current_values(current), **changes})
    new_row.created_by = actor_id
    new_row.activated_at = datetime.now(timezone.utc)
    new_row.notes = notes or f"Activated v{next_version} (from v{current.version})."
    current.is_active = False
    db.add(new_row)
    db.flush()
    return new_row


def _current_values(cfg: ECLConfiguration) -> dict:
    return {
        "methodology": cfg.methodology.value,
        "rating_mapping": cfg.rating_mapping,
        "risk_segments": cfg.risk_segments,
        "pd_term_structure": cfg.pd_term_structure,
        "lgd_model": cfg.lgd_model,
        "stage3_rules": cfg.stage3_rules,
        "sicr_rules": cfg.sicr_rules,
        "cure_rules": cfg.cure_rules,
        "override_rules": cfg.override_rules,
        "accounting_mapping": cfg.accounting_mapping,
        "dpd_provision_pct": cfg.dpd_provision_pct,
        "lifetime_loss_rate": float(cfg.lifetime_loss_rate),
    }


def list_versions(db: Session) -> list[ECLConfiguration]:
    return list(
        db.execute(select(ECLConfiguration).order_by(ECLConfiguration.version.desc()))
        .scalars()
        .all()
    )
