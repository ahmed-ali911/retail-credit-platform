"""Mock Payment Gateway — a genuinely separate FastAPI service.

Simulates a hosted-checkout payment provider for the Retail Credit and
Installment Sales Platform demo. Entirely simulated: no real card numbers,
CVVs, bank credentials, or real payment tokens are ever requested, stored,
or processed. Not affiliated with, and does not claim to be, any real bank
or payment network — see mock-payment-gateway/README.md.

This service owns its own database (app/database.py) and never reaches into
retail-credit-api's database or vice versa; the two only ever talk over
HTTP — a checkout-session request in one direction, signed webhooks in the
other (app/webhook_sender.py / app/security.py).
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database as database_module
from app import webhook_sender
from app.checkout_page import render_checkout_page
from app.config import get_settings
from app.database import get_db, init_db
from app.models import (
    CheckoutSession,
    CheckoutSessionStatus,
    GatewayTransactionRecord,
    GatewayTransactionStatus,
    WebhookDeliveryLog,
    new_gateway_transaction_reference,
)
from app.schemas import (
    CheckoutSessionCreate,
    CheckoutSessionOut,
    SimulatedOutcome,
    TransactionOut,
    WebhookRetryResult,
)

_CENTS = Decimal("0.01")
# Demo-only, illustrative gateway fee on a successful settlement — clearly a
# placeholder, not a real acquirer's pricing (see docs/payment-gateway/BRD.md).
_DEMO_FEE_RATE = Decimal("0.015")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Mock Payment Gateway", version="0.1.0", lifespan=lifespan)


def _utcnow() -> datetime:
    # Naive UTC, matching app/models.py::_utcnow — see that comment for why
    # (SQLite drops tzinfo on read-back, so this service compares naive
    # throughout rather than fighting that on every read).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fee_for(amount: Decimal) -> Decimal:
    return (amount * _DEMO_FEE_RATE).quantize(_CENTS, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Checkout session creation (called by retail-credit-api)
# --------------------------------------------------------------------------- #
@app.post("/gateway/checkout-sessions", response_model=CheckoutSessionOut, status_code=201)
def create_checkout_session(payload: CheckoutSessionCreate, db: Session = Depends(get_db)):
    settings = get_settings()
    expires_at = _utcnow() + timedelta(minutes=settings.checkout_session_expiry_minutes)

    session = CheckoutSession(
        merchant_reference=payload.merchant_reference,
        amount=payload.amount,
        currency=payload.currency,
        customer_name=payload.customer_name,
        contract_reference=payload.contract_reference,
        description=payload.description,
        return_url=payload.return_url,
        webhook_url=payload.webhook_url or settings.default_webhook_url,
        status=CheckoutSessionStatus.open,
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()

    txn = GatewayTransactionRecord(
        gateway_transaction_reference=new_gateway_transaction_reference(),
        checkout_session_id=session.id,
        merchant_reference=payload.merchant_reference,
        status=GatewayTransactionStatus.pending,
        amount=payload.amount,
        currency=payload.currency,
    )
    db.add(txn)
    db.commit()
    db.refresh(session)

    return CheckoutSessionOut(
        token=session.token,
        checkout_url=f"{settings.public_base_url}/gateway/checkout/{session.token}",
        expires_at=session.expires_at,
    )


# --------------------------------------------------------------------------- #
# Hosted checkout page ("Demo Gateway")
# --------------------------------------------------------------------------- #
def _get_session(db: Session, token: str) -> CheckoutSession:
    session = db.execute(
        select(CheckoutSession).where(CheckoutSession.token == token)
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    return session


@app.get("/gateway/checkout/{token}", response_class=HTMLResponse)
def show_checkout_page(token: str, db: Session = Depends(get_db)):
    session = _get_session(db, token)
    if session.status == CheckoutSessionStatus.consumed:
        html = render_checkout_page(
            session, disabled=True, banner_message="This checkout session has already been used."
        )
    elif _utcnow() > session.expires_at:
        html = render_checkout_page(
            session, disabled=True, banner_message="This checkout session has expired."
        )
    else:
        html = render_checkout_page(session)
    return HTMLResponse(content=html)


# --------------------------------------------------------------------------- #
# Simulated outcome — the customer's one click on the hosted page
# --------------------------------------------------------------------------- #
def _mark(
    db: Session,
    txn: GatewayTransactionRecord,
    session: CheckoutSession,
    *,
    status: GatewayTransactionStatus,
    event_type: str,
    timestamp_field: str | None = None,
    gateway_event_id: str | None = None,
    corrupt_signature: bool = False,
) -> WebhookDeliveryLog:
    txn.status = status
    if timestamp_field:
        setattr(txn, timestamp_field, _utcnow())
    db.flush()
    return webhook_sender.send_event(
        db, txn,
        event_type=event_type,
        status=status.value,
        webhook_url=session.webhook_url,
        gateway_event_id=gateway_event_id,
        corrupt_signature=corrupt_signature,
    )


def _run_delayed_settlement(checkout_session_id: int) -> None:
    db = database_module.SessionLocal()
    try:
        session = db.get(CheckoutSession, checkout_session_id)
        if session is None or session.transaction is None:
            return
        txn = session.transaction
        txn.gateway_fee = _fee_for(Decimal(txn.amount))
        _mark(
            db, txn, session,
            status=GatewayTransactionStatus.settled,
            event_type="payment.settled",
            timestamp_field="settlement_timestamp",
        )
        db.commit()
    finally:
        db.close()


@app.post("/gateway/checkout/{token}/simulate")
def simulate_outcome(
    token: str, outcome: SimulatedOutcome = Form(...), db: Session = Depends(get_db)
):
    session = _get_session(db, token)
    if session.status != CheckoutSessionStatus.open:
        raise HTTPException(status_code=409, detail="This checkout session is no longer open")
    if _utcnow() > session.expires_at:
        raise HTTPException(status_code=410, detail="This checkout session has expired")

    txn = session.transaction
    settings = get_settings()
    result_status = "pending"

    if outcome == SimulatedOutcome.success_settlement:
        _mark(db, txn, session, status=GatewayTransactionStatus.authorized,
              event_type="payment.authorized", timestamp_field="authorization_timestamp")
        _mark(db, txn, session, status=GatewayTransactionStatus.captured,
              event_type="payment.captured", timestamp_field="capture_timestamp")
        txn.gateway_fee = _fee_for(Decimal(txn.amount))
        _mark(db, txn, session, status=GatewayTransactionStatus.settled,
              event_type="payment.settled", timestamp_field="settlement_timestamp")
        result_status = "settled"

    elif outcome == SimulatedOutcome.success_authorization_only:
        _mark(db, txn, session, status=GatewayTransactionStatus.authorized,
              event_type="payment.authorized", timestamp_field="authorization_timestamp")
        result_status = "authorized"

    elif outcome == SimulatedOutcome.insufficient_funds:
        txn.failure_code = "insufficient_funds"
        txn.failure_reason = "Simulated decline: insufficient funds on the demo debit network"
        _mark(db, txn, session, status=GatewayTransactionStatus.failed, event_type="payment.failed")
        result_status = "failed"

    elif outcome == SimulatedOutcome.customer_cancel:
        _mark(db, txn, session, status=GatewayTransactionStatus.cancelled, event_type="payment.cancelled")
        result_status = "cancelled"

    elif outcome == SimulatedOutcome.timeout:
        _mark(db, txn, session, status=GatewayTransactionStatus.expired, event_type="payment.expired")
        result_status = "expired"

    elif outcome == SimulatedOutcome.delayed_settlement:
        _mark(db, txn, session, status=GatewayTransactionStatus.authorized,
              event_type="payment.authorized", timestamp_field="authorization_timestamp")
        _mark(db, txn, session, status=GatewayTransactionStatus.captured,
              event_type="payment.captured", timestamp_field="capture_timestamp")
        result_status = "captured"
        db.flush()
        session.status = CheckoutSessionStatus.consumed
        db.commit()
        threading.Timer(
            settings.delayed_settlement_seconds, _run_delayed_settlement, args=(session.id,)
        ).start()
        return RedirectResponse(
            url=f"{session.return_url}?payment_reference={session.merchant_reference}&outcome={result_status}",
            status_code=303,
        )

    elif outcome == SimulatedOutcome.duplicate_webhook:
        _mark(db, txn, session, status=GatewayTransactionStatus.authorized,
              event_type="payment.authorized", timestamp_field="authorization_timestamp")
        _mark(db, txn, session, status=GatewayTransactionStatus.captured,
              event_type="payment.captured", timestamp_field="capture_timestamp")
        txn.gateway_fee = _fee_for(Decimal(txn.amount))
        settled_log = _mark(db, txn, session, status=GatewayTransactionStatus.settled,
                             event_type="payment.settled", timestamp_field="settlement_timestamp")
        # Redeliver the SAME logical event a second time — exercises
        # retail-credit-api's gateway_event_id idempotency handling.
        webhook_sender.send_event(
            db, txn, event_type="payment.settled", status="SETTLED",
            webhook_url=session.webhook_url, gateway_event_id=settled_log.gateway_event_id,
        )
        result_status = "settled"

    elif outcome == SimulatedOutcome.invalid_signature:
        _mark(db, txn, session, status=GatewayTransactionStatus.authorized,
              event_type="payment.authorized", timestamp_field="authorization_timestamp",
              corrupt_signature=True)
        result_status = "authorized"

    elif outcome == SimulatedOutcome.settle_then_reverse:
        _mark(db, txn, session, status=GatewayTransactionStatus.authorized,
              event_type="payment.authorized", timestamp_field="authorization_timestamp")
        _mark(db, txn, session, status=GatewayTransactionStatus.captured,
              event_type="payment.captured", timestamp_field="capture_timestamp")
        txn.gateway_fee = _fee_for(Decimal(txn.amount))
        _mark(db, txn, session, status=GatewayTransactionStatus.settled,
              event_type="payment.settled", timestamp_field="settlement_timestamp")
        _mark(db, txn, session, status=GatewayTransactionStatus.reversed, event_type="payment.reversed")
        result_status = "reversed"

    txn.simulated_outcome = outcome.value
    session.status = CheckoutSessionStatus.consumed
    db.commit()

    return RedirectResponse(
        url=f"{session.return_url}?payment_reference={session.merchant_reference}&outcome={result_status}",
        status_code=303,
    )


# --------------------------------------------------------------------------- #
# Lookups / operational endpoints
# --------------------------------------------------------------------------- #
@app.get("/gateway/transactions/{reference}", response_model=TransactionOut)
def get_transaction(reference: str, db: Session = Depends(get_db)):
    txn = db.execute(
        select(GatewayTransactionRecord).where(
            (GatewayTransactionRecord.gateway_transaction_reference == reference)
            | (GatewayTransactionRecord.merchant_reference == reference)
        )
    ).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return TransactionOut(
        gateway_transaction_reference=txn.gateway_transaction_reference,
        merchant_reference=txn.merchant_reference,
        status=txn.status.value,
        amount=txn.amount,
        currency=txn.currency,
        gateway_fee=txn.gateway_fee,
        authorization_timestamp=txn.authorization_timestamp,
        capture_timestamp=txn.capture_timestamp,
        settlement_timestamp=txn.settlement_timestamp,
        failure_code=txn.failure_code,
        failure_reason=txn.failure_reason,
        simulated_outcome=txn.simulated_outcome,
    )


@app.post("/gateway/webhooks/{event_id}/retry", response_model=WebhookRetryResult)
def retry_webhook(event_id: str, db: Session = Depends(get_db)):
    last = db.execute(
        select(WebhookDeliveryLog)
        .where(WebhookDeliveryLog.gateway_event_id == event_id)
        .order_by(WebhookDeliveryLog.id.desc())
    ).scalars().first()
    if last is None:
        raise HTTPException(status_code=404, detail="No delivery found for this gateway_event_id")
    retry_log = webhook_sender.resend_event(db, last)
    db.commit()
    return WebhookRetryResult(
        gateway_event_id=retry_log.gateway_event_id,
        attempt_count=retry_log.attempt_count,
        last_response_status=retry_log.last_response_status,
        delivered=retry_log.delivered,
    )


@app.get("/health")
def health():
    return {"status": "ok"}
