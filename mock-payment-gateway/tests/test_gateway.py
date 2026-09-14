from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from app import webhook_sender
from app.config import get_settings


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok"):
        self.status_code = status_code
        self.text = text


@pytest.fixture(autouse=True)
def capture_deliveries(monkeypatch):
    """Replace the real network call with a recorder — this suite is testing
    the gateway's own logic (state machine, signing, outbox), not an actual
    HTTP round trip to a receiver."""
    calls = []

    def fake_post(url, *, content, headers, timeout):
        calls.append({"url": url, "body": content, "headers": headers})
        return _FakeResponse(200, "ok")

    monkeypatch.setattr(webhook_sender.httpx, "post", fake_post)
    return calls


def _create_session(client, **overrides):
    body = {
        "merchant_reference": "PI-000001",
        "amount": "150.00",
        "currency": "KWD",
        "customer_name": "Ahmed Al-Sabah",
        "contract_reference": "IC-000001",
        "description": "Overdue amount",
        "return_url": "http://merchant.example/payments/PI-000001/status",
    }
    body.update(overrides)
    resp = client.post("/gateway/checkout-sessions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _verify_signature(secret: str, header: str, raw_body: bytes) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    expected = hmac.new(
        secret.encode(), f"{parts['t']}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])


def test_create_checkout_session(client):
    session = _create_session(client)
    assert session["token"]
    assert session["checkout_url"].endswith(f"/gateway/checkout/{session['token']}")


def test_checkout_page_renders_demo_branding_and_masked_name(client):
    session = _create_session(client, customer_name="Ahmed Al-Sabah")
    resp = client.get(f"/gateway/checkout/{session['token']}")
    assert resp.status_code == 200
    assert "Demo Gateway" in resp.text
    assert "Demo Debit Network" in resp.text
    assert "A**** A*******" in resp.text
    assert "Ahmed Al-Sabah" not in resp.text  # never shown unmasked
    assert "PI-000001" in resp.text


def test_success_settlement_fires_three_webhooks_and_settles(client, capture_deliveries):
    session = _create_session(client)
    resp = client.post(
        f"/gateway/checkout/{session['token']}/simulate",
        data={"outcome": "success_settlement"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "outcome=settled" in resp.headers["location"]

    assert len(capture_deliveries) == 3
    statuses = [json.loads(c["body"])["status"] for c in capture_deliveries]
    assert statuses == ["AUTHORIZED", "CAPTURED", "SETTLED"]

    settings = get_settings()
    for call in capture_deliveries:
        assert _verify_signature(settings.webhook_secret, call["headers"]["X-Gateway-Signature"], call["body"])

    txn = client.get("/gateway/transactions/PI-000001").json()
    assert txn["status"] == "SETTLED"
    assert txn["gateway_fee"] is not None


def test_simulate_twice_is_rejected_once_consumed(client):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "customer_cancel"})
    resp = client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "success_settlement"})
    assert resp.status_code == 409


def test_duplicate_webhook_scenario_redelivers_same_event_id(client, capture_deliveries):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "duplicate_webhook"})
    assert len(capture_deliveries) == 4  # authorized, captured, settled, settled-again
    event_ids = [json.loads(c["body"])["gateway_event_id"] for c in capture_deliveries]
    assert event_ids[2] == event_ids[3]
    assert event_ids[0] != event_ids[1] != event_ids[2]


def test_invalid_signature_scenario_produces_a_bad_signature(client, capture_deliveries):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "invalid_signature"})
    assert len(capture_deliveries) == 1
    settings = get_settings()
    assert not _verify_signature(
        settings.webhook_secret, capture_deliveries[0]["headers"]["X-Gateway-Signature"], capture_deliveries[0]["body"]
    )


def test_insufficient_funds_scenario(client, capture_deliveries):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "insufficient_funds"})
    payload = json.loads(capture_deliveries[0]["body"])
    assert payload["status"] == "FAILED"
    assert payload["failure_code"] == "insufficient_funds"
    txn = client.get("/gateway/transactions/PI-000001").json()
    assert txn["status"] == "FAILED"


def test_customer_cancel_scenario(client, capture_deliveries):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "customer_cancel"})
    assert json.loads(capture_deliveries[0]["body"])["status"] == "CANCELLED"


def test_timeout_scenario(client, capture_deliveries):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "timeout"})
    assert json.loads(capture_deliveries[0]["body"])["status"] == "EXPIRED"


def test_settle_then_reverse_scenario(client, capture_deliveries):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "settle_then_reverse"})
    statuses = [json.loads(c["body"])["status"] for c in capture_deliveries]
    assert statuses == ["AUTHORIZED", "CAPTURED", "SETTLED", "REVERSED"]
    txn = client.get("/gateway/transactions/PI-000001").json()
    assert txn["status"] == "REVERSED"


def test_delayed_settlement_scenario_settles_asynchronously(client, capture_deliveries):
    session = _create_session(client)
    resp = client.post(
        f"/gateway/checkout/{session['token']}/simulate",
        data={"outcome": "delayed_settlement"},
        follow_redirects=False,
    )
    assert "outcome=captured" in resp.headers["location"]
    assert len(capture_deliveries) == 2  # authorized, captured — not settled yet

    deadline = time.time() + 5
    while time.time() < deadline and len(capture_deliveries) < 3:
        time.sleep(0.05)

    assert len(capture_deliveries) == 3
    assert json.loads(capture_deliveries[2]["body"])["status"] == "SETTLED"
    txn = client.get("/gateway/transactions/PI-000001").json()
    assert txn["status"] == "SETTLED"


def test_webhook_retry_endpoint_redelivers(client, capture_deliveries):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "customer_cancel"})
    event_id = json.loads(capture_deliveries[0]["body"])["gateway_event_id"]

    resp = client.post(f"/gateway/webhooks/{event_id}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gateway_event_id"] == event_id
    assert body["attempt_count"] == 2
    assert len(capture_deliveries) == 2


def test_unknown_checkout_token_is_404(client):
    resp = client.get("/gateway/checkout/does-not-exist")
    assert resp.status_code == 404


def test_settlement_batch_generate_includes_settled_transactions(client, capture_deliveries):
    from datetime import date

    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "success_settlement"})

    resp = client.get("/gateway/settlement-batches/generate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["settlement_date"] == date.today().isoformat()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["merchant_reference"] == "PI-000001"
    assert item["gateway_status"] == "SETTLED"
    assert float(item["net_amount"]) == pytest.approx(
        float(item["gross_amount"]) - float(item["gateway_fee"]), abs=0.01
    )


def test_settlement_batch_generate_excludes_unsettled_transactions(client):
    session = _create_session(client)
    client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "customer_cancel"})

    resp = client.get("/gateway/settlement-batches/generate")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_expired_session_cannot_be_simulated(client):
    session = _create_session(client)
    from app import database as database_module
    from app.models import CheckoutSession
    from datetime import datetime, timedelta, timezone

    db = database_module.SessionLocal()
    try:
        row = db.query(CheckoutSession).filter_by(token=session["token"]).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/gateway/checkout/{session['token']}/simulate", data={"outcome": "customer_cancel"})
    assert resp.status_code == 410
