from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.contract import InstallmentContract
from app.schemas.payment import (
    AssessOverdueRequest,
    AssessOverdueResult,
    PaymentCreate,
    PaymentOut,
    PaymentResult,
    ReceivableOut,
)
from app.services import overdue as overdue_service
from app.services import payments as payment_service
from app.services.errors import DomainError
from app.services.receivable import build_receivable

router = APIRouter(tags=["payments & receivable"])


def _get_contract(db: Session, contract_id: int) -> InstallmentContract:
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.post("/contracts/{contract_id}/payments", response_model=PaymentResult)
def record_payment(
    contract_id: int, payload: PaymentCreate, db: Session = Depends(get_db)
):
    contract = _get_contract(db, contract_id)
    try:
        outcome = payment_service.record_payment(
            db,
            contract,
            amount=payload.amount,
            external_reference=payload.external_reference,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    if not outcome.replayed:
        db.commit()
    db.refresh(outcome.payment)
    return PaymentResult(
        replayed=outcome.replayed,
        payment=PaymentOut.model_validate(outcome.payment),
    )


@router.get("/contracts/{contract_id}/receivable", response_model=ReceivableOut)
def get_receivable(contract_id: int, db: Session = Depends(get_db)):
    contract = _get_contract(db, contract_id)
    return build_receivable(contract)


@router.post("/jobs/assess-overdue", response_model=AssessOverdueResult)
def assess_overdue(
    payload: AssessOverdueRequest | None = None, db: Session = Depends(get_db)
):
    as_of = payload.as_of if payload else None
    summary = overdue_service.assess_overdue(db, as_of=as_of)
    db.commit()
    return AssessOverdueResult(
        as_of=summary.as_of,
        grace_period_days=summary.grace_period_days,
        installments_marked_overdue=summary.installments_marked_overdue,
        late_fees_assessed=summary.late_fees_assessed,
        total_late_fee_amount=float(summary.total_late_fee_amount),
        charges=summary.charges,
    )
