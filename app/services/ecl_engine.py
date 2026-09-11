"""ECL calculation — the pure, versionable arithmetic layer.

    three_stage         ECL = EAD × PD × LGD   (PD = 12-month for Stage 1,
                                                lifetime for Stages 2 & 3)
    simplified_lifetime ECL = EAD × lifetime_loss_rate
    dpd_banded          ECL = EAD × provision_pct[dpd_band]

This is deliberately a simple prototype formula. The methodology is versioned
(``calculation_version``) so a more sophisticated engine — discounting to
present value at the effective profit rate, marginal-PD term structures,
probability-weighted macro scenarios — can be introduced later without touching
the orchestration, override or persistence code. **A simple EAD × PD × LGD is
NOT a complete IFRS 9 implementation** — see the audit's remaining-validation
list.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.services.ecl_config import CALCULATION_VERSION

__all__ = ["CALCULATION_VERSION", "calc_three_stage", "calc_simplified_lifetime", "calc_dpd_banded", "dpd_band_label"]

_CENTS = Decimal("0.01")


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _d(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def calc_three_stage(
    *, ead, stage: int, pd_12m, pd_lifetime, lgd
) -> tuple[Decimal, Decimal, str]:
    """Returns (ecl_amount, effective_pd, formula_text)."""
    pd = _d(pd_12m) if stage == 1 else _d(pd_lifetime)
    ead_d, lgd_d = _d(ead), _d(lgd)
    ecl = _money(ead_d * pd * lgd_d)
    horizon = "12-month" if stage == 1 else "lifetime"
    formula = (
        f"ECL = EAD {float(ead_d):.2f} × {horizon} PD {float(pd):.4f} "
        f"× LGD {float(lgd_d):.4f} = {float(ecl):.2f}"
    )
    return ecl, pd, formula


def calc_simplified_lifetime(*, ead, lifetime_loss_rate) -> tuple[Decimal, str]:
    rate = _d(lifetime_loss_rate)
    ecl = _money(_d(ead) * rate)
    return ecl, (
        f"ECL = EAD {float(_d(ead)):.2f} × lifetime loss rate {float(rate):.4f} "
        f"= {float(ecl):.2f}  (lifetime ECL from day one, no staging)"
    )


def calc_dpd_banded(*, ead, band_pct) -> tuple[Decimal, str]:
    pct = _d(band_pct)
    ecl = _money(_d(ead) * pct)
    return ecl, f"ECL = EAD {float(_d(ead)):.2f} × band provision % {float(pct):.4f} = {float(ecl):.2f}"


def dpd_band_label(dpd: int, buckets: list) -> str:
    """DPD → band label, using ``dpd_report_buckets`` shape + 'current'."""
    if dpd <= 0:
        return "current"
    for lo, hi in buckets:
        if dpd >= lo and (hi is None or dpd <= hi):
            return f"{lo}-{hi}" if hi is not None else f"{lo}+"
    lo, hi = buckets[-1]
    return f"{lo}+" if hi is None else f"{lo}-{hi}"
