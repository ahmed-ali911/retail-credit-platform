"""Rules-based Credit Assessment Engine.

Every threshold is read from ConfigService. There are no policy numbers in this
module. The engine takes a submitted application, runs each rule, and returns a
single decision plus the list of rules that fired (for audit).

Decision precedence:  rejected  >  referred  >  approved
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.credit_application import (
    ApplicationStatus,
    AssessmentResult,
    CreditApplication,
)
from app.services import config_service as cfg
from app.services.config_service import ConfigService

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


def estimate_installment(requested_amount: float, tenor_months: int, factor: float) -> float:
    if tenor_months <= 0:
        raise ValueError("requested_tenor_months must be positive")
    return round((requested_amount * factor) / tenor_months, 2)


def assess_application(db: Session, application: CreditApplication) -> AssessmentResult:
    """Run the engine, persist an AssessmentResult, and update application.status."""
    config = ConfigService(db)

    min_income = config.get_float(cfg.KEY_MIN_INCOME)
    max_dbr = config.get_float(cfg.KEY_MAX_DBR)
    factor = config.get_float(cfg.KEY_INSTALLMENT_FACTOR)
    auto_approve_min = config.get_int(cfg.KEY_RISK_AUTO_APPROVE_MIN)
    refer_min = config.get_int(cfg.KEY_RISK_REFER_MIN)

    config_snapshot = {
        cfg.KEY_MIN_INCOME: min_income,
        cfg.KEY_MAX_DBR: max_dbr,
        cfg.KEY_INSTALLMENT_FACTOR: factor,
        cfg.KEY_RISK_AUTO_APPROVE_MIN: auto_approve_min,
        cfg.KEY_RISK_REFER_MIN: refer_min,
    }

    profile = application.customer.profile
    income = _f(profile.monthly_income) if profile else 0.0
    obligations = _f(profile.existing_monthly_obligations) if profile else 0.0
    risk_score = application.customer.risk_score

    estimated_installment = estimate_installment(
        _f(application.requested_amount), application.requested_tenor_months, factor
    )

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
