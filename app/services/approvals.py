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

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import (
    ACTION_COA_ACCOUNT_CREATE,
    ACTION_COA_ACCOUNT_DEACTIVATE,
    ACTION_COA_ACCOUNT_UPDATE,
    ACTION_COA_MAPPING_CREATE,
    ACTION_COA_MAPPING_DEACTIVATE,
    ACTION_COA_MAPPING_UPDATE,
    ACTION_CONFIG_UPDATE,
    ACTION_ECL_CONFIG_UPDATE,
    ACTION_ECL_PARAMETER_OVERRIDE,
    ACTION_ECL_STAGE_OVERRIDE,
    ACTION_GATEWAY_RECON_RESOLVE,
    ACTION_LATE_FEE_WAIVE,
    ACTION_RECON_MANUAL_MATCH,
    ACTION_SETTLEMENT_REBATE,
    ACTION_WRITE_OFF_REQUEST,
    ApprovalRequest,
    ApprovalStatus,
)
from app.models.accounting import AccountingEventType
from app.models.contract import InstallmentContract
from app.models.ecl import ECLOverride, ECLOverrideStatus
from app.models.gateway_settlement import ReconciliationItem
from app.models.gl import (
    AccountType,
    AmountSource,
    ChartOfAccount,
    EventAccountMapping,
    EventAccountMappingLine,
    EventClassification,
    NormalBalance,
    PostingSide,
)
from app.services import ecl_config
from app.models.ledger import LedgerEntryType, LedgerRelatedAction
from app.models.payment import LateFeeCharge, LateFeeStatus, Payment
from app.models.reconciliation import ReconciliationException
from app.models.write_off import WriteOffRequest, WriteOffRequestStatus
from app.services import accounting
from app.services import closure as closure_service
from app.services import gateway_reconciliation as gateway_recon_service
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
    else:
        _on_reject(db, approval, actor_id=decider_id)

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
                source_table="late_fee_charge",
                source_id=charge.id,
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

    if approval.action_type in (ACTION_ECL_STAGE_OVERRIDE, ACTION_ECL_PARAMETER_OVERRIDE):
        ov = db.get(ECLOverride, int(approval.entity_id))
        if ov is None:
            raise DomainError("ECL override no longer exists", status_code=409)
        if ov.status != ECLOverrideStatus.pending:
            raise DomainError(
                f"ECL override {ov.id} is already {ov.status.value}", status_code=409
            )
        today = _utcnow().date()
        in_window = ov.effective_from <= today and (
            ov.effective_to is None or today <= ov.effective_to
        )
        ov.status = ECLOverrideStatus.active if in_window else ECLOverrideStatus.approved
        ov.approved_by = actor_id
        ov.decided_at = _utcnow()
        record_event(
            db,
            user_id=actor_id,
            action="ecl.override_approved",
            entity_type="ecl_override",
            entity_id=ov.id,
            before={"status": "PENDING"},
            after={
                "status": ov.status.value,
                "override_type": ov.override_type.value,
                "approved_value": ov.approved_value,
                "approval_request_id": approval.id,
            },
        )

        # A contract can only ever be governed by one ACTIVE/APPROVED override
        # at a time. Nothing upstream blocks requesting a new one while an
        # older one still governs (only a *pending* request is blocked — see
        # ecl_override.py::_guards) — so this newly-approved override
        # supersedes any other still-active/approved override on the same
        # contract, reusing the existing CANCELLED status ("withdrawn /
        # superseded" per its own docstring) and the existing superseded_by
        # pointer column, rather than adding a new status.
        siblings = db.execute(
            select(ECLOverride).where(
                ECLOverride.contract_id == ov.contract_id,
                ECLOverride.id != ov.id,
                ECLOverride.status.in_(
                    [ECLOverrideStatus.active, ECLOverrideStatus.approved]
                ),
            )
        ).scalars().all()
        for old in siblings:
            before_status = old.status.value
            old.status = ECLOverrideStatus.cancelled
            old.superseded_by = ov.id
            old.comments = (
                (old.comments or "") + f"\n[superseded by override #{ov.id}]"
            ).strip()
            record_event(
                db,
                user_id=actor_id,
                action="ecl.override_superseded",
                entity_type="ecl_override",
                entity_id=old.id,
                before={"status": before_status},
                after={"status": "CANCELLED", "superseded_by": ov.id},
            )
        return

    if approval.action_type == ACTION_GATEWAY_RECON_RESOLVE:
        item = db.get(ReconciliationItem, int(approval.entity_id))
        if item is None:
            raise DomainError("Reconciliation item no longer exists", status_code=409)
        p = approval.payload or {}
        gateway_recon_service.apply_resolution(
            db, item, actor_id=actor_id, reason=p.get("reason", ""), comments=p.get("comments"),
        )
        return

    if approval.action_type == ACTION_WRITE_OFF_REQUEST:
        wo = db.get(WriteOffRequest, int(approval.entity_id))
        if wo is None:
            raise DomainError("Write-off request no longer exists", status_code=409)
        if wo.status != WriteOffRequestStatus.pending:
            raise DomainError(
                f"Write-off request {wo.id} is already {wo.status.value}", status_code=409
            )
        # Deliberately a pure status transition — NO balance mutation, ledger
        # entry, accounting event, or contract/case closure here. Approval
        # and financial execution are two distinct, separately-controlled
        # steps for write-off (mirrors ECLRun's own COMPLETED -> POSTED
        # split) — execution is a later, explicit step
        # (services/write_off.py's execution function, not yet built).
        wo.status = WriteOffRequestStatus.approved
        wo.approved_by = actor_id
        wo.decided_at = _utcnow()
        record_event(
            db,
            user_id=actor_id,
            action="writeoff.approved",
            entity_type="write_off_request",
            entity_id=wo.id,
            before={"status": "PENDING"},
            after={
                "status": "APPROVED",
                "write_off_type": wo.write_off_type.value,
                "approval_request_id": approval.id,
            },
        )
        return

    if approval.action_type == ACTION_ECL_CONFIG_UPDATE:
        payload = approval.payload or {}
        try:
            new_cfg = ecl_config.activate_version(
                db,
                changes=payload["changes"],
                actor_id=actor_id,
                notes=payload.get("notes"),
            )
        except ValueError as exc:
            raise DomainError(str(exc), status_code=409)
        record_event(
            db,
            user_id=actor_id,
            action="ecl.config_activated",
            entity_type="ecl_configuration",
            entity_id=new_cfg.version,
            after={
                "version": new_cfg.version,
                "changes": payload["changes"],
                "approval_request_id": approval.id,
            },
        )
        return

    if approval.action_type == ACTION_COA_ACCOUNT_CREATE:
        p = approval.payload or {}
        existing = db.execute(
            select(ChartOfAccount).where(ChartOfAccount.account_code == p["account_code"])
        ).scalar_one_or_none()
        if existing is not None:
            raise DomainError(
                f"Account code {p['account_code']!r} was created by another request "
                "while this one was pending",
                status_code=409,
            )
        account = ChartOfAccount(
            account_code=p["account_code"],
            account_name=p["account_name"],
            account_type=AccountType(p["account_type"]),
            normal_balance=NormalBalance(p["normal_balance"]),
            is_active=True,
            is_demo=bool(p.get("is_demo", True)),
            description=p["description"],
            created_by=approval.requested_by,
            approved_at=_utcnow(),
            approved_by=actor_id,
        )
        db.add(account)
        db.flush()
        record_event(
            db, user_id=actor_id, action="accounting.account_created",
            entity_type="chart_of_account", entity_id=account.id,
            after={"account_code": account.account_code, "approval_request_id": approval.id},
        )
        return

    if approval.action_type == ACTION_COA_ACCOUNT_UPDATE:
        p = approval.payload or {}
        account = db.get(ChartOfAccount, int(approval.entity_id))
        if account is None:
            raise DomainError("Account no longer exists", status_code=409)
        changes = p.get("changes", {})
        before = {
            "account_code": account.account_code, "account_name": account.account_name,
            "account_type": account.account_type.value, "normal_balance": account.normal_balance.value,
            "description": account.description,
        }
        if "account_code" in changes:
            dup = db.execute(
                select(ChartOfAccount).where(ChartOfAccount.account_code == changes["account_code"])
            ).scalar_one_or_none()
            if dup is not None and dup.id != account.id:
                raise DomainError(
                    f"Account code {changes['account_code']!r} was taken by another "
                    "request while this one was pending",
                    status_code=409,
                )
            account.account_code = changes["account_code"]
        if "account_name" in changes:
            account.account_name = changes["account_name"]
        if "account_type" in changes:
            account.account_type = AccountType(changes["account_type"])
        if "normal_balance" in changes:
            account.normal_balance = NormalBalance(changes["normal_balance"])
        if "description" in changes:
            account.description = changes["description"]
        account.approved_at = _utcnow()
        account.approved_by = actor_id
        db.flush()
        record_event(
            db, user_id=actor_id, action="accounting.account_updated",
            entity_type="chart_of_account", entity_id=account.id,
            before=before, after={"changes": changes, "approval_request_id": approval.id},
        )
        return

    if approval.action_type == ACTION_COA_ACCOUNT_DEACTIVATE:
        account = db.get(ChartOfAccount, int(approval.entity_id))
        if account is None:
            raise DomainError("Account no longer exists", status_code=409)
        if not account.is_active:
            raise DomainError(f"Account {account.account_code} is already inactive", status_code=409)
        # Re-check at approval time too — a mapping could have been activated
        # against this account while the deactivation was pending.
        still_used = db.execute(
            select(EventAccountMappingLine.id)
            .join(EventAccountMapping, EventAccountMappingLine.mapping_id == EventAccountMapping.id)
            .where(
                EventAccountMappingLine.account_id == account.id,
                EventAccountMappingLine.is_active.is_(True),
                EventAccountMapping.is_active.is_(True),
            ).limit(1)
        ).first()
        if still_used is not None:
            raise DomainError(
                f"Account {account.account_code} is now referenced by an active mapping "
                "— cannot deactivate",
                status_code=409,
            )
        account.is_active = False
        db.flush()
        record_event(
            db, user_id=actor_id, action="accounting.account_deactivated",
            entity_type="chart_of_account", entity_id=account.id,
            before={"is_active": True}, after={"is_active": False, "approval_request_id": approval.id},
        )
        return

    if approval.action_type in (ACTION_COA_MAPPING_CREATE, ACTION_COA_MAPPING_UPDATE):
        p = approval.payload or {}
        event_type = AccountingEventType(p["account_event_type"])
        current = db.execute(
            select(EventAccountMapping).where(
                EventAccountMapping.account_event_type == event_type,
                EventAccountMapping.is_active.is_(True),
            )
        ).scalar_one_or_none()
        expected_from_version = p.get("from_version")
        current_version = current.version if current is not None else None
        if current_version != expected_from_version:
            raise DomainError(
                f"The active mapping for {event_type.value} moved to version "
                f"{current_version} while this proposal (based on version "
                f"{expected_from_version}) was pending — reject and re-propose",
                status_code=409,
            )
        new_mapping = EventAccountMapping(
            account_event_type=event_type,
            version=p["version"],
            classification=EventClassification(p["classification"]),
            effective_from=date.fromisoformat(p["effective_from"]),
            effective_to=None,
            is_active=True,
            is_demo=bool(p.get("is_demo", True)),
            description=p["description"],
            change_reason=p.get("change_reason"),
            created_by=approval.requested_by,
            approved_at=_utcnow(),
            approved_by=actor_id,
            approval_request_id=approval.id,
        )
        db.add(new_mapping)
        db.flush()
        for line in p.get("lines", []):
            db.add(
                EventAccountMappingLine(
                    mapping_id=new_mapping.id,
                    line_sequence=line["line_sequence"],
                    posting_side=PostingSide(line["posting_side"]),
                    account_id=line["account_id"],
                    amount_source=AmountSource(line["amount_source"]),
                    reverse_on_negative=bool(line.get("reverse_on_negative", False)),
                    description=line.get("description"),
                )
            )
        if current is not None:
            current.is_active = False
            current.effective_to = date.fromisoformat(p["effective_from"])
        db.flush()
        record_event(
            db, user_id=actor_id,
            action="accounting.mapping_activated",
            entity_type="event_account_mapping", entity_id=new_mapping.id,
            after={
                "account_event_type": event_type.value, "version": new_mapping.version,
                "classification": new_mapping.classification.value,
                "approval_request_id": approval.id,
            },
        )
        return

    if approval.action_type == ACTION_COA_MAPPING_DEACTIVATE:
        p = approval.payload or {}
        mapping = db.get(EventAccountMapping, int(p["mapping_id"]))
        if mapping is None or not mapping.is_active:
            raise DomainError(
                "The mapping this request targeted is no longer the active version",
                status_code=409,
            )
        mapping.is_active = False
        mapping.effective_to = _utcnow().date()
        db.flush()
        record_event(
            db, user_id=actor_id, action="accounting.mapping_deactivated",
            entity_type="event_account_mapping", entity_id=mapping.id,
            before={"is_active": True},
            after={"is_active": False, "reason": p.get("reason"), "approval_request_id": approval.id},
        )
        return

    raise DomainError(
        f"Don't know how to execute action_type '{approval.action_type}'",
        status_code=409,
    )


def _on_reject(db: Session, approval: ApprovalRequest, *, actor_id: int) -> None:
    """Cleanup hooks for rejected requests (most action types need none)."""
    if approval.action_type in (ACTION_ECL_STAGE_OVERRIDE, ACTION_ECL_PARAMETER_OVERRIDE):
        ov = db.get(ECLOverride, int(approval.entity_id))
        if ov is not None and ov.status == ECLOverrideStatus.pending:
            ov.status = ECLOverrideStatus.rejected
            ov.approved_by = actor_id
            ov.decided_at = _utcnow()
            record_event(
                db,
                user_id=actor_id,
                action="ecl.override_rejected",
                entity_type="ecl_override",
                entity_id=ov.id,
                before={"status": "PENDING"},
                after={"status": "REJECTED", "approval_request_id": approval.id},
            )

    if approval.action_type == ACTION_WRITE_OFF_REQUEST:
        wo = db.get(WriteOffRequest, int(approval.entity_id))
        if wo is not None and wo.status == WriteOffRequestStatus.pending:
            wo.status = WriteOffRequestStatus.rejected
            wo.approved_by = actor_id
            wo.decided_at = _utcnow()
            record_event(
                db,
                user_id=actor_id,
                action="writeoff.rejected",
                entity_type="write_off_request",
                entity_id=wo.id,
                before={"status": "PENDING"},
                after={"status": "REJECTED", "approval_request_id": approval.id},
            )
