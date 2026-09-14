"""Mock Payment Gateway — checkpoint 2 scope: reaching the configured final-
allocation status calls the EXISTING record_payment()/allocate() engine
(never a second implementation), reversal via compensating records,
Collections/Promise-to-Pay integration, the gateway_fee_recognized /
payment_reversed / refund_completed / settlement_difference accounting
events, and the settlement-batch reconciliation engine (a distinct process
from the bank-statement module — see models/gateway_settlement.py).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.accounting import AccountingEvent, AccountingEventType
from app.models.gateway_settlement import (
    GatewayReconciliationItemStatus,
    ReconciliationItem,
    ReconciliationOutcome,
)
from app.models.ledger import LedgerEntry, LedgerRelatedAction
from app.models.payment import Payment, PaymentAllocation, PaymentSource, PaymentStatus
from tests.helpers import active_contract, first_due_date

APPROX = dict(abs=0.01)


# --------------------------------------------------------------------------- #
# Shared helpers (mirrors tests/test_payment_gateway.py's checkpoint-1 helpers)
# --------------------------------------------------------------------------- #
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


def _settle_intent(
    client, cid, *, idem, purpose="current_installment", amount=None,
    gateway_fee="0.00", gateway_txn_ref=None, settlement_date: date | None = None,
):
    intent = _open_intent(client, cid, purpose=purpose, amount=amount, idem=idem)
    _checkout(client, intent["id"])
    ref = intent["payment_reference"]
    txn_ref = gateway_txn_ref or f"GWTXN-{idem}"
    settle_dt = datetime.combine(
        settlement_date or datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc
    )

    for i, status in enumerate(["AUTHORIZED", "CAPTURED", "SETTLED"]):
        extra = {"gateway_transaction_reference": txn_ref}
        if status == "SETTLED":
            extra["gateway_fee"] = gateway_fee
            extra["settled_amount"] = str(intent["requested_amount"])
            extra["settlement_timestamp"] = settle_dt.isoformat()
        resp = _post_webhook(client, _webhook_body(
            gateway_event_id=f"evt-{idem}-{i}", payment_reference=ref, status=status,
            event_type=f"payment.{status.lower()}", **extra,
        ))
        assert resp.status_code == 200, resp.text
        assert resp.json()["processing_status"] == "PROCESSED", resp.text
    return intent


def _reverse_intent(client, intent, *, idem, status="REVERSED"):
    resp = _post_webhook(client, _webhook_body(
        gateway_event_id=f"evt-{idem}-rev", payment_reference=intent["payment_reference"],
        status=status, event_type=f"payment.{status.lower()}",
    ))
    assert resp.status_code == 200, resp.text
    assert resp.json()["processing_status"] == "PROCESSED", resp.text
    return resp


def _assess_overdue(client, as_of):
    if isinstance(as_of, date):
        as_of = as_of.isoformat()
    r = client.post("/jobs/assess-overdue", json={"as_of": as_of})
    assert r.status_code == 200, r.text
    return r.json()


def _open_cases(client, cid):
    return client.get("/collections/cases", params={"contract_id": cid, "status": "open"}).json()


def _closed_cases(client, cid):
    return client.get("/collections/cases", params={"contract_id": cid, "status": "closed"}).json()


# --------------------------------------------------------------------------- #
# Allocation reuse: settling calls the EXISTING record_payment()/allocate()
# --------------------------------------------------------------------------- #
def test_settled_payment_creates_payment_via_existing_engine_and_allocates(client, db):
    ctx = active_contract(client, national_id="PGS-1")
    cid = ctx["contract_id"]
    first_total = ctx["schedule"][0]["total"]

    before = client.get(f"/contracts/{cid}/receivable").json()
    intent = _settle_intent(client, cid, idem="PGS-1")
    after = client.get(f"/contracts/{cid}/receivable").json()

    assert after["outstanding_receivable"] == pytest.approx(
        before["outstanding_receivable"] - first_total, **APPROX
    )

    payment = db.execute(
        select(Payment).where(Payment.payment_intent_id == intent["id"])
    ).scalar_one()
    assert payment.source == PaymentSource.gateway
    assert payment.status == PaymentStatus.applied
    allocations = db.execute(
        select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
    ).scalars().all()
    assert len(allocations) >= 1
    assert float(sum(a.total for a in allocations)) == pytest.approx(first_total, **APPROX)


def test_settlement_updates_balance_only_once_even_with_duplicate_webhook(client, db):
    ctx = active_contract(client, national_id="PGS-2")
    cid = ctx["contract_id"]
    intent = _settle_intent(client, cid, idem="PGS-2")
    after_first = client.get(f"/contracts/{cid}/receivable").json()

    # Redeliver the exact SETTLED event (same gateway_event_id) a second time.
    dup = _post_webhook(client, _webhook_body(
        gateway_event_id="evt-PGS-2-2", payment_reference=intent["payment_reference"],
        status="SETTLED", event_type="payment.settled",
    ))
    assert dup.status_code == 200
    after_second = client.get(f"/contracts/{cid}/receivable").json()
    assert after_second == after_first

    payments = db.execute(
        select(Payment).where(
            Payment.payment_intent_id == intent["id"], Payment.source == PaymentSource.gateway
        )
    ).scalars().all()
    assert len(payments) == 1


def test_failed_payment_still_does_not_update_balances(client, db):
    ctx = active_contract(client, national_id="PGS-3")
    cid = ctx["contract_id"]
    before = client.get(f"/contracts/{cid}/receivable").json()
    intent = _open_intent(client, cid, idem="PGS-3")
    _checkout(client, intent["id"])
    _post_webhook(client, _webhook_body(
        gateway_event_id="evt-PGS-3-fail", payment_reference=intent["payment_reference"],
        status="FAILED", event_type="payment.failed", failure_code="insufficient_funds",
    ))
    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after == before
    assert db.execute(
        select(Payment).where(Payment.payment_intent_id == intent["id"])
    ).scalar_one_or_none() is None


def test_gateway_fee_recognized_accounting_event_emitted_on_settlement(client, db):
    ctx = active_contract(client, national_id="PGS-4")
    cid = ctx["contract_id"]
    _settle_intent(client, cid, idem="PGS-4", gateway_fee="1.31")

    events = db.execute(
        select(AccountingEvent).where(
            AccountingEvent.event_type == AccountingEventType.gateway_fee_recognized,
            AccountingEvent.contract_id == cid,
        )
    ).scalars().all()
    assert len(events) == 1
    assert float(events[0].amount) == pytest.approx(1.31, **APPROX)


def test_kwd_decimal_precision_is_retained_through_settlement(client, db):
    ctx = active_contract(client, national_id="PGS-5")
    cid = ctx["contract_id"]
    first_total = ctx["schedule"][0]["total"]  # e.g. 87.46 — deliberately non-round
    intent = _settle_intent(client, cid, idem="PGS-5")
    assert float(intent["requested_amount"]) == pytest.approx(first_total, abs=0.001)
    payment = db.execute(
        select(Payment).where(Payment.payment_intent_id == intent["id"])
    ).scalar_one()
    assert str(payment.amount) == f"{first_total:.2f}"


# --------------------------------------------------------------------------- #
# Collections integration
# --------------------------------------------------------------------------- #
def test_partial_gateway_payment_does_not_close_the_case(client, db):
    ctx = active_contract(client, national_id="PGS-6")
    cid = ctx["contract_id"]
    _assess_overdue(client, first_due_date(client, cid) + timedelta(days=6))
    assert _open_cases(client, cid)

    options = client.get(f"/contracts/{cid}/payment-options").json()
    partial_amount = round(options["overdue_amount"] / 2, 2)
    _settle_intent(client, cid, idem="PGS-6", purpose="partial", amount=partial_amount)

    assert _open_cases(client, cid), "case must stay open on a partial payment"


def test_full_overdue_gateway_payment_closes_the_case(client, db):
    ctx = active_contract(client, national_id="PGS-7")
    cid = ctx["contract_id"]
    _assess_overdue(client, first_due_date(client, cid) + timedelta(days=6))
    assert _open_cases(client, cid)

    _settle_intent(client, cid, idem="PGS-7", purpose="overdue_amount")
    assert _open_cases(client, cid) == []
    assert _closed_cases(client, cid)


def _log_promise(client, case_id, *, promised_amount, promised_date):
    r = client.post(f"/collections/cases/{case_id}/activities", json={
        "activity_type": "promise_to_pay", "notes": "will pay",
        "promised_amount": promised_amount, "promised_date": promised_date,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_settled_payment_marks_eligible_promise_to_pay_kept(client, db):
    ctx = active_contract(client, national_id="PGS-8")
    cid = ctx["contract_id"]
    _assess_overdue(client, first_due_date(client, cid) + timedelta(days=6))
    case_id = _open_cases(client, cid)[0]["id"]
    promised_on = (date.today() + timedelta(days=15)).isoformat()
    promise = _log_promise(client, case_id, promised_amount=50.0, promised_date=promised_on)

    _settle_intent(client, cid, idem="PGS-8", purpose="partial", amount=50.0)

    detail = client.get(f"/collections/cases/{case_id}").json()
    kept = next(a for a in detail["activities"] if a["id"] == promise["id"])
    assert kept["promise_status"] == "kept"


def test_merely_initiated_payment_does_not_mark_promise_kept(client, db):
    ctx = active_contract(client, national_id="PGS-9")
    cid = ctx["contract_id"]
    _assess_overdue(client, first_due_date(client, cid) + timedelta(days=6))
    case_id = _open_cases(client, cid)[0]["id"]
    promised_on = (date.today() + timedelta(days=15)).isoformat()
    promise = _log_promise(client, case_id, promised_amount=50.0, promised_date=promised_on)

    intent = _open_intent(client, cid, idem="PGS-9", purpose="partial", amount=50.0)
    _checkout(client, intent["id"])
    # Only AUTHORIZED — never reaches SETTLED.
    _post_webhook(client, _webhook_body(
        gateway_event_id="evt-PGS-9-0", payment_reference=intent["payment_reference"],
        status="AUTHORIZED", event_type="payment.authorized",
    ))

    detail = client.get(f"/collections/cases/{case_id}").json()
    still_pending = next(a for a in detail["activities"] if a["id"] == promise["id"])
    assert still_pending["promise_status"] == "pending"


# --------------------------------------------------------------------------- #
# Reversal
# --------------------------------------------------------------------------- #
def test_reversal_restores_balances_via_compensating_records(client, db):
    ctx = active_contract(client, national_id="PGS-10")
    cid = ctx["contract_id"]
    before = client.get(f"/contracts/{cid}/receivable").json()
    intent = _settle_intent(client, cid, idem="PGS-10")
    after_settle = client.get(f"/contracts/{cid}/receivable").json()
    assert after_settle["outstanding_receivable"] < before["outstanding_receivable"]

    _reverse_intent(client, intent, idem="PGS-10")
    after_reversal = client.get(f"/contracts/{cid}/receivable").json()
    assert after_reversal["outstanding_receivable"] == pytest.approx(
        before["outstanding_receivable"], **APPROX
    )

    original = db.execute(
        select(Payment).where(
            Payment.payment_intent_id == intent["id"], Payment.status == PaymentStatus.reversed,
            Payment.external_reference == intent["payment_reference"],
        )
    ).scalar_one()
    original_allocations = db.execute(
        select(PaymentAllocation).where(PaymentAllocation.payment_id == original.id)
    ).scalars().all()
    assert all(a.reversed_by_allocation_id is not None for a in original_allocations)

    reversal_payment = db.execute(
        select(Payment).where(
            Payment.payment_intent_id == intent["id"],
            Payment.external_reference == f"{intent['payment_reference']}-REV",
        )
    ).scalar_one()
    assert reversal_payment.status == PaymentStatus.reversed

    reversal_ledger = db.execute(
        select(LedgerEntry).where(LedgerEntry.related_action == LedgerRelatedAction.reversal)
    ).scalars().all()
    assert len(reversal_ledger) >= 1

    events = db.execute(
        select(AccountingEvent).where(
            AccountingEvent.event_type == AccountingEventType.payment_reversed,
            AccountingEvent.contract_id == cid,
        )
    ).scalars().all()
    assert len(events) == 1


def test_reversal_reopens_the_case(client, db):
    ctx = active_contract(client, national_id="PGS-11")
    cid = ctx["contract_id"]
    _assess_overdue(client, first_due_date(client, cid) + timedelta(days=6))
    assert _open_cases(client, cid)

    intent = _settle_intent(client, cid, idem="PGS-11", purpose="overdue_amount")
    assert _open_cases(client, cid) == [], "settling the only overdue amount should close the case"

    _reverse_intent(client, intent, idem="PGS-11")
    assert _open_cases(client, cid), "reversing the settled payment must reopen the case"


def test_refunded_status_also_triggers_reversal_with_refund_event(client, db):
    ctx = active_contract(client, national_id="PGS-12")
    cid = ctx["contract_id"]
    before = client.get(f"/contracts/{cid}/receivable").json()
    intent = _settle_intent(client, cid, idem="PGS-12")
    _reverse_intent(client, intent, idem="PGS-12", status="REFUNDED")

    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after["outstanding_receivable"] == pytest.approx(
        before["outstanding_receivable"], **APPROX
    )
    events = db.execute(
        select(AccountingEvent).where(
            AccountingEvent.event_type == AccountingEventType.refund_completed,
            AccountingEvent.contract_id == cid,
        )
    ).scalars().all()
    assert len(events) == 1


# --------------------------------------------------------------------------- #
# Settlement-batch reconciliation
# --------------------------------------------------------------------------- #
def _import_batch(client, *, batch_reference, settlement_date_, items):
    resp = client.post("/payments/settlement-batches", json={
        "batch_reference": batch_reference,
        "settlement_date": settlement_date_.isoformat(),
        "currency": "KWD",
        "items": items,
    })
    return resp


def test_reconciliation_matched_marks_payment_reconciled(client, db):
    ctx = active_contract(client, national_id="PGS-13")
    cid = ctx["contract_id"]
    today = date.today()
    intent = _settle_intent(
        client, cid, idem="PGS-13", gateway_fee="1.00",
        gateway_txn_ref="GWTXN-PGS-13", settlement_date=today,
    )
    gross = float(intent["requested_amount"])

    resp = _import_batch(client, batch_reference="BATCH-PGS-13", settlement_date_=today, items=[{
        "gateway_transaction_reference": "GWTXN-PGS-13",
        "merchant_reference": intent["payment_reference"],
        "settlement_date": today.isoformat(),
        "gross_amount": gross,
        "gateway_fee": 1.00,
        "net_amount": round(gross - 1.00, 2),
        "currency": "KWD",
        "gateway_status": "SETTLED",
    }])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["matched"] == 1
    assert body["exceptions"] == 0

    payment = db.execute(
        select(Payment).where(Payment.payment_intent_id == intent["id"])
    ).scalar_one()
    assert payment.reconciliation_status.value == "reconciled"


def test_reconciliation_amount_mismatch_resolved_via_maker_checker(client, client_as, db):
    ctx = active_contract(client, national_id="PGS-14")
    cid = ctx["contract_id"]
    today = date.today()
    intent = _settle_intent(
        client, cid, idem="PGS-14", gateway_txn_ref="GWTXN-PGS-14", settlement_date=today,
    )
    gross = float(intent["requested_amount"])
    wrong_gross = round(gross + 5.00, 2)

    resp = _import_batch(client, batch_reference="BATCH-PGS-14", settlement_date_=today, items=[{
        "gateway_transaction_reference": "GWTXN-PGS-14",
        "merchant_reference": intent["payment_reference"],
        "settlement_date": today.isoformat(),
        "gross_amount": wrong_gross,
        "gateway_fee": 0.0,
        "net_amount": wrong_gross,
        "currency": "KWD",
        "gateway_status": "SETTLED",
    }])
    assert resp.status_code == 201, resp.text
    assert resp.json()["exceptions"] == 1

    item = db.execute(
        select(ReconciliationItem).where(
            ReconciliationItem.merchant_reference == intent["payment_reference"]
        )
    ).scalar_one()
    assert item.outcome == ReconciliationOutcome.amount_mismatch
    assert float(item.variance_amount) == pytest.approx(5.00, **APPROX)

    fo = client_as("finance_officer")
    req = fo.post(f"/payments/reconciliation-items/{item.id}/resolve", json={
        "reason": "confirmed with gateway ops", "comments": "gateway over-reported by 5.00",
    })
    assert req.status_code == 201, req.text
    approval_id = req.json()["id"]

    assert fo.post(f"/approvals/{approval_id}/approve").status_code == 409  # same requester

    ok = client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    assert ok.status_code == 200, ok.text

    db.expire_all()
    resolved = db.get(ReconciliationItem, item.id)
    assert resolved.status == GatewayReconciliationItemStatus.resolved

    diff_events = db.execute(
        select(AccountingEvent).where(
            AccountingEvent.event_type == AccountingEventType.settlement_difference,
            AccountingEvent.contract_id == cid,
        )
    ).scalars().all()
    assert len(diff_events) == 1
    assert float(diff_events[0].amount) == pytest.approx(5.00, **APPROX)


def test_reconciliation_missing_in_internal_system(client, db):
    today = date.today()
    resp = _import_batch(client, batch_reference="BATCH-PGS-15", settlement_date_=today, items=[{
        "gateway_transaction_reference": "GWTXN-GHOST",
        "merchant_reference": "PI-DOES-NOT-EXIST",
        "settlement_date": today.isoformat(),
        "gross_amount": 42.00,
        "gateway_fee": 0.0,
        "net_amount": 42.00,
        "currency": "KWD",
        "gateway_status": "SETTLED",
    }])
    assert resp.status_code == 201, resp.text
    item = db.execute(
        select(ReconciliationItem).where(
            ReconciliationItem.merchant_reference == "PI-DOES-NOT-EXIST"
        )
    ).scalar_one()
    assert item.outcome == ReconciliationOutcome.missing_in_internal_system


def test_reconciliation_missing_in_gateway_sweep(client, db):
    ctx = active_contract(client, national_id="PGS-16")
    cid = ctx["contract_id"]
    today = date.today()
    intent = _settle_intent(
        client, cid, idem="PGS-16", gateway_txn_ref="GWTXN-PGS-16", settlement_date=today,
    )

    # An empty batch for the same date — the gateway's feed never mentions
    # our own settled transaction at all.
    resp = _import_batch(client, batch_reference="BATCH-PGS-16", settlement_date_=today, items=[])
    assert resp.status_code == 201, resp.text
    assert resp.json()["missing_in_gateway"] == 1

    item = db.execute(
        select(ReconciliationItem).where(
            ReconciliationItem.merchant_reference == intent["payment_reference"]
        )
    ).scalar_one()
    assert item.outcome == ReconciliationOutcome.missing_in_gateway


def test_duplicate_batch_reference_is_rejected(client):
    today = date.today()
    first = _import_batch(client, batch_reference="BATCH-DUP", settlement_date_=today, items=[])
    assert first.status_code == 201
    second = _import_batch(client, batch_reference="BATCH-DUP", settlement_date_=today, items=[])
    assert second.status_code == 409
