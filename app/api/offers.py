from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.contract import InstallmentContract
from app.models.credit_application import CreditApplication
from app.models.offer import InstallmentOffer
from app.schemas.contract import AcceptResult, ContractOut
from app.schemas.offer import OfferAccept, OfferCreate, OfferOut
from app.services import offers as offer_service
from app.services.errors import DomainError

router = APIRouter(tags=["offers & contracts"])


@router.post(
    "/applications/{application_id}/offer",
    response_model=OfferOut,
    status_code=status.HTTP_201_CREATED,
)
def create_offer(
    application_id: int, payload: OfferCreate, db: Session = Depends(get_db)
):
    application = db.get(CreditApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        offer = offer_service.generate_offer(
            db,
            application,
            down_payment_amount=payload.down_payment_amount,
            tenor_months=payload.tenor_months,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(offer)
    return offer


@router.get("/offers/{offer_id}", response_model=OfferOut)
def get_offer(offer_id: int, db: Session = Depends(get_db)):
    offer = db.get(InstallmentOffer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    return offer


@router.post("/offers/{offer_id}/accept", response_model=AcceptResult)
def accept_offer(offer_id: int, payload: OfferAccept, db: Session = Depends(get_db)):
    offer = db.get(InstallmentOffer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    try:
        contract = offer_service.accept_offer(
            db,
            offer,
            down_payment_confirmed=payload.down_payment_confirmed,
            down_payment_reference=payload.down_payment_reference,
            down_payment_amount=payload.down_payment_amount,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(contract)
    return AcceptResult(
        offer_id=offer.id,
        sales_order_id=contract.sales_order_id,
        contract_id=contract.id,
        contract=ContractOut.model_validate(contract),
    )


@router.get("/contracts/{contract_id}", response_model=ContractOut)
def get_contract(contract_id: int, db: Session = Depends(get_db)):
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.post("/contracts/{contract_id}/confirm-delivery", response_model=ContractOut)
def confirm_delivery(contract_id: int, db: Session = Depends(get_db)):
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    try:
        offer_service.confirm_delivery(db, contract)
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(contract)
    return contract
