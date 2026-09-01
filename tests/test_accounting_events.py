"""Accounting-event boundary (fills Gap Matrix G-07 / assessment §22).

Events are generated automatically and additively from things that already
happen; posting to the mock ERP adapter is a separate on-demand job. These tests
lock in the amounts, the idempotency, and the job behaviour.
"""
import pytest

from tests.helpers import active_contract, created_contract

APPROX = dict(abs=0.005)


def _events(client, contract_id=None, **params):
    if contract_id is not None:
        params["contract_id"] = contract_id
    r = client.get("/accounting/events", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _by_type(events):
    out = {}
    for e in events:
        out.setdefault(e["event_type"], []).append(e)
    return out


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
def test_delivery_emits_contract_activated_and_down_payment(client):
    ctx = active_contract(client, national_id="AE-1")  # cash 1200, DP 300, profit 81
    events = _by_type(_events(client, ctx["contract_id"]))

    assert len(events["contract_activated"]) == 1
    assert len(events["down_payment_received"]) == 1
    # sale price = cash price + total profit = 1200 + 81
    assert events["contract_activated"][0]["amount"] == pytest.approx(1281.0, **APPROX)
    assert events["down_payment_received"][0]["amount"] == pytest.approx(300.0, **APPROX)
    assert events["contract_activated"][0]["currency"] == "KWD"
    assert events["contract_activated"][0]["accounting_status"] == "pending"


def test_payment_emits_payment_received_and_profit_recognized(client):
    ctx = active_contract(client, national_id="AE-2")
    cid = ctx["contract_id"]
    first = ctx["schedule"][0]
    total = first["total"]

    client.post(
        f"/contracts/{cid}/payments",
        json={"amount": total, "external_reference": "AE2-P1"},
    )
    events = _by_type(_events(client, cid))

    assert len(events["payment_received"]) == 1
    assert events["payment_received"][0]["amount"] == pytest.approx(total, **APPROX)

    assert len(events["profit_recognized"]) == 1
    # the profit portion actually allocated by THIS payment, not the full schedule
    assert events["profit_recognized"][0]["amount"] == pytest.approx(
        first["profit_component"], **APPROX
    )
    assert events["profit_recognized"][0]["amount"] < first["total"]


def test_late_fee_charge_and_waiver_each_emit_one_event(client, client_as):
    ctx = active_contract(client, national_id="AE-3")
    cid = ctx["contract_id"]
    client.post("/jobs/assess-overdue", json={"as_of": "2026-10-15"})

    charged = _events(client, cid, event_type="late_fee_charged")
    assert len(charged) == 1
    fee_amount = charged[0]["amount"]
    assert fee_amount > 0

    late_fee_id = client.get(f"/contracts/{cid}").json()["late_fee_charges"][0]["id"]
    req = client_as("finance_officer").post(
        f"/late-fees/{late_fee_id}/request-waiver", json={"reason": "goodwill"}
    )
    client_as("credit_manager").post(f"/approvals/{req.json()['id']}/approve")

    waived = _events(client, cid, event_type="late_fee_waived")
    assert len(waived) == 1
    assert waived[0]["amount"] == pytest.approx(fee_amount, **APPROX)


def test_settlement_emits_one_event(client):
    ctx = active_contract(client, national_id="AE-4")
    cid = ctx["contract_id"]
    q = client.get(f"/contracts/{cid}/settlement-quote").json()
    client.post(
        f"/contracts/{cid}/settle",
        json={"amount": q["final_payoff_amount"], "external_reference": "AE4-S"},
    )

    ev = _events(client, cid, event_type="early_settlement")
    assert len(ev) == 1
    # plain early settlement records no financial_adjustment -> event amount 0.00
    assert ev[0]["amount"] == pytest.approx(0.0, **APPROX)


def test_cancellation_emits_one_event_with_signed_refund(client):
    ctx = created_contract(client, national_id="AE-5")  # DP 300, refund pct 1.0
    cid = ctx["contract_id"]
    body = client.post(f"/contracts/{cid}/cancel").json()

    ev = _events(client, cid, event_type="cancellation")
    assert len(ev) == 1
    # positive: refund owed to the customer
    assert ev[0]["amount"] == pytest.approx(body["down_payment_refund"], **APPROX)
    assert ev[0]["amount"] == pytest.approx(300.0, **APPROX)


def test_return_emits_one_event_with_signed_adjustment(client):
    ctx = active_contract(client, national_id="AE-6")  # return refund pct 0.0
    cid = ctx["contract_id"]
    body = client.post(f"/contracts/{cid}/return").json()

    ev = _events(client, cid, event_type="return")
    assert len(ev) == 1
    # negative: customer still owes the settlement-shape payoff
    assert ev[0]["amount"] < 0
    assert ev[0]["amount"] == pytest.approx(body["net_adjustment"], **APPROX)


# --------------------------------------------------------------------------- #
# idempotency
# --------------------------------------------------------------------------- #
def test_replaying_a_payment_never_duplicates_an_accounting_event(client):
    ctx = active_contract(client, national_id="AE-7")
    cid = ctx["contract_id"]
    total = ctx["schedule"][0]["total"]
    body = {"amount": total, "external_reference": "AE7-DUP"}

    client.post(f"/contracts/{cid}/payments", json=body)
    replay = client.post(f"/contracts/{cid}/payments", json=body)
    assert replay.json()["replayed"] is True

    assert len(_events(client, cid, event_type="payment_received")) == 1
    assert len(_events(client, cid, event_type="profit_recognized")) == 1


def test_running_assess_overdue_twice_never_duplicates_the_charge_event(client):
    ctx = active_contract(client, national_id="AE-8")
    cid = ctx["contract_id"]
    client.post("/jobs/assess-overdue", json={"as_of": "2026-10-15"})
    client.post("/jobs/assess-overdue", json={"as_of": "2026-10-20"})

    assert len(_events(client, cid, event_type="late_fee_charged")) == 1


# --------------------------------------------------------------------------- #
# posting job
# --------------------------------------------------------------------------- #
def test_posting_job_moves_pending_to_posted(client):
    ctx = active_contract(client, national_id="AE-9")
    cid = ctx["contract_id"]

    before = _events(client, cid)
    assert before and all(e["accounting_status"] == "pending" for e in before)

    result = client.post("/jobs/post-accounting-events")
    assert result.status_code == 200, result.text
    assert result.json()["posted"] == len(before)
    assert result.json()["failed"] == 0

    after = _events(client, cid)
    for e in after:
        assert e["accounting_status"] == "posted"
        assert e["external_gl_reference"].startswith("MOCK-GL-")


def test_posting_job_is_idempotent(client):
    active_contract(client, national_id="AE-10")

    first = client.post("/jobs/post-accounting-events").json()
    assert first["posted"] == 2  # contract_activated + down_payment_received

    second = client.post("/jobs/post-accounting-events").json()
    assert second["events_considered"] == 0
    assert second["posted"] == 0

    # references are stable and unique per event
    refs = [e["external_gl_reference"] for e in _events(client)]
    assert len(refs) == len(set(refs))


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #
def test_rbac_on_accounting_endpoints(client, client_as):
    active_contract(client, national_id="AE-11")

    sales = client_as("sales_employee")
    assert sales.get("/accounting/events").status_code == 403
    assert sales.post("/jobs/post-accounting-events").status_code == 403

    fin = client_as("finance_officer")
    assert fin.get("/accounting/events").status_code == 200
    # posting is admin-only, like /jobs/assess-overdue
    assert fin.post("/jobs/post-accounting-events").status_code == 403
