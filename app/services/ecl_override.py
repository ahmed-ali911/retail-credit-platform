"""Manual ECL overrides — Stage Override and Parameter Override.

Two distinct, separately-permissioned actions, both MANDATORY maker-checker
(generic ``ApprovalRequest``; the ``decided_by != requested_by`` rule is
enforced in :mod:`app.services.approvals`):

  * **Stage Override**     — force a contract into a different IFRS 9 stage.
  * **Parameter Override** — pin a PD / LGD / EAD / rating for a contract.

Neither ever mutates the automated assessment. An override is its own record
with an effective window; the next automatic run re-derives the automated
result and layers an ACTIVE override on top (``final_*``). Overrides expire on
``effective_to`` and can be cancelled.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import (
    ACTION_ECL_PARAMETER_OVERRIDE,
    ACTION_ECL_STAGE_OVERRIDE,
    ApprovalRequest,
    ApprovalStatus,
)
from app.models.contract import InstallmentContract
from app.models.ecl import (
    ECLAssessment,
    ECLMethodology,
    ECLOverride,
    ECLOverrideReasonCode,
    ECLOverrideStatus,
    ECLOverrideType,
)
from app.services import approvals as approval_service
from app.services import ecl_config
from app.services.errors import DomainError

_CENTS = Decimal("0.01")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _d(v):
    return None if v is None else Decimal(str(v))


def _rules(db: Session) -> dict:
    return ecl_config.get_active(db).override_rules or {}


def latest_assessment(db: Session, contract_id: int) -> ECLAssessment | None:
    return db.execute(
        select(ECLAssessment)
        .where(ECLAssessment.contract_id == contract_id)
        .order_by(ECLAssessment.as_of_date.desc(), ECLAssessment.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _pending_override(db: Session, contract_id: int) -> ECLOverride | None:
    return db.execute(
        select(ECLOverride).where(
            ECLOverride.contract_id == contract_id,
            ECLOverride.status == ECLOverrideStatus.pending,
        )
    ).scalar_one_or_none()


def _resolve_window(
    rules: dict, effective_from: str | None, effective_to: str | None, review_date: str | None
) -> tuple[date, date, date]:
    today = _utcnow().date()
    ef = date.fromisoformat(effective_from) if effective_from else today
    et = (
        date.fromisoformat(effective_to)
        if effective_to
        else ef + timedelta(days=int(rules.get("default_validity_days", 90)))
    )
    rv = (
        date.fromisoformat(review_date)
        if review_date
        else ef + timedelta(days=int(rules.get("review_after_days", 90)))
    )
    if et < ef:
        raise DomainError("effective_to cannot be before effective_from", status_code=422)
    return ef, et, rv


def _pd_lgd_from_snapshot(a: ECLAssessment, stage: int) -> tuple[Decimal, Decimal]:
    """Project the automated PD/LGD the engine would use for ``stage`` (from the
    config snapshot stamped on the assessment) — for the financial-impact
    preview only; the authoritative number comes from the next run."""
    snap = a.config_snapshot or {}
    seg = a.risk_segment or "retail_unrated"
    pts = (snap.get("pd_term_structure") or {}).get(seg, {})
    lgdm = snap.get("lgd_model") or {}
    lrow = lgdm.get(seg) or lgdm.get("_default") or {}
    if stage == 1:
        pd = a.automated_pd_12m if a.automated_pd_12m is not None else _d(pts.get("pd_12m")) or Decimal("0")
        lgd = a.automated_lgd if a.automated_lgd is not None else _d(lrow.get("lgd")) or Decimal("0")
    elif stage == 2:
        pd = _d(pts.get("pd_lifetime")) or Decimal("0")
        lgd = _d(lrow.get("lgd")) or Decimal("0")
    else:
        pd = _d(pts.get("pd_lifetime_stage_3", pts.get("pd_lifetime"))) or Decimal("0")
        lgd = _d(lrow.get("lgd_stage_3", lrow.get("lgd"))) or Decimal("0")
    return pd, lgd


def _project_stage_ecl(a: ECLAssessment, stage: int) -> Decimal:
    pd, lgd = _pd_lgd_from_snapshot(a, stage)
    return _money(_d(a.final_ead or a.ead) * pd * lgd)


def _project_parameter_ecl(a: ECLAssessment, values: dict) -> Decimal:
    stage = a.automated_stage or 1
    ead = _d(values.get("ead")) if values.get("ead") is not None else _d(a.ead)
    lgd = _d(values.get("lgd")) if values.get("lgd") is not None else _d(a.automated_lgd)
    if stage == 1:
        pd = _d(values.get("pd_12m")) if values.get("pd_12m") is not None else _d(a.automated_pd_12m)
    else:
        pd = (
            _d(values.get("pd_lifetime"))
            if values.get("pd_lifetime") is not None
            else _d(a.automated_pd_lifetime)
        )
    return _money((ead or Decimal("0")) * (pd or Decimal("0")) * (lgd or Decimal("0")))


def _reason(code: str) -> ECLOverrideReasonCode:
    try:
        return ECLOverrideReasonCode(code)
    except ValueError:
        raise DomainError(
            f"reason_code must be one of {[c.value for c in ECLOverrideReasonCode]}",
            status_code=422,
        )


def _guards(db: Session, contract_id: int, evidence_ref: str | None) -> tuple[InstallmentContract, ECLAssessment]:
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise DomainError("Contract not found", status_code=404)
    a = latest_assessment(db, contract_id)
    if a is None:
        raise DomainError(
            "No ECL assessment for this contract yet — run the ECL job first",
            status_code=409,
        )
    if _pending_override(db, contract_id) is not None:
        raise DomainError(
            "An override request is already pending for this contract", status_code=409
        )
    if _rules(db).get("require_evidence") and not evidence_ref:
        raise DomainError("evidence_ref is required by ECL override policy", status_code=422)
    return contract, a


def request_stage_override(
    db: Session, *, contract_id: int, actor_id: int, payload
) -> ApprovalRequest:
    contract, a = _guards(db, contract_id, payload.evidence_ref)
    if a.methodology != ECLMethodology.three_stage:
        raise DomainError(
            "Stage override only applies under the three_stage methodology",
            status_code=409,
        )
    if payload.stage == a.automated_stage:
        raise DomainError(
            f"Contract is already automated Stage {a.automated_stage}", status_code=422
        )
    ef, et, rv = _resolve_window(
        _rules(db), payload.effective_from, payload.effective_to, payload.review_date
    )
    ecl_before = _money(a.automated_ecl)
    ecl_after = _project_stage_ecl(a, payload.stage)
    ov = ECLOverride(
        contract_id=contract_id,
        customer_id=a.customer_id,
        override_type=ECLOverrideType.stage,
        status=ECLOverrideStatus.pending,
        reason_code=_reason(payload.reason_code),
        justification=payload.justification,
        evidence_ref=payload.evidence_ref,
        comments=payload.comments,
        automated_value={"stage": a.automated_stage},
        approved_value={"stage": payload.stage},
        ecl_before=ecl_before,
        ecl_after=ecl_after,
        financial_impact=ecl_after - ecl_before,
        effective_from=ef,
        effective_to=et,
        review_date=rv,
        requested_by=actor_id,
    )
    db.add(ov)
    db.flush()
    req = approval_service.create_request(
        db,
        action_type=ACTION_ECL_STAGE_OVERRIDE,
        entity_type="ecl_override",
        entity_id=ov.id,
        requested_by=actor_id,
        payload={
            "contract_id": contract_id,
            "override_id": ov.id,
            "override_type": "STAGE",
            "automated_stage": a.automated_stage,
            "requested_stage": payload.stage,
            "reason_code": payload.reason_code,
            "justification": payload.justification,
            "ecl_before": float(ecl_before),
            "ecl_after": float(ecl_after),
            "financial_impact": float(ecl_after - ecl_before),
            "effective_from": ef.isoformat(),
            "effective_to": et.isoformat(),
        },
    )
    ov.approval_request_id = req.id
    db.flush()
    return req


def request_parameter_override(
    db: Session, *, contract_id: int, actor_id: int, payload
) -> ApprovalRequest:
    contract, a = _guards(db, contract_id, payload.evidence_ref)
    if a.methodology != ECLMethodology.three_stage:
        # _assess_one() only applies override_{pd_12m,pd_lifetime,lgd,ead} under
        # three_stage — approving one here would show ACTIVE while silently
        # leaving final_ecl == automated_ecl. Reject at request time instead.
        raise DomainError(
            "Parameter override only applies under the three_stage methodology",
            status_code=409,
        )
    allowed = set(_rules(db).get("allowed_parameters", []))
    values = {
        k: getattr(payload, k)
        for k in ("pd_12m", "pd_lifetime", "lgd", "ead", "risk_rating")
        if getattr(payload, k) is not None
    }
    if not values:
        raise DomainError("At least one parameter must be provided", status_code=422)
    bad = [k for k in values if allowed and k not in allowed]
    if bad:
        raise DomainError(
            f"Parameter(s) {bad} are not overridable per ECL policy "
            f"(allowed: {sorted(allowed)})",
            status_code=422,
        )
    ef, et, rv = _resolve_window(
        _rules(db), payload.effective_from, payload.effective_to, payload.review_date
    )
    ecl_before = _money(a.automated_ecl)
    ecl_after = _project_parameter_ecl(a, values)
    automated = {
        "pd_12m": float(a.automated_pd_12m) if a.automated_pd_12m is not None else None,
        "pd_lifetime": float(a.automated_pd_lifetime)
        if a.automated_pd_lifetime is not None
        else None,
        "lgd": float(a.automated_lgd) if a.automated_lgd is not None else None,
        "ead": float(a.ead),
        "risk_rating": a.risk_rating,
    }
    ov = ECLOverride(
        contract_id=contract_id,
        customer_id=a.customer_id,
        override_type=ECLOverrideType.parameter,
        status=ECLOverrideStatus.pending,
        reason_code=_reason(payload.reason_code),
        justification=payload.justification,
        evidence_ref=payload.evidence_ref,
        comments=payload.comments,
        automated_value={k: automated[k] for k in values},
        approved_value=values,
        ecl_before=ecl_before,
        ecl_after=ecl_after,
        financial_impact=ecl_after - ecl_before,
        effective_from=ef,
        effective_to=et,
        review_date=rv,
        requested_by=actor_id,
    )
    db.add(ov)
    db.flush()
    req = approval_service.create_request(
        db,
        action_type=ACTION_ECL_PARAMETER_OVERRIDE,
        entity_type="ecl_override",
        entity_id=ov.id,
        requested_by=actor_id,
        payload={
            "contract_id": contract_id,
            "override_id": ov.id,
            "override_type": "PARAMETER",
            "automated_value": ov.automated_value,
            "approved_value": values,
            "reason_code": payload.reason_code,
            "justification": payload.justification,
            "ecl_before": float(ecl_before),
            "ecl_after": float(ecl_after),
            "financial_impact": float(ecl_after - ecl_before),
            "effective_from": ef.isoformat(),
            "effective_to": et.isoformat(),
        },
    )
    ov.approval_request_id = req.id
    db.flush()
    return req


def cancel_override(db: Session, override_id: int, *, actor_id: int, reason: str | None = None) -> ECLOverride:
    ov = db.get(ECLOverride, override_id)
    if ov is None:
        raise DomainError("Override not found", status_code=404)
    if ov.status not in (
        ECLOverrideStatus.pending,
        ECLOverrideStatus.approved,
        ECLOverrideStatus.active,
    ):
        raise DomainError(
            f"Cannot cancel an override that is {ov.status.value}", status_code=409
        )
    # a still-pending approval request is closed off too
    if ov.approval_request_id:
        req = db.get(ApprovalRequest, ov.approval_request_id)
        if req is not None and req.status == ApprovalStatus.pending:
            req.status = ApprovalStatus.rejected
            req.decided_by = actor_id
            req.decided_at = _utcnow()
            req.decision_notes = reason or "Override cancelled by requester/finance"
    ov.status = ECLOverrideStatus.cancelled
    ov.comments = ((ov.comments or "") + f"\n[cancelled] {reason or ''}").strip()
    db.flush()
    return ov


def expire_due(db: Session, *, as_of: date | None = None) -> dict:
    """Flip ACTIVE/APPROVED overrides whose window has closed to EXPIRED, and
    promote APPROVED overrides that have now entered their window to ACTIVE."""
    today = as_of or _utcnow().date()
    rows = db.execute(
        select(ECLOverride).where(
            ECLOverride.status.in_(
                [ECLOverrideStatus.active, ECLOverrideStatus.approved]
            )
        )
    ).scalars().all()
    expired, activated = 0, 0
    for ov in rows:
        if ov.effective_to is not None and ov.effective_to < today:
            ov.status = ECLOverrideStatus.expired
            expired += 1
        elif ov.status == ECLOverrideStatus.approved and ov.effective_from <= today:
            ov.status = ECLOverrideStatus.active
            activated += 1
    db.flush()
    return {"expired": expired, "activated": activated, "as_of": today.isoformat()}
