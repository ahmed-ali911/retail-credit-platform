"""Rules-based Credit Assessment Engine.

Every threshold is read from ConfigService. There are no policy numbers in this
module. The engine takes a submitted application, runs each rule, and returns a
single decision plus the list of rules that fired (for audit).

Decision precedence:  rejected  >  referred  >  approved
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models.credit_application import (
    ApplicationStatus,
    AssessmentResult,
    CreditApplication,
)
from app.services import config_service as cfg
from app.services import exposure as exposure_service
from app.services import pricing
from app.services.config_service import ConfigService

_CENTS = Decimal("0.01")

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_REFERRED = "referred"

# How each rule's outcome maps onto a decision.
_OUTCOME_RANK = {DECISION_APPROVED: 0, DECISION_REFERRED: 1, DECISION_REJECTED: 2}
_RANK_TO_DECISION = {v: k for k, v in _OUTCOME_RANK.items()}


@dataclass
class RuleOutcome:
    rule: str
    outcome: str  # approved | referred | rejected
    passed: bool
    detail: str
    context: dict = field(default_factory=dict)


@dataclass
class AssessmentOutput:
    decision: str
    estimated_installment: float
    debt_burden_ratio: float | None
    triggered_rules: list[dict]
    config_snapshot: dict


def _f(value) -> float:
    return float(value) if not isinstance(value, Decimal) else float(value)


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def estimate_installment(
    db: Session,
    *,
    requested_amount: float,
    tenor_months: int,
    min_down_payment_pct: float,
    fallback_factor: float,
) -> tuple[float, dict]:
    """Application-time estimate of the monthly installment.

    The real down payment and per-installment split don't exist yet (no offer),
    so: assume the configured minimum down payment, and use the Pricing Engine's
    own tenor -> rate table (`pricing.resolve_profit_rate`) — never a second copy
    of that logic. Falls back to the old flat proxy only if the requested tenor
    has no configured rate yet.

    Returns ``(estimated_installment, basis)`` where ``basis`` is a small dict
    recording exactly what assumption produced the figure (stored on the
    AssessmentResult's config snapshot for audit).
    """
    if tenor_months <= 0:
        raise ValueError("requested_tenor_months must be positive")

    req = Decimal(str(requested_amount))
    dp_pct = Decimal(str(min_down_payment_pct))
    amount_financed = req * (Decimal("1") - dp_pct)
    # The financed-amount estimate is the same regardless of the profit method,
    # and is reused by the P0-4 exposure rule — always record it.
    common_basis = {
        "assumed_down_payment_pct": float(dp_pct),
        "amount_financed_estimate": float(_round2(amount_financed)),
    }

    try:
        rate = pricing.resolve_profit_rate(db, tenor_months)
    except pricing.PricingError:
        value = req * Decimal(str(fallback_factor)) / Decimal(tenor_months)
        return float(_round2(value)), {
            "installment_estimate_method": "flat_factor",
            cfg.KEY_INSTALLMENT_FACTOR: float(fallback_factor),
            **common_basis,
        }

    estimated_profit = amount_financed * rate
    value = (amount_financed + estimated_profit) / Decimal(tenor_months)
    return float(_round2(value)), {
        "installment_estimate_method": "rate_table",
        "tenor_profit_rate": float(rate),
        "estimated_profit": float(_round2(estimated_profit)),
        **common_basis,
    }


def assess_application(db: Session, application: CreditApplication) -> AssessmentResult:
    """Run the engine, persist an AssessmentResult, and update application.status."""
    config = ConfigService(db)

    min_income = config.get_float(cfg.KEY_MIN_INCOME)
    max_dbr = config.get_float(cfg.KEY_MAX_DBR)
    factor = config.get_float(cfg.KEY_INSTALLMENT_FACTOR)
    min_dp_pct = config.get_float(cfg.KEY_MIN_DOWN_PAYMENT_PCT)
    auto_approve_min = config.get_int(cfg.KEY_RISK_AUTO_APPROVE_MIN)
    refer_min = config.get_int(cfg.KEY_RISK_REFER_MIN)
    max_exposure = config.get_float(cfg.KEY_MAX_CUSTOMER_EXPOSURE)
    aggregation_level = str(config.get(cfg.KEY_EXPOSURE_AGGREGATION_LEVEL)).strip()

    estimated_installment, estimate_basis = estimate_installment(
        db,
        requested_amount=_f(application.requested_amount),
        tenor_months=application.requested_tenor_months,
        min_down_payment_pct=min_dp_pct,
        fallback_factor=factor,
    )

    config_snapshot = {
        cfg.KEY_MIN_INCOME: min_income,
        cfg.KEY_MAX_DBR: max_dbr,
        cfg.KEY_INSTALLMENT_FACTOR: factor,
        cfg.KEY_MIN_DOWN_PAYMENT_PCT: min_dp_pct,
        cfg.KEY_RISK_AUTO_APPROVE_MIN: auto_approve_min,
        cfg.KEY_RISK_REFER_MIN: refer_min,
        cfg.KEY_MAX_CUSTOMER_EXPOSURE: max_exposure,
        cfg.KEY_EXPOSURE_AGGREGATION_LEVEL: aggregation_level,
        **estimate_basis,
    }

    profile = application.customer.profile
    income = _f(profile.monthly_income) if profile else 0.0
    obligations = _f(profile.existing_monthly_obligations) if profile else 0.0
    risk_score = application.customer.risk_score

    # Customer's current aggregate exposure across existing non-closed contracts,
    # plus the estimated financed amount of THIS request (P0-3's estimate).
    current_exposure = _f(
        exposure_service.compute_exposure(db, application.customer_id).total_outstanding
    )
    new_financed_estimate = _f(estimate_basis["amount_financed_estimate"])
    projected_exposure = round(current_exposure + new_financed_estimate, 2)

    outcomes: list[RuleOutcome] = []

    # Rule 1 — minimum income
    if income >= min_income:
        outcomes.append(RuleOutcome(
            rule="minimum_income",
            outcome=DECISION_APPROVED,
            passed=True,
            detail=f"monthly income {income:.2f} >= minimum {min_income:.2f}",
            context={"monthly_income": income, "minimum_monthly_income": min_income},
        ))
    else:
        outcomes.append(RuleOutcome(
            rule="minimum_income",
            outcome=DECISION_REJECTED,
            passed=False,
            detail=f"monthly income {income:.2f} is below minimum {min_income:.2f}",
            context={"monthly_income": income, "minimum_monthly_income": min_income},
        ))

    # Rule 2 — debt-burden ratio
    dbr: float | None = None
    if income > 0:
        dbr = round((obligations + estimated_installment) / income, 4)
        if dbr <= max_dbr:
            outcomes.append(RuleOutcome(
                rule="debt_burden_ratio",
                outcome=DECISION_APPROVED,
                passed=True,
                detail=f"DBR {dbr:.4f} <= maximum {max_dbr:.4f}",
                context={"debt_burden_ratio": dbr, "maximum_debt_burden_ratio": max_dbr,
                         "existing_obligations": obligations,
                         "estimated_installment": estimated_installment},
            ))
        else:
            outcomes.append(RuleOutcome(
                rule="debt_burden_ratio",
                outcome=DECISION_REFERRED,
                passed=False,
                detail=f"DBR {dbr:.4f} exceeds maximum {max_dbr:.4f}",
                context={"debt_burden_ratio": dbr, "maximum_debt_burden_ratio": max_dbr,
                         "existing_obligations": obligations,
                         "estimated_installment": estimated_installment},
            ))
    else:
        outcomes.append(RuleOutcome(
            rule="debt_burden_ratio",
            outcome=DECISION_REJECTED,
            passed=False,
            detail="cannot compute DBR: monthly income is zero or missing",
            context={"monthly_income": income},
        ))

    # Rule 3 — risk banding
    if risk_score is None:
        outcomes.append(RuleOutcome(
            rule="risk_band",
            outcome=DECISION_REFERRED,
            passed=False,
            detail="customer risk_score is not set; manual review required",
            context={"risk_score": None},
        ))
    elif risk_score >= auto_approve_min:
        outcomes.append(RuleOutcome(
            rule="risk_band",
            outcome=DECISION_APPROVED,
            passed=True,
            detail=f"risk_score {risk_score} >= auto-approve threshold {auto_approve_min}",
            context={"risk_score": risk_score,
                     "risk_score_auto_approve_min": auto_approve_min},
        ))
    elif risk_score >= refer_min:
        outcomes.append(RuleOutcome(
            rule="risk_band",
            outcome=DECISION_REFERRED,
            passed=False,
            detail=(f"risk_score {risk_score} in referral band "
                    f"[{refer_min}, {auto_approve_min})"),
            context={"risk_score": risk_score,
                     "risk_score_refer_min": refer_min,
                     "risk_score_auto_approve_min": auto_approve_min},
        ))
    else:
        outcomes.append(RuleOutcome(
            rule="risk_band",
            outcome=DECISION_REJECTED,
            passed=False,
            detail=f"risk_score {risk_score} is below referral threshold {refer_min}",
            context={"risk_score": risk_score, "risk_score_refer_min": refer_min},
        ))

    # Rule 4 — customer exposure limit (P0-4). Same shape as the DBR rule: a
    # prudential debt-capacity check, so a breach routes to manual review
    # (referred), it does not auto-reject.
    exposure_context = {
        "aggregation_level": aggregation_level,
        "current_exposure": round(current_exposure, 2),
        "new_financed_estimate": round(new_financed_estimate, 2),
        "projected_exposure": projected_exposure,
        cfg.KEY_MAX_CUSTOMER_EXPOSURE: max_exposure,
    }
    if projected_exposure <= max_exposure:
        outcomes.append(RuleOutcome(
            rule="customer_exposure",
            outcome=DECISION_APPROVED,
            passed=True,
            detail=(f"projected exposure {projected_exposure:.2f} "
                    f"<= maximum {max_exposure:.2f}"),
            context=exposure_context,
        ))
    else:
        outcomes.append(RuleOutcome(
            rule="customer_exposure",
            outcome=DECISION_REFERRED,
            passed=False,
            detail=(f"projected exposure {projected_exposure:.2f} "
                    f"(current {current_exposure:.2f} + new "
                    f"{new_financed_estimate:.2f}) exceeds maximum "
                    f"{max_exposure:.2f}"),
            context=exposure_context,
        ))

    decision = _RANK_TO_DECISION[max(_OUTCOME_RANK[o.outcome] for o in outcomes)]

    # Only the rules that did NOT pass are "triggered" (audit list). If the
    # application is approved, this list is empty.
    triggered = [
        {
            "rule": o.rule,
            "outcome": o.outcome,
            "reason": o.detail,
            "context": o.context,
        }
        for o in outcomes
        if not o.passed
    ]

    result = AssessmentResult(
        application_id=application.id,
        decision=decision,
        estimated_installment=estimated_installment,
        debt_burden_ratio=dbr,
        triggered_rules=triggered,
        config_snapshot=config_snapshot,
    )
    db.add(result)

    application.status = ApplicationStatus(decision)
    db.flush()
    return result
