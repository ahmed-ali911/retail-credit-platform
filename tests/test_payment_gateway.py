"""Mock Payment Gateway — checkpoint 1 scope: PaymentIntent lifecycle and
inbound webhook processing (data model, migrations, payment-intent service,
gateway_webhooks.process_webhook). Allocation/Collections integration is
checkpoint 2 — _apply_final_allocation is a deliberate no-op here, so these
tests assert SETTLED is reachable and audited, not that a Payment row exists
yet (that assertion belongs in the checkpoint-2 test file).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.models.payment_gateway import PaymentIntent, WebhookEvent
from tests.helpers import active_contract


def _sign(secret: str, raw_body: bytes, *, timestamp: int | None = None) -> str:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signature = hmac.new(secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def _webhook_body(**fields) -> bytes:
    payload = {
        "gateway_event_id": "evt_1",
        "event_type": "payment.authorized",
        "payment_reference": "",
        "status": "AUTHORIZED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gateway_transaction_reference": "GWTXN-1",
        "authorized_amount": "10.00",
    }
    payload.update(fields)
    return json.dumps(payload).encode()


def _post_webhook(client, raw_body: bytes, *, secret: str | None = None, timestamp: int | None = None):
    secret = secret or get_settings().gateway_webhook_secret
    header = _sign(secret, raw_body, timestamp=timestamp)
    return client.post(
        "/integrations/mock-gateway/webhooks",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Gateway-Signature": header},
    )


@pytest.fixture(autouse=True)
def _mock_gateway_client(monkeypatch):
    """No real mock-payment-gateway service is running during this test suite
    — stub the outbound HTTP call retail-credit-api makes to open a checkout
    session, exactly like the client-side of a real integration test would."""
    from app.services import payment_gateway_client

    def fake_post(url, *, json, timeout):
        class _Resp:
            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return {
                    "token": "tok_test",
                    "checkout_url": "http://mock-payment-gateway.test/gateway/checkout/tok_test",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                }

        return _Resp()

    monkeypatch.setattr(payment_gateway_client.httpx, "post", fake_post)


def _open_intent(client, cid, *, purpose="current_installment", amount=None, idem="IDEM-1"):
    body = {"contract_id": cid, "payment_purpose": purpose, "idempotency_key": idem}
    if amount is not None:
        body["amount"] = amount
    resp = client.post("/payments/intents", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _checkout(client, intent_id):
    resp = client.post(f"/payments/intents/{intent_id}/checkout")
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Payment options / intent creation
# --------------------------------------------------------------------------- #
def test_payment_options_reflect_the_contract(client):
    ctx = active_contract(client, national_id="PG-1")
    cid = ctx["contract_id"]
    resp = client.get(f"/contracts/{cid}/payment-options")
    assert resp.status_code == 200, resp.text
    options = resp.json()
    assert options["currency"] == "KWD"
    assert options["can_pay_current_installment"] is True
    assert options["total_outstanding"] > 0


def test_create_intent_then_replay_with_same_idempotency_key(client):
    ctx = active_contract(client, national_id="PG-2")
    cid = ctx["contract_id"]
    first = _open_intent(client, cid, idem="SAME-KEY")
    second = _open_intent(client, cid, idem="SAME-KEY")
    assert second["id"] == first["id"]
    assert second["payment_reference"] == first["payment_reference"]


def test_intent_amount_above_total_outstanding_is_rejected(client):
    ctx = active_contract(client, national_id="PG-3")
    cid = ctx["contract_id"]
    resp = client.post("/payments/intents", json={
        "contract_id": cid, "payment_purpose": "partial", "amount": 999999,
        "idempotency_key": "OVER-1",
    })
    assert resp.status_code == 422, resp.text


def test_partial_amount_below_minimum_is_rejected(client):
    ctx = active_contract(client, national_id="PG-4")
    cid = ctx["contract_id"]
    resp = client.post("/payments/intents", json={
        "contract_id": cid, "payment_purpose": "partial", "amount": 0.50,
        "idempotency_key": "LOW-1",
    })
    assert resp.status_code == 422, resp.text


def test_checkout_moves_intent_to_pending(client):
    ctx = active_contract(client, national_id="PG-5")
    cid = ctx["contract_id"]
    intent = _open_intent(client, cid, idem="CO-1")
    checkout = _checkout(client, intent["id"])
    assert checkout["status"] == "PENDING"
    assert checkout["checkout_url"]


# --------------------------------------------------------------------------- #
# Webhook processing
# --------------------------------------------------------------------------- #
def _pending_intent(client, national_id):
    ctx = active_contract(client, national_id=national_id)
    cid = ctx["contract_id"]
    intent = _open_intent(client, cid, idem=f"IDEM-{national_id}")
    _checkout(client, intent["id"])
    return intent


def test_full_success_sequence_reaches_settled(client, db):
    intent = _pending_intent(client, "PGW-1")
    ref = intent["payment_reference"]

    for i, status in enumerate(["AUTHORIZED", "CAPTURED", "SETTLED"]):
        body = _webhook_body(
            gateway_event_id=f"evt-settle-{i}", payment_reference=ref, status=status,
            event_type=f"payment.{status.lower()}",
        )
        resp = _post_webhook(client, body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["processing_status"] == "PROCESSED"

    row = db.get(PaymentIntent, intent["id"])
    assert row.status.value == "SETTLED"


def test_duplicate_webhook_is_acknowledged_without_reprocessing(client, db):
    intent = _pending_intent(client, "PGW-2")
    ref = intent["payment_reference"]
    body = _webhook_body(gateway_event_id="evt-dup-1", payment_reference=ref, status="AUTHORIZED")

    first = _post_webhook(client, body)
    assert first.status_code == 200
    second = _post_webhook(client, body)
    assert second.status_code == 200
    assert second.json()["processing_status"] == "PROCESSED"

    events = db.query(WebhookEvent).filter_by(gateway_event_id="evt-dup-1").all()
    assert len(events) == 1
    assert events[0].retry_count == 1


def test_invalid_signature_is_rejected(client, db):
    intent = _pending_intent(client, "PGW-3")
    ref = intent["payment_reference"]
    body = _webhook_body(gateway_event_id="evt-badsig", payment_reference=ref, status="AUTHORIZED")
    resp = _post_webhook(client, body, secret="wrong-secret")
    assert resp.status_code == 401
    assert resp.json()["processing_status"] == "REJECTED_SIGNATURE"

    row = db.get(PaymentIntent, intent["id"])
    assert row.status.value == "PENDING"  # untouched


def test_stale_webhook_is_rejected(client, db):
    intent = _pending_intent(client, "PGW-4")
    ref = intent["payment_reference"]
    body = _webhook_body(gateway_event_id="evt-stale", payment_reference=ref, status="AUTHORIZED")
    old_ts = int(time.time()) - 10_000
    resp = _post_webhook(client, body, timestamp=old_ts)
    assert resp.status_code == 400
    assert resp.json()["processing_status"] == "REJECTED_STALE"


def test_out_of_order_webhook_is_rejected_without_moving_status_backwards(client, db):
    intent = _pending_intent(client, "PGW-5")
    ref = intent["payment_reference"]
    for i, status in enumerate(["AUTHORIZED", "CAPTURED", "SETTLED"]):
        _post_webhook(client, _webhook_body(
            gateway_event_id=f"evt-ooo-{i}", payment_reference=ref, status=status,
        ))

    # Now claim AUTHORIZED again — settled cannot move "backwards".
    resp = _post_webhook(client, _webhook_body(
        gateway_event_id="evt-ooo-late", payment_reference=ref, status="AUTHORIZED",
    ))
    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "REJECTED_OUT_OF_ORDER"

    row = db.get(PaymentIntent, intent["id"])
    assert row.status.value == "SETTLED"  # unchanged


def test_failed_payment_never_touches_the_contract_balance(client, db):
    ctx = active_contract(client, national_id="PGW-6")
    cid = ctx["contract_id"]
    before = client.get(f"/contracts/{cid}/receivable").json()

    intent = _open_intent(client, cid, idem="IDEM-PGW-6")
    _checkout(client, intent["id"])
    resp = _post_webhook(client, _webhook_body(
        gateway_event_id="evt-fail-1", payment_reference=intent["payment_reference"],
        status="FAILED", event_type="payment.failed", failure_code="insufficient_funds",
    ))
    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "PROCESSED"

    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after == before


def test_unknown_payment_reference_is_acknowledged_but_not_processed(client, db):
    resp = _post_webhook(client, _webhook_body(
        gateway_event_id="evt-unknown", payment_reference="PI-DOES-NOT-EXIST", status="AUTHORIZED",
    ))
    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "FAILED"
