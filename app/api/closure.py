from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.contract import InstallmentContract
from app.schemas.closure import (
    CancellationResultOut,
    CloseRequest,
    ContractClosureOut,
    ReturnResultOut,
    SettleRequest,
    SettleResult,
    SettlementQuoteOut,
)
from app.services import closure as closure_service
from app.services.errors import DomainError

router = APIRouter(tags=["closure (settlement / cancellation / return)"])


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
def settlement_quote(contract_id: int, db: Session = Depends(get_db)):
    contract = _get_contract(db, contract_id)
    try:
        quote = closure_service.build_settlement_quote(db, contract)
    except DomainError as exc:
        raise _domain(exc)
    return _quote_out(quote)


@router.post("/contracts/{contract_id}/settle", response_model=SettleResult)
def settle(contract_id: int, payload: SettleRequest, db: Session = Depends(get_db)):
    contract = _get_contract(db, contract_id)
    try:
        quote = closure_service.build_settlement_quote(db, contract)
        closure = closure_service.settle_contract(
            db,
            contract,
            amount=payload.amount,
            external_reference=payload.external_reference,
        )
    except DomainError as exc:
        raise _domain(exc)
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
):
    contract = _get_contract(db, contract_id)
    try:
        result = closure_service.cancel_contract(
            db, contract, notes=payload.notes if payload else None
        )
    except DomainError as exc:
        raise _domain(exc)
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
):
    contract = _get_contract(db, contract_id)
    try:
        result = closure_service.return_contract(
            db, contract, notes=payload.notes if payload else None
        )
    except DomainError as exc:
        raise _domain(exc)
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
