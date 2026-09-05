from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import (
    authorize_owner_or_roles,
    contract_owner_customer_id,
    get_current_user,
    require_roles,
)
from app.core.database import get_db
from app.models.approval import ACTION_SETTLEMENT_REBATE
from app.models.contract import InstallmentContract
from app.models.user import User, UserRole
from app.schemas.approval import ApprovalRequestOut
from app.schemas.closure import (
    CancellationResultOut,
    CloseRequest,
    ContractClosureOut,
    ReturnResultOut,
    SettleRequest,
    SettleResult,
    SettlementQuoteOut,
)
from app.services import approvals as approval_service
from app.services import closure as closure_service
from app.services.audit import record_event
from app.services.errors import DomainError

router = APIRouter(tags=["closure (settlement / cancellation / return)"])

_CLOSURE_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)


def _get_contract(db: Session, contract_id: int) -> InstallmentContract:
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


def _domain(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _quote_out(quote) -> SettlementQuoteOut:
    # pydantic coerces the Decimal fields to float
    return SettlementQuoteOut(**dataclasses.asdict(quote))


@router.get(
    "/contracts/{contract_id}/settlement-quote", response_model=SettlementQuoteOut
)
def settlement_quote(
    contract_id: int,
    db: Session = Depends(get_db),
    requested_rebate_pct: float | None = Query(default=None, ge=0, le=1),
    requested_rebate_amount: float | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
):
    contract = _get_contract(db, contract_id)
    authorize_owner_or_roles(
        db, user,
        staff_roles=_CLOSURE_ROLES,
        owner_customer_id=contract_owner_customer_id(db, contract),
    )
    try:
        quote = closure_service.build_settlement_quote(
            db,
            contract,
            requested_rebate_pct=requested_rebate_pct,
            requested_rebate_amount=requested_rebate_amount,
        )
    except DomainError as exc:
        raise _domain(exc)
    return _quote_out(quote)


@router.post("/contracts/{contract_id}/settle", response_model=SettleResult)
def settle(
    contract_id: int,
    payload: SettleRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_CLOSURE_ROLES)),
):
    contract = _get_contract(db, contract_id)
    status_before = contract.status.value
    try:
        quote = closure_service.build_settlement_quote(
            db,
            contract,
            requested_rebate_pct=payload.requested_rebate_pct,
            requested_rebate_amount=payload.requested_rebate_amount,
        )
    except DomainError as exc:
        raise _domain(exc)

    # BDR item #7 — a rebate that deviates from the config default is a
    # financial decision that always needs a second approver. Don't execute:
    # create a pending ApprovalRequest (same generic flow as the config-change
    # and late-fee-waiver approvals) and let a different approver run it.
    if quote.is_deviation:
        if approval_service.pending_request_for(
            db, ACTION_SETTLEMENT_REBATE, contract.id
        ):
            raise HTTPException(
                status_code=409,
                detail="A settlement-rebate request is already pending for this contract",
            )
        req = approval_service.create_request(
            db,
            action_type=ACTION_SETTLEMENT_REBATE,
            entity_type="installment_contract",
            entity_id=contract.id,
            requested_by=actor.id,
            payload={
                "requested_rebate_pct": payload.requested_rebate_pct,
                "requested_rebate_amount": payload.requested_rebate_amount,
                "external_reference": payload.external_reference,
                # informational only — recomputed server-side at approval time
                "quoted_payoff_amount": float(quote.final_payoff_amount),
                "quoted_rebate_amount": float(quote.profit_rebate_amount),
            },
        )
        db.commit()
        db.refresh(req)
        return SettleResult(
            contract_id=contract.id,
            status="pending_approval",
            quote=_quote_out(quote),
            closure=None,
            pending_approval=ApprovalRequestOut.model_validate(req),
        )

    try:
        closure = closure_service.settle_contract(
            db,
            contract,
            amount=payload.amount,
            external_reference=payload.external_reference,
            actor_id=actor.id,
            requested_rebate_pct=payload.requested_rebate_pct,
            requested_rebate_amount=payload.requested_rebate_amount,
        )
    except DomainError as exc:
        raise _domain(exc)
    record_event(
        db,
        user_id=actor.id,
        action="contract.settled",
        entity_type="installment_contract",
        entity_id=contract.id,
        before={"status": status_before},
        after={
            "status": contract.status.value,
            "final_payoff_amount": float(quote.final_payoff_amount),
            "profit_rebate_amount": float(quote.profit_rebate_amount),
        },
    )
    db.commit()
    db.refresh(contract)
    return SettleResult(
        contract_id=contract.id,
        status=contract.status.value,
        quote=_quote_out(quote),
        closure=ContractClosureOut.model_validate(closure),
    )


@router.post("/contracts/{contract_id}/cancel", response_model=CancellationResultOut)
def cancel(
    contract_id: int,
    payload: CloseRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_CLOSURE_ROLES)),
):
    contract = _get_contract(db, contract_id)
    status_before = contract.status.value
    try:
        result = closure_service.cancel_contract(
            db, contract, notes=payload.notes if payload else None, actor_id=actor.id
        )
    except DomainError as exc:
        raise _domain(exc)
    record_event(
        db,
        user_id=actor.id,
        action="contract.cancelled",
        entity_type="installment_contract",
        entity_id=contract.id,
        before={"status": status_before},
        after={
            "status": contract.status.value,
            "down_payment_refund": float(result.down_payment_refund),
        },
    )
    db.commit()
    db.refresh(contract)
    return CancellationResultOut(
        contract_id=contract.id,
        status=contract.status.value,
        down_payment_amount=float(result.down_payment_amount),
        refund_pct=float(result.refund_pct),
        down_payment_refund=float(result.down_payment_refund),
        closure=ContractClosureOut.model_validate(result.closure),
    )


@router.post("/contracts/{contract_id}/return", response_model=ReturnResultOut)
def return_contract(
    contract_id: int,
    payload: CloseRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_CLOSURE_ROLES)),
):
    contract = _get_contract(db, contract_id)
    status_before = contract.status.value
    try:
        result = closure_service.return_contract(
            db, contract, notes=payload.notes if payload else None, actor_id=actor.id
        )
    except DomainError as exc:
        raise _domain(exc)
    record_event(
        db,
        user_id=actor.id,
        action="contract.returned",
        entity_type="installment_contract",
        entity_id=contract.id,
        before={"status": status_before},
        after={
            "status": contract.status.value,
            "net_adjustment": float(result.net_adjustment),
            "ownership_transfers_on_delivery": result.ownership_transfers_on_delivery,
        },
    )
    db.commit()
    db.refresh(contract)
    return ReturnResultOut(
        contract_id=contract.id,
        status=contract.status.value,
        ownership_transfers_on_delivery=result.ownership_transfers_on_delivery,
        down_payment_amount=float(result.down_payment_amount),
        refund_pct=float(result.refund_pct),
        down_payment_refund=float(result.down_payment_refund),
        settlement_shape_payoff=float(result.quote.final_payoff_amount),
        net_adjustment=float(result.net_adjustment),
        closure=ContractClosureOut.model_validate(result.closure),
    )
