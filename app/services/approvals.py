"""Generic maker-checker approval workflow.

One rule matters above role checks: **the decider must not be the requester**
(`decided_by != requested_by`), enforced here in the service layer, not just by
convention. Violating it is a 409 whatever the caller's role.

Action types that run through it:
  * ``late_fee.waive``               — on approval, LateFeeCharge.status -> waived
  * ``config.update``                — on approval, ConfigService applies the new
                                       value and the ``config.updated`` audit
                                       event fires, referencing the approval
  * ``reconciliation.manual_match``  — P0-5
  * ``contract.settlement_rebate``   — BDR item #7: a staff-granted
                                       early-settlement profit rebate that
                                       deviates from the config default. On
                                       approval the settlement quote is
                                       recomputed and the contract settled.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import (
    ACTION_CONFIG_UPDATE,
    ACTION_LATE_FEE_WAIVE,
    ACTION_RECON_MANUAL_MATCH,
    ACTION_SETTLEMENT_REBATE,
    ApprovalRequest,
    ApprovalStatus,
)
from app.models.accounting import AccountingEventType
from app.models.contract import InstallmentContract
from app.models.ledger import LedgerEntryType, LedgerRelatedAction
from app.models.payment import LateFeeCharge, LateFeeStatus, Payment
from app.models.reconciliation import ReconciliationException
from app.services import accounting
from app.services import closure as closure_service
from app.services import ledger as ledger_service
from app.services import reconciliation as recon_service
from app.services.audit import record_event
from app.services.config_service import ConfigService
from app.services.errors import DomainError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_request(
    db: Session,
    *,
    action_type: str,
    entity_type: str,
    entity_id: object,
    requested_by: int,
    payload: dict,
) -> ApprovalRequest:
    req = ApprovalRequest(
        action_type=action_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        requested_by=requested_by,
        payload=payload,
        status=ApprovalStatus.pending,
    )
    db.add(req)
    db.flush()
    record_event(
        db,
        user_id=requested_by,
        action="approval.requested",
        entity_type="approval_request",
        entity_id=req.id,
        after={"action_type": action_type, "target": f"{entity_type}:{entity_id}"},
    )
    return req


def pending_request_for(
    db: Session, action_type: str, entity_id: object
) -> ApprovalRequest | None:
    return db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.action_type == action_type,
            ApprovalRequest.entity_id == str(entity_id),
            ApprovalRequest.status == ApprovalStatus.pending,
        )
    ).scalar_one_or_none()


def decide(
    db: Session,
    approval: ApprovalRequest,
    *,
    decider_id: int,
    approve: bool,
    notes: str | None = None,
) -> ApprovalRequest:
    if approval.status != ApprovalStatus.pending:
        raise DomainError(
            f"Approval request {approval.id} is already {approval.status.value}",
            status_code=409,
        )
    if decider_id == approval.requested_by:
        raise DomainError(
            "You cannot approve or reject your own request "
            "(decided_by must differ from requested_by)",
            status_code=409,
        )

    approval.decided_by = decider_id
    approval.decided_at = _utcnow()
    approval.decision_notes = notes
    approval.status = ApprovalStatus.approved if approve else ApprovalStatus.rejected

    if approve:
        _execute(db, approval, actor_id=decider_id)

    record_event(
        db,
        user_id=decider_id,
        action="approval.approved" if approve else "approval.rejected",
        entity_type="approval_request",
        entity_id=approval.id,
        before={"status": "pending"},
        after={"status": approval.status.value, "notes": notes},
    )
    db.flush()
    return approval


def _execute(db: Session, approval: ApprovalRequest, *, actor_id: int) -> None:
    if approval.action_type == ACTION_LATE_FEE_WAIVE:
        charge = db.get(LateFeeCharge, int(approval.entity_id))
        if charge is None:
            raise DomainError("Late fee charge no longer exists", status_code=409)
        waived_amount = (charge.amount or Decimal("0")) - (charge.amount_paid or Decimal("0"))
        charge.status = LateFeeStatus.waived
        record_event(
            db,
            user_id=actor_id,
            action="late_fee.waived",
            entity_type="late_fee_charge",
            entity_id=charge.id,
            before={"status": "assessed"},
            after={"status": "waived", "approval_request_id": approval.id},
        )
        # --- dual-write to the immutable ledger (Phase 1) ---
        if waived_amount > Decimal("0"):
            ledger_service.record_entry(
                db,
                contract_id=charge.contract_id,
                entry_type=LedgerEntryType.late_fee_waived,
                amount=waived_amount,
                related_action=LedgerRelatedAction.waiver,
                reference_type="approval_request",
                reference_id=approval.id,
                created_by=actor_id,
            )
        # --- accounting-event boundary (additive) ---
        contract = db.get(InstallmentContract, charge.contract_id)
        if contract is not None:
            accounting.emit(
                db,
                event_type=AccountingEventType.late_fee_waived,
                event_reference=f"late-fee-waived-{charge.id}",
                contract=contract,
                amount=waived_amount,
            )
        return

    if approval.action_type == ACTION_CONFIG_UPDATE:
        key = approval.entity_id
        payload = approval.payload or {}
        service = ConfigService(db)
        try:
            before_value = service.get_raw(key).value
        except KeyError:
            raise DomainError(f"Unknown config parameter '{key}'", status_code=409)
        param = service.set(
            key,
            payload["new_value"],
            value_type=payload.get("value_type"),
            description=payload.get("description"),
        )
        record_event(
            db,
            user_id=actor_id,
            action="config.updated",
            entity_type="config_parameter",
            entity_id=key,
            before={"value": before_value},
            after={"value": param.value, "approval_request_id": approval.id},
        )
        return

    if approval.action_type == ACTION_RECON_MANUAL_MATCH:
        exception = db.get(ReconciliationException, int(approval.entity_id))
        if exception is None:
            raise DomainError(
                "Reconciliation exception no longer exists", status_code=409
            )
        payment = db.get(Payment, int(approval.payload["payment_id"]))
        if payment is None:
            raise DomainError("Target payment no longer exists", status_code=409)
        recon_service.apply_manual_match(
            db, exception, payment, actor_id=actor_id
        )
        return

    if approval.action_type == ACTION_SETTLEMENT_REBATE:
        # BDR item #7 — a staff-granted early-settlement profit rebate that
        # deviates from the config default. On approval the quote is
        # recomputed server-side (never trusting a stale amount) with the
        # exact rebate that was requested, then settled — so every existing
        # settlement hook (ledger, accounting event, audit) fires exactly as
        # it does for a normal settlement.
        contract = db.get(InstallmentContract, int(approval.entity_id))
        if contract is None:
            raise DomainError("Contract no longer exists", status_code=409)
        p = approval.payload or {}
        quote = closure_service.build_settlement_quote(
            db,
            contract,
            requested_rebate_pct=p.get("requested_rebate_pct"),
            requested_rebate_amount=p.get("requested_rebate_amount"),
        )
        closure = closure_service.settle_contract(
            db,
            contract,
            amount=float(quote.final_payoff_amount),
            external_reference=p["external_reference"],
            actor_id=actor_id,
            requested_rebate_pct=p.get("requested_rebate_pct"),
            requested_rebate_amount=p.get("requested_rebate_amount"),
        )
        record_event(
            db,
            user_id=actor_id,
            action="contract.settled",
            entity_type="installment_contract",
            entity_id=contract.id,
            before={"status": "active"},
            after={
                "status": contract.status.value,
                "final_payoff_amount": float(quote.final_payoff_amount),
                "profit_rebate_amount": float(quote.profit_rebate_amount),
                "approval_request_id": approval.id,
            },
        )
        return

    raise DomainError(
        f"Don't know how to execute action_type '{approval.action_type}'",
        status_code=409,
    )
