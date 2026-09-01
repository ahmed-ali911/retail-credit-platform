from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import authorize_owner_or_roles, get_current_user, require_roles
from app.core.database import get_db
from app.models.customer import Customer, CustomerProfile
from app.models.user import User, UserRole
from app.schemas.customer import CustomerCreate, CustomerListItem, CustomerOut
from app.schemas.exposure import CustomerExposureOut
from app.services import exposure as exposure_service
from app.services.audit import record_event

router = APIRouter(prefix="/customers", tags=["customers"])

_EXPOSURE_STAFF_ROLES = (
    UserRole.credit_officer,
    UserRole.credit_manager,
    UserRole.finance_officer,
    UserRole.admin,
)
_DIRECTORY_ROLES = (
    UserRole.sales_employee,
    UserRole.credit_officer,
    UserRole.credit_manager,
    UserRole.finance_officer,
    UserRole.admin,
)


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


@router.get("", response_model=list[CustomerListItem])
def search_customers(
    db: Session = Depends(get_db),
    search: str = Query(min_length=1, max_length=100),
    _: User = Depends(require_roles(*_DIRECTORY_ROLES)),
):
    """Step 10 customer directory — partial, case-insensitive match on name OR
    national_id. `search` is the only supported parameter."""
    like = f"%{search.strip()}%"
    stmt = (
        select(Customer)
        .where(or_(Customer.name.ilike(like), Customer.national_id.ilike(like)))
        .order_by(Customer.name)
    )
    return db.execute(stmt).scalars().all()


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/{customer_id}/exposure", response_model=CustomerExposureOut)
def get_customer_exposure(
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """P0-4 — aggregate outstanding balance across the customer's non-closed
    contracts, with a per-contract breakdown."""
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    authorize_owner_or_roles(
        db, user,
        staff_roles=_EXPOSURE_STAFF_ROLES,
        owner_customer_id=customer_id,
    )
    try:
        return exposure_service.compute_exposure(db, customer_id)
    except ValueError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
