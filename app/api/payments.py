from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import (
    authorize_owner_or_roles,
    contract_owner_customer_id,
    get_current_user,
    require_roles,
)
from app.core.database import get_db
from app.models.contract import InstallmentContract
from app.models.user import User, UserRole
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
from app.services.audit import record_event
from app.services.errors import DomainError
from app.services.receivable import build_receivable

router = APIRouter(tags=["payments & receivable"])

_RECEIVABLE_STAFF_ROLES = (
    UserRole.finance_officer,
    UserRole.credit_manager,
    UserRole.admin,
)


def _get_contract(db: Session, contract_id: int) -> InstallmentContract:
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.post("/contracts/{contract_id}/payments", response_model=PaymentResult)
def record_payment(
    contract_id: int,
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_roles(
            UserRole.sales_employee,
            UserRole.finance_officer,
            UserRole.customer,
            UserRole.admin,
        )
    ),
):
    contract = _get_contract(db, contract_id)
    try:
        outcome = payment_service.record_payment(
            db,
            contract,
            amount=payload.amount,
            external_reference=payload.external_reference,
            actor_id=actor.id,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    if not outcome.replayed:
        record_event(
            db,
            user_id=actor.id,
            action="payment.recorded",
            entity_type="payment",
            entity_id=outcome.payment.id,
            after={
                "contract_id": contract.id,
                "amount": float(outcome.payment.amount),
                "allocated_amount": float(outcome.payment.allocated_amount),
                "status": outcome.payment.status.value,
            },
        )
        if outcome.closure is not None:
            record_event(
                db,
                user_id=actor.id,
                action="contract.closed",
                entity_type="installment_contract",
                entity_id=contract.id,
                before={"status": "active"},
                after={"status": "closed", "reason": outcome.closure.reason.value},
            )
        db.commit()
    db.refresh(outcome.payment)
    return PaymentResult(
        replayed=outcome.replayed,
        payment=PaymentOut.model_validate(outcome.payment),
    )


@router.get("/contracts/{contract_id}/receivable", response_model=ReceivableOut)
def get_receivable(
    contract_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _get_contract(db, contract_id)
    authorize_owner_or_roles(
        db, user,
        staff_roles=_RECEIVABLE_STAFF_ROLES,
        owner_customer_id=contract_owner_customer_id(db, contract),
    )
    return build_receivable(contract)


@router.post("/jobs/assess-overdue", response_model=AssessOverdueResult)
def assess_overdue(
    payload: AssessOverdueRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.admin)),
):
    as_of = payload.as_of if payload else None
    summary = overdue_service.assess_overdue(db, as_of=as_of, actor_id=actor.id)

    record_event(
        db,
        user_id=actor.id,
        action="overdue.assessed",
        entity_type="job",
        entity_id=None,
        after={
            "as_of": summary.as_of.isoformat(),
            "installments_marked_overdue": summary.installments_marked_overdue,
            "late_fees_assessed": summary.late_fees_assessed,
            "total_late_fee_amount": float(summary.total_late_fee_amount),
            "collection_cases_opened": summary.collection_cases_opened,
        },
    )
    for charge in summary.charges:
        record_event(
            db,
            user_id=actor.id,
            action="late_fee.assessed",
            entity_type="installment",
            entity_id=charge["installment_id"],
            after={"amount": charge["amount"], "dpd": charge["dpd"]},
        )
    db.commit()
    return AssessOverdueResult(
        collection_cases_opened=summary.collection_cases_opened,
        as_of=summary.as_of,
        grace_period_days=summary.grace_period_days,
        installments_marked_overdue=summary.installments_marked_overdue,
        late_fees_assessed=summary.late_fees_assessed,
        total_late_fee_amount=float(summary.total_late_fee_amount),
        charges=summary.charges,
    )
