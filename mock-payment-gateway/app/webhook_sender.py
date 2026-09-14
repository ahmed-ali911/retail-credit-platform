"""Builds and delivers signed webhooks to retail-credit-api.

Each call to ``send_event`` is one delivery attempt and gets its own
``WebhookDeliveryLog`` row (the gateway's outbox) — multiple rows can share
the same ``gateway_event_id`` when a scenario deliberately redelivers the
same logical event (the `duplicate_webhook` demo scenario, and the manual
``POST /gateway/webhooks/{event_id}/retry`` endpoint), which is exactly the
condition retail-credit-api's idempotency handling (dedup on
``gateway_event_id``) exists to prove out.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GatewayTransactionRecord, WebhookDeliveryLog, new_event_id
from app.security import sign, sign_corrupt


def _utcnow() -> datetime:
    # Naive UTC — matches every timestamp COLUMN in app/models.py (see the
    # comment there). The outbound payload's own "timestamp" field is built
    # separately below with an explicit +00:00 offset, since that string
    # crosses the wire to retail-credit-api and must be unambiguous there.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {o!r}")


def _build_payload(
    txn: GatewayTransactionRecord, *, gateway_event_id: str, event_type: str, status: str
) -> dict:
    return {
        "gateway_event_id": gateway_event_id,
        "event_type": event_type,
        "payment_reference": txn.merchant_reference,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gateway_transaction_reference": txn.gateway_transaction_reference,
        "authorized_amount": str(txn.amount) if status in ("AUTHORIZED", "CAPTURED", "SETTLED") else None,
        "captured_amount": str(txn.amount) if status in ("CAPTURED", "SETTLED") else None,
        "settled_amount": str(txn.amount) if status == "SETTLED" else None,
        "gateway_fee": str(txn.gateway_fee) if txn.gateway_fee is not None else None,
        "authorization_timestamp": txn.authorization_timestamp.isoformat() if txn.authorization_timestamp else None,
        "capture_timestamp": txn.capture_timestamp.isoformat() if txn.capture_timestamp else None,
        "settlement_timestamp": txn.settlement_timestamp.isoformat() if txn.settlement_timestamp else None,
        "failure_code": txn.failure_code,
        "failure_reason": txn.failure_reason,
    }


def send_event(
    db: Session,
    txn: GatewayTransactionRecord,
    *,
    event_type: str,
    status: str,
    webhook_url: str,
    gateway_event_id: str | None = None,
    corrupt_signature: bool = False,
) -> WebhookDeliveryLog:
    """Build, sign, and POST one webhook. ``gateway_event_id`` may be passed
    explicitly to deliberately redeliver a *previous* logical event (the
    duplicate-webhook and manual-retry scenarios) rather than minting a new
    one."""
    settings = get_settings()
    event_id = gateway_event_id or new_event_id()
    payload = _build_payload(txn, gateway_event_id=event_id, event_type=event_type, status=status)
    raw_body = json.dumps(payload, default=_json_default).encode()
    header = sign_corrupt(settings.webhook_secret, raw_body) if corrupt_signature else sign(
        settings.webhook_secret, raw_body
    )

    prior_attempts = db.execute(
        select(WebhookDeliveryLog).where(WebhookDeliveryLog.gateway_event_id == event_id)
    ).scalars().all()

    log = WebhookDeliveryLog(
        gateway_event_id=event_id,
        gateway_transaction_id=txn.id,
        event_type=event_type,
        claimed_status=status,
        payload_json=payload,
        webhook_url=webhook_url,
        corrupt_signature=corrupt_signature,
        attempt_count=len(prior_attempts) + 1,
    )
    _deliver(log, raw_body=raw_body, header=header, url=webhook_url)
    db.add(log)
    db.flush()
    return log


def resend_event(db: Session, log: WebhookDeliveryLog) -> WebhookDeliveryLog:
    """POST /gateway/webhooks/{event_id}/retry — redeliver the exact stored
    payload for one prior event (byte-identical body, freshly signed) as a
    new outbox row, same gateway_event_id."""
    settings = get_settings()
    raw_body = json.dumps(log.payload_json, default=_json_default).encode()
    header = sign_corrupt(settings.webhook_secret, raw_body) if log.corrupt_signature else sign(
        settings.webhook_secret, raw_body
    )
    prior_attempts = db.execute(
        select(WebhookDeliveryLog).where(WebhookDeliveryLog.gateway_event_id == log.gateway_event_id)
    ).scalars().all()
    retry_log = WebhookDeliveryLog(
        gateway_event_id=log.gateway_event_id,
        gateway_transaction_id=log.gateway_transaction_id,
        event_type=log.event_type,
        claimed_status=log.claimed_status,
        payload_json=log.payload_json,
        webhook_url=log.webhook_url,
        corrupt_signature=log.corrupt_signature,
        attempt_count=len(prior_attempts) + 1,
    )
    _deliver(retry_log, raw_body=raw_body, header=header, url=log.webhook_url)
    db.add(retry_log)
    db.flush()
    return retry_log


def _deliver(log: WebhookDeliveryLog, *, raw_body: bytes, header: str, url: str) -> None:
    log.last_attempt_at = _utcnow()
    try:
        resp = httpx.post(
            url,
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Gateway-Signature": header},
            timeout=10.0,
        )
        log.last_response_status = resp.status_code
        log.last_response_body = resp.text[:500]
        log.delivered = resp.status_code < 300
    except httpx.HTTPError as exc:
        log.last_response_status = None
        log.last_response_body = str(exc)[:500]
        log.delivered = False
