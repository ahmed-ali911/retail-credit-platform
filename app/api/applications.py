from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import authorize_owner_or_roles, get_current_user, require_roles
from app.core.database import get_db
from app.models.credit_application import (
    ApplicationStatus,
    AssessmentResult,
    AssessmentSource,
    CreditApplication,
)
from app.models.customer import Customer
from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.application import (
    ApplicationCreate,
    ApplicationOut,
    ReviewDecision,
    ReviewRequest,
)
from app.services.assessment import assess_application
from app.services.audit import record_event

router = APIRouter(prefix="/applications", tags=["applications"])

_ORIGINATION_ROLES = (UserRole.sales_employee, UserRole.customer, UserRole.admin)
_VIEW_STAFF_ROLES = (
    UserRole.sales_employee,
    UserRole.credit_officer,
    UserRole.credit_manager,
    UserRole.admin,
)
_REVIEW_ROLES = (UserRole.credit_officer, UserRole.credit_manager, UserRole.admin)

# Manual-review decision -> resulting application status.
_REVIEW_STATUS = {
    ReviewDecision.approved: ApplicationStatus.approved,
    ReviewDecision.rejected: ApplicationStatus.rejected,
    ReviewDecision.return_for_info: ApplicationStatus.draft,
}


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_ORIGINATION_ROLES)),
):
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
    db.flush()
    record_event(
        db,
        user_id=actor.id,
        action="application.created",
        entity_type="credit_application",
        entity_id=application.id,
        after={"status": application.status.value, "customer_id": application.customer_id},
    )
    db.commit()
    db.refresh(application)
    return application


@router.post("/{application_id}/submit", response_model=ApplicationOut)
def submit_application(
    application_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_ORIGINATION_ROLES)),
):
    application = db.get(CreditApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status != ApplicationStatus.draft:
        raise HTTPException(
            status_code=409,
            detail=f"Only draft applications can be submitted (current: {application.status.value})",
        )

    status_before = application.status.value

    # draft -> submitted -> under_assessment -> (approved | rejected | referred)
    application.status = ApplicationStatus.submitted
    db.flush()
    application.status = ApplicationStatus.under_assessment
    db.flush()

    result = assess_application(db, application)
    record_event(
        db,
        user_id=actor.id,
        action="application.submitted",
        entity_type="credit_application",
        entity_id=application.id,
        before={"status": status_before},
        after={"status": application.status.value, "decision": result.decision},
    )
    db.commit()
    db.refresh(application)
    return application


@router.post("/{application_id}/review", response_model=ApplicationOut)
def review_application(
    application_id: int,
    payload: ReviewRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_REVIEW_ROLES)),
):
    """Manual verification of a *referred* application by a credit officer.

    Fixes S-1 (referred was a dead end). This does NOT touch the automated
    assessment engine — it records a second, `manual`-source `AssessmentResult`
    and moves the application on:
      * approved       -> `approved`  (proceeds to offer generation like an auto-approval)
      * rejected       -> `rejected`
      * return_for_info -> `draft`    (resubmit through the normal submit flow)
    """
    application = db.get(CreditApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status != ApplicationStatus.referred:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Application {application_id} is not awaiting manual review "
                f"(status: {application.status.value}); only 'referred' "
                f"applications can be reviewed."
            ),
        )

    status_before = application.status.value
    new_status = _REVIEW_STATUS[payload.decision]

    prior = application.latest_assessment  # the automated 'referred' assessment
    review = AssessmentResult(
        application_id=application.id,
        decision=payload.decision.value,
        source=AssessmentSource.manual,
        reviewed_by=actor.id,
        notes=payload.reason,
        estimated_installment=(
            prior.estimated_installment if prior is not None else 0
        ),
        debt_burden_ratio=(prior.debt_burden_ratio if prior is not None else None),
        triggered_rules=[
            {
                "rule": "manual_review",
                "outcome": payload.decision.value,
                "reason": payload.reason,
            }
        ],
        config_snapshot={},
    )
    db.add(review)
    application.status = new_status
    db.flush()

    record_event(
        db,
        user_id=actor.id,
        action="application.reviewed",
        entity_type="credit_application",
        entity_id=application.id,
        before={"status": status_before},
        after={
            "status": new_status.value,
            "decision": payload.decision.value,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(application)
    return application


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    application = db.get(CreditApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    authorize_owner_or_roles(
        db, user,
        staff_roles=_VIEW_STAFF_ROLES,
        owner_customer_id=application.customer_id,
    )
    return application
