"""Write-off & Recovery — eligibility engine and write-off REQUEST creation.

Financial execution (turning an APPROVED request into a WriteOffExecution —
mutating installment/late-fee balances, closing the contract for a FULL
write-off, emitting the accounting event) is a LATER checkpoint by explicit
instruction. This module only:

  1. evaluates eligibility (explainable, never fabricates data it doesn't
     have — see ``evaluate_eligibility``);
  2. creates a ``WriteOffRequest`` and routes it through the EXISTING
     generic maker-checker (``services/approvals.py``, a new
     ``ACTION_WRITE_OFF_REQUEST`` action type);
  3. on approval, moves the request to APPROVED — nothing more. See
     ``services/approvals.py``'s dispatch branch for that action type.

Design principle (confirmed with the business owner before this was
written): write-off is never automatic. A Stage-3/severely-delinquent
contract does not become eligible by force — ``evaluate_eligibility``
computes an explainable, informational verdict; a human maker still decides
whether to request a write-off, and a human checker still decides whether
to approve it.

Eligibility control (confirmed): ELIGIBLE allows a normal request;
NOT_ELIGIBLE blocks a normal request; NOT_ELIGIBLE and UNAVAILABLE both
allow a controlled EXCEPTION request (mandatory ``exception_justification``,
still subject to the exact same maker-checker). Both the system's own
eligibility verdict and the exception rationale are preserved on the
request row, permanently, side by side — the system is never overruled
silently.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import contract_owner_customer_id
from app.models.approval import ACTION_WRITE_OFF_REQUEST, ApprovalRequest, ApprovalStatus
from app.models.collections import (
    CollectionActivity,
    CollectionActivityType,
    CollectionCase,
    PromiseStatus,
)
from app.models.contract import ContractStatus, InstallmentContract
from app.models.write_off import (
    WriteOffEligibilityStatus,
    WriteOffReasonCode,
    WriteOffRequest,
    WriteOffRequestStatus,
    WriteOffType,
)
from app.services import approvals as approval_service
from app.services import collections as collections_service
from app.services import config_service as cfg
from app.services.audit import record_event
from app.services.config_service import ConfigService
from app.services.ecl_override import latest_assessment as ecl_latest_assessment
from app.services.errors import DomainError
from app.services.receivable import build_receivable

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _d(v):
    return None if v is None else Decimal(str(v))


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #
@dataclass
class EligibilityIndicator:
    id: str
    name: str
    category: str  # "structural" | "delinquency" | "credit_risk" | "collections" | "informational"
    result: str     # "satisfied" | "not_satisfied" | "unavailable"
    detail: str


@dataclass
class EligibilityResult:
    status: WriteOffEligibilityStatus
    indicators: list[EligibilityIndicator] = field(default_factory=list)
    dpd: int | None = None
    ecl_stage: int | None = None
    ecl_amount: Decimal | None = None
    provision_amount: Decimal | None = None
    collections_case_id: int | None = None
    collections_case_status: str | None = None


def _open_or_latest_case(db: Session, contract_id: int) -> CollectionCase | None:
    open_case = collections_service.get_open_case(db, contract_id)
    if open_case is not None:
        return open_case
    return db.execute(
        select(CollectionCase)
        .where(CollectionCase.contract_id == contract_id)
        .order_by(CollectionCase.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _collection_activity_count(db: Session, contract_id: int) -> int:
    return db.execute(
        select(func.count(CollectionActivity.id))
        .join(CollectionCase, CollectionActivity.collection_case_id == CollectionCase.id)
        .where(CollectionCase.contract_id == contract_id)
    ).scalar_one()


def _has_active_promise(db: Session, contract_id: int) -> bool:
    return (
        db.execute(
            select(CollectionActivity.id)
            .join(CollectionCase, CollectionActivity.collection_case_id == CollectionCase.id)
            .where(
                CollectionCase.contract_id == contract_id,
                CollectionActivity.activity_type == CollectionActivityType.promise_to_pay,
                CollectionActivity.promise_status == PromiseStatus.pending,
            )
        ).first()
        is not None
    )


def _has_broken_promise(db: Session, contract_id: int) -> bool:
    return (
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


# Indicator categories excluded from the ELIGIBLE/NOT_ELIGIBLE/UNAVAILABLE
# gate — shown for human context only, per "show them as unavailable / policy
# pending rather than inventing data" (legal / dispute / restructuring have no
# data source anywhere in this platform — confirmed by repository audit).
_INFORMATIONAL = "informational"


def evaluate_eligibility(db: Session, contract: InstallmentContract) -> EligibilityResult:
    """Explainable, informational — never blocks anything by itself. See the
    module docstring for the ELIGIBLE / NOT_ELIGIBLE / UNAVAILABLE contract."""
    indicators: list[EligibilityIndicator] = []

    indicators.append(
        EligibilityIndicator(
            id="contract_active",
            name="Contract is active",
            category="structural",
            result="satisfied" if contract.status == ContractStatus.active else "not_satisfied",
            detail=f"Contract status: {contract.status.value}",
        )
    )

    assessment = ecl_latest_assessment(db, contract.id)
    dpd = assessment.dpd if assessment else None
    ecl_stage = (assessment.final_stage if assessment.final_stage is not None else assessment.automated_stage) if assessment else None
    ecl_amount = assessment.final_ecl if assessment else None
    provision_amount = assessment.closing_provision if assessment else None

    dpd_threshold = ConfigService(db).get_int(cfg.KEY_WRITEOFF_ELIGIBILITY_DPD_THRESHOLD)
    if dpd is None:
        indicators.append(
            EligibilityIndicator(
                id="dpd_threshold", name=f"DPD >= {dpd_threshold}", category="delinquency",
                result="unavailable", detail="No ECL assessment exists yet for this contract.",
            )
        )
    else:
        indicators.append(
            EligibilityIndicator(
                id="dpd_threshold", name=f"DPD >= {dpd_threshold}", category="delinquency",
                result="satisfied" if dpd >= dpd_threshold else "not_satisfied",
                detail=f"DPD {dpd} vs threshold {dpd_threshold}",
            )
        )

    min_stage = ConfigService(db).get_int(cfg.KEY_WRITEOFF_ELIGIBILITY_MIN_ECL_STAGE)
    if ecl_stage is None:
        indicators.append(
            EligibilityIndicator(
                id="ecl_stage", name=f"ECL stage >= {min_stage}", category="credit_risk",
                result="unavailable", detail="No ECL assessment exists yet for this contract.",
            )
        )
    else:
        indicators.append(
            EligibilityIndicator(
                id="ecl_stage", name=f"ECL stage >= {min_stage}", category="credit_risk",
                result="satisfied" if ecl_stage >= min_stage else "not_satisfied",
                detail=f"Stage {ecl_stage} vs minimum {min_stage}",
            )
        )
    indicators.append(
        EligibilityIndicator(
            id="default_status", name="Stage 3 (credit-impaired / default)", category=_INFORMATIONAL,
            result=("unavailable" if ecl_stage is None else ("satisfied" if ecl_stage == 3 else "not_satisfied")),
            detail=f"ECL stage: {ecl_stage if ecl_stage is not None else 'no assessment'}",
        )
    )

    min_activities = ConfigService(db).get_int(cfg.KEY_WRITEOFF_MIN_COLLECTION_ACTIVITIES)
    activity_count = _collection_activity_count(db, contract.id)
    indicators.append(
        EligibilityIndicator(
            id="collections_exhausted", name=f"Collections engaged (>= {min_activities} logged activities)",
            category="collections",
            result="satisfied" if activity_count >= min_activities else "not_satisfied",
            detail=f"{activity_count} logged activit{'y' if activity_count == 1 else 'ies'} vs minimum {min_activities}",
        )
    )

    block_on_active_ptp = bool(ConfigService(db).get(cfg.KEY_WRITEOFF_BLOCK_IF_ACTIVE_PTP))
    has_active = _has_active_promise(db, contract.id)
    if block_on_active_ptp:
        indicators.append(
            EligibilityIndicator(
                id="no_active_promise_to_pay", name="No active (pending) Promise-to-Pay", category="collections",
                result="not_satisfied" if has_active else "satisfied",
                detail="An active promise-to-pay exists" if has_active else "No pending promise-to-pay",
            )
        )
    else:
        indicators.append(
            EligibilityIndicator(
                id="no_active_promise_to_pay", name="Active Promise-to-Pay (policy: not blocking)", category=_INFORMATIONAL,
                result="not_satisfied" if has_active else "satisfied",
                detail="An active promise-to-pay exists" if has_active else "No pending promise-to-pay",
            )
        )
    indicators.append(
        EligibilityIndicator(
            id="broken_promise_to_pay", name="Broken Promise-to-Pay on record", category=_INFORMATIONAL,
            result="satisfied" if _has_broken_promise(db, contract.id) else "not_satisfied",
            detail="Supporting context only — never gates eligibility by itself.",
        )
    )

    for ind_id, name in (
        ("legal_status", "Legal status"),
        ("dispute_status", "Dispute status"),
        ("restructuring_status", "Restructuring / forbearance status"),
    ):
        indicators.append(
            EligibilityIndicator(
                id=ind_id, name=name, category=_INFORMATIONAL, result="unavailable",
                detail="No such data is captured anywhere in this platform yet — policy pending.",
            )
        )

    case = _open_or_latest_case(db, contract.id)

    gating = [i for i in indicators if i.category != _INFORMATIONAL]
    if any(i.result == "unavailable" for i in gating):
        overall = WriteOffEligibilityStatus.unavailable
    elif all(i.result == "satisfied" for i in gating):
        overall = WriteOffEligibilityStatus.eligible
    else:
        overall = WriteOffEligibilityStatus.not_eligible

    return EligibilityResult(
        status=overall,
        indicators=indicators,
        dpd=dpd,
        ecl_stage=ecl_stage,
        ecl_amount=_d(ecl_amount),
        provision_amount=_d(provision_amount),
        collections_case_id=case.id if case else None,
        collections_case_status=case.status.value if case else None,
    )


# --------------------------------------------------------------------------- #
# Write-off request (maker side)
# --------------------------------------------------------------------------- #
def _reason(code: str) -> WriteOffReasonCode:
    try:
        return WriteOffReasonCode(code)
    except ValueError:
        raise DomainError(
            f"reason_code must be one of {[c.value for c in WriteOffReasonCode]}", status_code=422
        )


def _pending_request(db: Session, contract_id: int) -> WriteOffRequest | None:
    return db.execute(
        select(WriteOffRequest).where(
            WriteOffRequest.contract_id == contract_id,
            WriteOffRequest.status == WriteOffRequestStatus.pending,
        )
    ).scalar_one_or_none()


def request_write_off(db: Session, *, contract_id: int, actor_id: int, payload) -> ApprovalRequest:
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise DomainError("Contract not found", status_code=404)
    if contract.status != ContractStatus.active:
        raise DomainError(
            f"Write-off can only be requested against an active contract "
            f"(current status: {contract.status.value})",
            status_code=409,
        )
    if _pending_request(db, contract_id) is not None:
        raise DomainError(
            "A write-off request is already pending for this contract", status_code=409
        )

    write_off_type = WriteOffType(payload.write_off_type)
    if write_off_type == WriteOffType.partial and not bool(
        ConfigService(db).get(cfg.KEY_WRITEOFF_PARTIAL_ALLOWED)
    ):
        raise DomainError("Partial write-off is not permitted by current policy", status_code=409)

    eligibility = evaluate_eligibility(db, contract)
    is_exception = eligibility.status != WriteOffEligibilityStatus.eligible
    exception_justification = (payload.exception_justification or "").strip() or None
    if is_exception and not exception_justification:
        raise DomainError(
            f"This contract's write-off eligibility is {eligibility.status.value} — "
            "a normal request is blocked. Submit as a controlled exception with "
            "exception_justification to proceed.",
            status_code=422,
        )

    receivable = build_receivable(contract)
    snap_principal = _money(receivable.outstanding_principal)
    snap_profit = _money(receivable.outstanding_profit)
    snap_late_fee = _money(receivable.outstanding_late_fees)
    snap_other = _ZERO  # no "other charges" balance exists anywhere in this platform
    snap_total = snap_principal + snap_profit + snap_late_fee + snap_other

    if write_off_type == WriteOffType.full:
        req_principal, req_profit, req_late_fee, req_other = (
            snap_principal, snap_profit, snap_late_fee, snap_other,
        )
    else:
        req_principal = _money(getattr(payload, "requested_principal", None) or 0)
        req_profit = _money(getattr(payload, "requested_profit", None) or 0)
        req_late_fee = _money(getattr(payload, "requested_late_fee", None) or 0)
        req_other = _money(getattr(payload, "requested_other_charges", None) or 0)
        for label, requested, available in (
            ("principal", req_principal, snap_principal),
            ("profit", req_profit, snap_profit),
            ("late_fee", req_late_fee, snap_late_fee),
            ("other_charges", req_other, snap_other),
        ):
            if requested > available:
                raise DomainError(
                    f"Requested {label} write-off ({requested}) exceeds the eligible "
                    f"outstanding {label} ({available})",
                    status_code=422,
                )
        if req_principal + req_profit + req_late_fee + req_other <= _ZERO:
            raise DomainError(
                "A partial write-off must request a positive amount for at least one component",
                status_code=422,
            )

    wo = WriteOffRequest(
        contract_id=contract.id,
        customer_id=contract_owner_customer_id(db, contract),
        write_off_type=write_off_type,
        status=WriteOffRequestStatus.pending,
        reason_code=_reason(payload.reason_code),
        justification=payload.justification,
        evidence_ref=getattr(payload, "evidence_ref", None),
        comments=getattr(payload, "comments", None),
        eligibility_status=eligibility.status,
        eligibility_snapshot=[asdict(i) for i in eligibility.indicators],
        is_exception=is_exception,
        exception_justification=exception_justification,
        snapshot_principal_outstanding=snap_principal,
        snapshot_profit_outstanding=snap_profit,
        snapshot_late_fee_outstanding=snap_late_fee,
        snapshot_other_charges_outstanding=snap_other,
        snapshot_total_outstanding=snap_total,
        snapshot_dpd=eligibility.dpd,
        snapshot_ecl_stage=eligibility.ecl_stage,
        snapshot_ecl_amount=eligibility.ecl_amount,
        snapshot_provision_amount=eligibility.provision_amount,
        snapshot_collections_case_status=eligibility.collections_case_status,
        snapshot_collections_case_id=eligibility.collections_case_id,
        requested_principal=req_principal,
        requested_profit=req_profit,
        requested_late_fee=req_late_fee,
        requested_other_charges=req_other,
        requested_by=actor_id,
    )
    db.add(wo)
    db.flush()

    record_event(
        db, user_id=actor_id, action="writeoff.eligibility_evaluated",
        entity_type="write_off_request", entity_id=wo.id,
        after={
            "contract_id": contract.id,
            "eligibility_status": eligibility.status.value,
            "is_exception": is_exception,
        },
    )

    req = approval_service.create_request(
        db,
        action_type=ACTION_WRITE_OFF_REQUEST,
        entity_type="write_off_request",
        entity_id=wo.id,
        requested_by=actor_id,
        payload={
            "contract_id": contract.id,
            "write_off_request_id": wo.id,
            "write_off_type": write_off_type.value,
            "reason_code": payload.reason_code,
            "justification": payload.justification,
            "eligibility_status": eligibility.status.value,
            "is_exception": is_exception,
            "exception_justification": exception_justification,
            "snapshot_total_outstanding": float(snap_total),
            "requested_total": float(req_principal + req_profit + req_late_fee + req_other),
            "requested_principal": float(req_principal),
            "requested_profit": float(req_profit),
            "requested_late_fee": float(req_late_fee),
            "requested_other_charges": float(req_other),
        },
    )
    wo.approval_request_id = req.id
    db.flush()

    record_event(
        db, user_id=actor_id, action="writeoff.requested",
        entity_type="write_off_request", entity_id=wo.id,
        after={
            "contract_id": contract.id,
            "write_off_type": write_off_type.value,
            "is_exception": is_exception,
            "approval_request_id": req.id,
        },
    )
    return req


def cancel_request(db: Session, request_id: int, *, actor_id: int, reason: str | None = None) -> WriteOffRequest:
    """Withdraw a still-pending request — mirrors ecl_override.py::cancel_override."""
    wo = db.get(WriteOffRequest, request_id)
    if wo is None:
        raise DomainError("Write-off request not found", status_code=404)
    if wo.status != WriteOffRequestStatus.pending:
        raise DomainError(f"Cannot cancel a request that is {wo.status.value}", status_code=409)

    if wo.approval_request_id:
        req = db.get(ApprovalRequest, wo.approval_request_id)
        if req is not None and req.status == ApprovalStatus.pending:
            req.status = ApprovalStatus.rejected
            req.decided_by = actor_id
            req.decided_at = _utcnow()
            req.decision_notes = reason or "Write-off request cancelled by requester"

    wo.status = WriteOffRequestStatus.cancelled
    wo.comments = ((wo.comments or "") + f"\n[cancelled] {reason or ''}").strip()
    db.flush()

    record_event(
        db, user_id=actor_id, action="writeoff.cancelled",
        entity_type="write_off_request", entity_id=wo.id,
        before={"status": "pending"}, after={"status": "cancelled", "reason": reason},
    )
    return wo
