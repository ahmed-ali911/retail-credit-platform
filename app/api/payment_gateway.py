"""Mock Payment Gateway — Retail Credit-side endpoints (sections 3, 12).

Two routers:
  * ``router`` — customer/staff-facing, behind the normal bearer-token auth
    (registered with the same `dependencies=_authed` as every other router
    in app/main.py).
  * ``webhook_router`` — the inbound callback from the *separate*
    mock-payment-gateway service. Registered WITHOUT the bearer-token
    dependency (the gateway has no user JWT) — authenticated instead by the
    HMAC signature verified inside gateway_webhooks.py, exactly like
    /auth/login is the one other pre-existing un-authenticated route.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import authorize_owner_or_roles, contract_owner_customer_id, get_current_user
from app.core.database import get_db
from app.models.contract import InstallmentContract
from app.models.payment_gateway import PaymentIntent
from app.models.user import User, UserRole
from app.schemas.payment_gateway import (
    CheckoutSessionOut,
    PaymentIntentCreate,
    PaymentIntentOut,
    PaymentOptionsOut,
    PaymentStatusOut,
)
from app.services import gateway_webhooks, payment_gateway_client, payment_intents
from app.services.audit import record_event
from app.services.errors import DomainError

router = APIRouter(tags=["payment gateway"])
webhook_router = APIRouter(tags=["payment gateway — inbound webhook"])

# Same shape as payments.py::_PAYMENT_STAFF_ROLES — a customer may only act on
# their own contract; these staff roles may act on any contract (e.g. taking
# a payment over the phone/in-branch through the same gateway).
_PAYMENT_STAFF_ROLES = (
    UserRole.sales_employee,
    UserRole.finance_officer,
    UserRole.admin,
)


def _get_contract(db: Session, contract_id: int) -> InstallmentContract:
    contract = db.get(InstallmentContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


def _authorize_contract(db: Session, actor: User, contract: InstallmentContract) -> None:
    authorize_owner_or_roles(
        db, actor,
        staff_roles=_PAYMENT_STAFF_ROLES,
        owner_customer_id=contract_owner_customer_id(db, contract),
    )


@router.get("/contracts/{contract_id}/payment-options", response_model=PaymentOptionsOut)
def get_payment_options(
    contract_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    contract = _get_contract(db, contract_id)
    _authorize_contract(db, actor, contract)
    return payment_intents.compute_payment_options(db, contract)


@router.post("/payments/intents", response_model=PaymentIntentOut, status_code=201)
def create_payment_intent(
    payload: PaymentIntentCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    contract = _get_contract(db, payload.contract_id)
    _authorize_contract(db, actor, contract)
    try:
        outcome = payment_intents.create_intent(
            db,
            contract,
            purpose=payload.payment_purpose,
            amount=Decimal(str(payload.amount)) if payload.amount is not None else None,
            actor_id=actor.id,
            idempotency_key=payload.idempotency_key,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    intent = outcome.intent
    if not outcome.replayed:
        record_event(
            db, user_id=actor.id, action="payment_intent.created",
            entity_type="payment_intent", entity_id=intent.id,
            after={
                "contract_id": contract.id,
                "requested_amount": float(intent.requested_amount),
                "payment_purpose": intent.payment_purpose.value,
                "status": intent.status.value,
            },
        )
        db.commit()
    db.refresh(intent)
    return intent


@router.get("/payments/intents/{intent_id}", response_model=PaymentIntentOut)
def get_payment_intent(
    intent_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    intent = db.get(PaymentIntent, intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Payment intent not found")
    _authorize_contract(db, actor, intent.contract)
    return intent


@router.post("/payments/intents/{intent_id}/checkout", response_model=CheckoutSessionOut)
def open_checkout(
    intent_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    intent = db.get(PaymentIntent, intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Payment intent not found")
    _authorize_contract(db, actor, intent.contract)
    try:
        result = payment_intents.create_checkout(db, intent)
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except payment_gateway_client.GatewayClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    record_event(
        db, user_id=actor.id, action="payment_intent.checkout_opened",
        entity_type="payment_intent", entity_id=intent.id,
        after={"gateway_session_id": intent.gateway_session_id, "status": intent.status.value},
    )
    db.commit()
    return CheckoutSessionOut(
        payment_reference=intent.payment_reference,
        checkout_url=result.checkout_url,
        expires_at=intent.expires_at,
        status=intent.status,
    )


@router.get("/payments/{payment_reference}/status", response_model=PaymentStatusOut)
def get_payment_status(
    payment_reference: str,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    from sqlalchemy import select

    intent = db.execute(
        select(PaymentIntent).where(PaymentIntent.payment_reference == payment_reference)
    ).scalar_one_or_none()
    if intent is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    _authorize_contract(db, actor, intent.contract)
    return PaymentStatusOut(
        payment_reference=intent.payment_reference,
        status=intent.status,
        requested_amount=float(intent.requested_amount),
        currency=intent.currency,
        contract_id=intent.contract_id,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
        transactions=list(intent.transactions),
    )


# --------------------------------------------------------------------------- #
# Inbound webhook — no bearer-token auth; HMAC-verified inside.
# --------------------------------------------------------------------------- #
@webhook_router.post("/integrations/mock-gateway/webhooks")
async def receive_gateway_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        outcome = gateway_webhooks.process_webhook(db, headers=headers, raw_body=raw_body)
    except DomainError as exc:
        db.commit()  # persist whatever audit row was already added before the raise
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    return Response(
        status_code=outcome.http_status,
        content=(
            f'{{"received": true, "processing_status": '
            f'"{outcome.webhook_event.processing_status.value}"}}'
        ),
        media_type="application/json",
    )
