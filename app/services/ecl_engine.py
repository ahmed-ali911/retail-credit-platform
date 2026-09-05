"""ECL calculation engine — the 3-path provider structure.

Same principle as the KYC / Credit Bureau / Payment Gateway / ERP mocks in this
platform: a narrow interface with swappable implementations, chosen at runtime
by a config switch (``ecl_methodology``). Adding a real PD/LGD data source for
Path B later is a new provider — it does not touch Path A or Path C.

    EclContext  (inputs)  --->  EclProvider.assess()  --->  EclOutcome (result)

Nothing here reads the database or the config table directly: the orchestrator
(``app/services/ecl.py``) gathers everything into an ``EclContext`` first. That
keeps each provider a pure function of its inputs and trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models.ecl import ECLMethodology

_CENTS = Decimal("0.01")
_RATE = Decimal("0.000001")

# Shown wherever a Path B PD / LGD / ECL figure would otherwise appear. The UI
# and every report must render this, never a 0 or an invented number.
NO_PD_LGD_SOURCE = "n/a — no PD/LGD source configured"


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _rate(value) -> Decimal:
    return Decimal(str(value)).quantize(_RATE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class EclContext:
    """Everything a provider needs, already gathered by the orchestrator."""

    contract_id: int
    as_of: date
    ead: Decimal
    dpd: int
    # DPD band label for `dpd` (from `dpd_report_buckets` + "current"); the
    # orchestrator computes it once so Path C and the snapshot agree.
    dpd_bucket: str
    # corroborating risk signals that already exist elsewhere in the platform
    has_open_collections_case: bool = False
    has_broken_promise_to_pay: bool = False
    # config values in force (also copied verbatim into the snapshot)
    provision_pct_by_bucket: dict = field(default_factory=dict)
    lifetime_loss_rate: Decimal = Decimal("0")
    sicr_dpd_threshold: int = 30
    default_dpd_threshold: int = 90


@dataclass
class EclOutcome:
    methodology: ECLMethodology
    ead: Decimal
    dpd: int
    dpd_bucket: str | None = None
    stage: int | None = None
    stage_reason: str | None = None
    pd: Decimal | None = None
    lgd: Decimal | None = None
    loss_rate: Decimal | None = None
    ecl_amount: Decimal | None = None
    # human-readable placeholder for any figure that has no real input yet
    unpopulated_note: str | None = None
    inputs: dict = field(default_factory=dict)


class EclProvider:
    """Interface every methodology implements."""

    methodology: ECLMethodology

    def assess(self, ctx: EclContext) -> EclOutcome:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Path C — dpd_banded (the only path fully computable today)
# --------------------------------------------------------------------------- #
class DpdBandedProvider(EclProvider):
    methodology = ECLMethodology.dpd_banded

    def assess(self, ctx: EclContext) -> EclOutcome:
        pct_table = {str(k): Decimal(str(v)) for k, v in ctx.provision_pct_by_bucket.items()}
        rate = pct_table.get(ctx.dpd_bucket)
        if rate is None:
            # An unmapped band is a config gap, not a silent zero.
            raise ValueError(
                f"ecl_provision_pct_by_bucket has no entry for band "
                f"{ctx.dpd_bucket!r} (bands: {sorted(pct_table)})"
            )
        ecl = _money(ctx.ead * rate)
        return EclOutcome(
            methodology=self.methodology,
            ead=ctx.ead,
            dpd=ctx.dpd,
            dpd_bucket=ctx.dpd_bucket,
            loss_rate=_rate(rate),
            ecl_amount=ecl,
            inputs={
                "dpd_bucket": ctx.dpd_bucket,
                "provision_pct": float(rate),
                "ead": float(ctx.ead),
                "formula": "ecl = ead * provision_pct[dpd_bucket]",
            },
        )


# --------------------------------------------------------------------------- #
# Path A — simplified_lifetime (implementable in full — needs no PD/LGD)
# --------------------------------------------------------------------------- #
class SimplifiedLifetimeProvider(EclProvider):
    methodology = ECLMethodology.simplified_lifetime

    def assess(self, ctx: EclContext) -> EclOutcome:
        rate = Decimal(str(ctx.lifetime_loss_rate))
        ecl = _money(ctx.ead * rate)
        return EclOutcome(
            methodology=self.methodology,
            ead=ctx.ead,
            dpd=ctx.dpd,
            dpd_bucket=None,  # Path A has no staging / banding
            loss_rate=_rate(rate),
            ecl_amount=ecl,
            inputs={
                "lifetime_loss_rate": float(rate),
                "ead": float(ctx.ead),
                "formula": "ecl = ead * lifetime_loss_rate  (lifetime ECL from day one)",
            },
        )


# --------------------------------------------------------------------------- #
# Path B — three_stage (structure only — no real PD/LGD source exists)
# --------------------------------------------------------------------------- #
class ThreeStageProvider(EclProvider):
    methodology = ECLMethodology.three_stage

    def assess(self, ctx: EclContext) -> EclOutcome:
        signals: list[str] = []
        if ctx.has_open_collections_case:
            signals.append("open collections case")
        if ctx.has_broken_promise_to_pay:
            signals.append("broken promise-to-pay")

        stage, reason = self._stage(ctx, signals)

        return EclOutcome(
            methodology=self.methodology,
            ead=ctx.ead,
            dpd=ctx.dpd,
            dpd_bucket=None,
            stage=stage,
            stage_reason=reason,
            # deliberately unpopulated — no PD/LGD source configured
            pd=None,
            lgd=None,
            loss_rate=None,
            ecl_amount=None,
            unpopulated_note=NO_PD_LGD_SOURCE,
            inputs={
                "stage": stage,
                "stage_reason": reason,
                "dpd": ctx.dpd,
                "sicr_dpd_threshold": ctx.sicr_dpd_threshold,
                "default_dpd_threshold": ctx.default_dpd_threshold,
                "corroborating_signals": signals,
                "pd": NO_PD_LGD_SOURCE,
                "lgd": NO_PD_LGD_SOURCE,
                "ecl_amount": NO_PD_LGD_SOURCE,
            },
        )

    @staticmethod
    def _stage(ctx: EclContext, signals: list[str]) -> tuple[int, str]:
        """DPD thresholds are *rebuttable presumptions* — an indicator that
        prompts a staging review, corroborated by at least one other risk
        signal. DPD on its own does not force a stage change (that's the
        presumption being rebutted, since there is no PD/LGD model to assess
        SICR quantitatively)."""
        sig = ", ".join(signals) if signals else None

        if ctx.dpd >= ctx.default_dpd_threshold:
            if sig:
                return 3, (
                    f"DPD {ctx.dpd} ≥ {ctx.default_dpd_threshold} (default presumption) "
                    f"corroborated by: {sig} → credit-impaired"
                )
            return 1, (
                f"DPD {ctx.dpd} ≥ {ctx.default_dpd_threshold} but no corroborating risk "
                f"signal — default presumption treated as rebutted (no PD/LGD model to "
                f"confirm impairment)"
            )

        if ctx.dpd >= ctx.sicr_dpd_threshold:
            if sig:
                return 2, (
                    f"DPD {ctx.dpd} ≥ {ctx.sicr_dpd_threshold} (SICR presumption) "
                    f"corroborated by: {sig}"
                )
            return 1, (
                f"DPD {ctx.dpd} ≥ {ctx.sicr_dpd_threshold} but no corroborating risk "
                f"signal — 30-DPD SICR presumption treated as rebutted"
            )

        if sig:
            return 2, f"SICR from a non-DPD signal: {sig}"
        return 1, "no SICR indicator (performing)"


_PROVIDERS: dict[ECLMethodology, EclProvider] = {
    ECLMethodology.dpd_banded: DpdBandedProvider(),
    ECLMethodology.simplified_lifetime: SimplifiedLifetimeProvider(),
    ECLMethodology.three_stage: ThreeStageProvider(),
}


def get_provider(methodology: ECLMethodology | str) -> EclProvider:
    if not isinstance(methodology, ECLMethodology):
        try:
            methodology = ECLMethodology(str(methodology))
        except ValueError:
            raise ValueError(
                f"ecl_methodology={methodology!r} is not one of "
                f"{[m.value for m in ECLMethodology]}"
            )
    return _PROVIDERS[methodology]
