from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.customer import Customer, CustomerProfile
from app.models.user import User, UserRole
from app.schemas.customer import CustomerCreate, CustomerOut
from app.services.audit import record_event

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.sales_employee, UserRole.admin)),
):
    customer = Customer(
        name=payload.name,
        national_id=payload.national_id,
        phone=payload.phone,
        email=payload.email,
        status=payload.status,
        risk_score=payload.risk_score,
        profile=CustomerProfile(**payload.profile.model_dump()),
    )
    db.add(customer)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A customer with national_id '{payload.national_id}' already exists",
        )
    record_event(
        db,
        user_id=actor.id,
        action="customer.created",
        entity_type="customer",
        entity_id=customer.id,
        after={"national_id": customer.national_id, "status": customer.status.value},
    )
    db.commit()
    db.refresh(customer)
    return customer


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer
