"""Customer origination → risk rating → risk segment → PD / LGD.

    risk_score  ──rating_mapping──▶  internal rating  ──risk_segments──▶  segment
    segment     ──pd_term_structure──▶  {pd_12m, pd_lifetime, pd_lifetime_stage_3}
    segment     ──lgd_model──────────▶  {lgd, lgd_stage_3}

Every mapping is read from the active ``ECLConfiguration`` — no PD/LGD number is
hard-coded here, and none is claimed to be an IFRS 9 value. A new customer never
gets an arbitrary manual PD: it is derived from the credit-assessment risk score
via the configured rating/segment/PD structure.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.ecl import ECLConfiguration

UNRATED = "UNRATED"
# Rating grade order, worst last — for "notch deterioration" comparisons.
_RATING_ORDER = ["A", "B", "C", "D", "E"]


@dataclass(frozen=True)
class PDSet:
    pd_12m: Decimal
    pd_lifetime: Decimal
    pd_lifetime_stage_3: Decimal


@dataclass(frozen=True)
class LGDSet:
    lgd: Decimal
    lgd_stage_3: Decimal


def resolve_rating(risk_score: int | None, cfg: ECLConfiguration) -> str:
    """risk_score → internal rating grade. ``None`` → UNRATED."""
    if risk_score is None:
        return UNRATED
    for band in cfg.rating_mapping:
        if band["min"] <= risk_score <= band["max"]:
            return str(band["rating"])
    return UNRATED


def resolve_segment(rating: str, cfg: ECLConfiguration) -> str:
    seg = cfg.risk_segments.get(rating)
    if seg:
        return str(seg)
    return cfg.risk_segments.get(UNRATED, "retail_unrated")


def resolve_pd(segment: str, cfg: ECLConfiguration) -> PDSet:
    row = cfg.pd_term_structure.get(segment) or cfg.pd_term_structure.get(
        "retail_unrated"
    )
    if row is None:
        raise ValueError(
            f"ECL configuration has no PD term structure for segment {segment!r}"
        )
    pd_12m = Decimal(str(row["pd_12m"]))
    pd_life = Decimal(str(row["pd_lifetime"]))
    pd_life_s3 = Decimal(str(row.get("pd_lifetime_stage_3", row["pd_lifetime"])))
    return PDSet(pd_12m=pd_12m, pd_lifetime=pd_life, pd_lifetime_stage_3=pd_life_s3)


def resolve_lgd(segment: str, cfg: ECLConfiguration) -> LGDSet:
    model = cfg.lgd_model or {}
    row = model.get(segment) or model.get("_default")
    if row is None:
        raise ValueError(
            f"ECL configuration has no LGD model for segment {segment!r}"
        )
    lgd = Decimal(str(row["lgd"]))
    lgd_s3 = Decimal(str(row.get("lgd_stage_3", row["lgd"])))
    return LGDSet(lgd=lgd, lgd_stage_3=lgd_s3)


def rating_notches(from_rating: str, to_rating: str) -> int:
    """How many grades worse ``to_rating`` is than ``from_rating`` (0 or negative
    if the same or better). UNRATED is treated as the worst grade."""
    def idx(r: str) -> int:
        return _RATING_ORDER.index(r) if r in _RATING_ORDER else len(_RATING_ORDER)

    return idx(to_rating) - idx(from_rating)
