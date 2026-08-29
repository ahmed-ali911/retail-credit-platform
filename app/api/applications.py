from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.credit_application import (
    ApplicationStatus,
    CreditApplication,
)
from app.models.customer import Customer
from app.models.product import Product
from app.schemas.application import ApplicationCreate, ApplicationOut
from app.services.assessment import assess_application

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    if db.get(Customer, payload.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    product = db.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if not product.installment_eligible:
        raise HTTPException(
            status_code=422, detail="Product is not eligible for installment sale"
        )

    application = CreditApplication(
        customer_id=payload.customer_id,
        product_id=payload.product_id,
        requested_amount=payload.requested_amount,
        requested_tenor_months=payload.requested_tenor_months,
        channel=payload.channel,
        created_by=payload.created_by,
        status=ApplicationStatus.draft,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


@router.post("/{application_id}/submit", response_model=ApplicationOut)
def submit_application(application_id: int, db: Session = Depends(get_db)):
    application = db.get(CreditApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status != ApplicationStatus.draft:
        raise HTTPException(
            status_code=409,
            detail=f"Only draft applications can be submitted (current: {application.status.value})",
        )

    # draft -> submitted -> under_assessment -> (approved | rejected | referred)
    application.status = ApplicationStatus.submitted
    db.flush()
    application.status = ApplicationStatus.under_assessment
    db.flush()

    assess_application(db, application)
    db.commit()
    db.refresh(application)
    return application


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(application_id: int, db: Session = Depends(get_db)):
    application = db.get(CreditApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application
