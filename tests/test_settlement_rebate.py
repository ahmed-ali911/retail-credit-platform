"""BDR item #7 — flexible early-settlement profit waiver.

Default `early_settlement_profit_rebate_pct` is now 0.0. Any rebate staff grant
at settlement time is a deviation from that default and must go through the
existing maker-checker approval flow before the settlement finalises.
"""
import pytest

from app.models.approval import ACTION_SETTLEMENT_REBATE
from app.services import config_service as cfg
from tests.helpers import active_contract

APPROX = dict(abs=0.01)


def _quote(client, cid, **params):
    r = client.get(f"/contracts/{cid}/settlement-quote", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# 1. no rebate requested — behaves exactly as before (regression)
# --------------------------------------------------------------------------- #
def test_quote_with_no_rebate_charges_full_unearned_profit(client):
    ctx = active_contract(client, national_id="SR-1")
    cid = ctx["contract_id"]

    q = _quote(client, cid)
    assert q["profit_rebate_pct"] == pytest.approx(0.0, **APPROX)
    assert q["profit_rebate_amount"] == pytest.approx(0.0, **APPROX)
    assert q["profit_still_charged"] == pytest.approx(q["unearned_profit_total"], **APPROX)
    assert q["is_deviation"] is False


def test_settle_with_no_rebate_closes_immediately_no_approval(client):
    ctx = active_contract(client, national_id="SR-2")
    cid = ctx["contract_id"]
    q = _quote(client, cid)

    r = client.post(
        f"/contracts/{cid}/settle",
        json={"amount": q["final_payoff_amount"], "external_reference": "SR2-S"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "closed"
    assert body["closure"]["reason"] == "early_settlement"
    assert body["pending_approval"] is None
    # no approval request was created
    assert client.get("/approvals").json() == []


def test_explicit_zero_rebate_is_not_a_deviation(client):
    ctx = active_contract(client, national_id="SR-3")
    cid = ctx["contract_id"]
    q = _quote(client, cid, requested_rebate_pct=0)
    assert q["is_deviation"] is False

    r = client.post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR3-S",
            "requested_rebate_pct": 0,
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "closed"


# --------------------------------------------------------------------------- #
# 2. a requested rebate > 0 reduces the payoff and flags a deviation
# --------------------------------------------------------------------------- #
def test_quote_with_requested_pct_reduces_payoff_and_flags_deviation(client):
    ctx = active_contract(client, national_id="SR-4")
    cid = ctx["contract_id"]

    base = _quote(client, cid)
    reb = _quote(client, cid, requested_rebate_pct=0.4)

    assert reb["is_deviation"] is True
    assert reb["profit_rebate_pct"] == pytest.approx(0.4, **APPROX)
    assert reb["profit_rebate_amount"] == pytest.approx(
        base["unearned_profit_total"] * 0.4, **APPROX
    )
    # payoff drops by exactly the rebated profit
    assert reb["final_payoff_amount"] == pytest.approx(
        base["final_payoff_amount"] - reb["profit_rebate_amount"], **APPROX
    )


def test_quote_with_requested_amount(client):
    ctx = active_contract(client, national_id="SR-5")
    cid = ctx["contract_id"]
    base = _quote(client, cid)
    amt = round(base["unearned_profit_total"] / 2, 2)

    reb = _quote(client, cid, requested_rebate_amount=amt)
    assert reb["is_deviation"] is True
    assert reb["profit_rebate_amount"] == pytest.approx(amt, **APPROX)


def test_quote_rejects_both_rebate_params_at_once(client):
    ctx = active_contract(client, national_id="SR-6")
    cid = ctx["contract_id"]
    r = client.get(
        f"/contracts/{cid}/settlement-quote",
        params={"requested_rebate_pct": 0.3, "requested_rebate_amount": 5},
    )
    assert r.status_code == 422


def test_quote_rejects_amount_over_unearned_profit(client):
    ctx = active_contract(client, national_id="SR-7")
    cid = ctx["contract_id"]
    base = _quote(client, cid)
    r = client.get(
        f"/contracts/{cid}/settlement-quote",
        params={"requested_rebate_amount": base["unearned_profit_total"] + 100},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# 3. settling with a deviation creates a pending request, moves nothing
# --------------------------------------------------------------------------- #
def test_settle_with_deviation_creates_pending_request_and_does_not_close(client, db):
    ctx = active_contract(client, national_id="SR-8")
    cid = ctx["contract_id"]
    q = _quote(client, cid, requested_rebate_pct=0.5)
    assert q["is_deviation"] is True

    rec_before = client.get(f"/contracts/{cid}/receivable").json()
    unearned_before = client.get(f"/contracts/{cid}").json()["unearned_profit_balance"]

    r = client.post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR8-S",
            "requested_rebate_pct": 0.5,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["closure"] is None
    assert body["pending_approval"]["action_type"] == ACTION_SETTLEMENT_REBATE
    assert body["pending_approval"]["status"] == "pending"

    # contract untouched — not closed, no money moved
    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "active"
    assert contract["closure"] is None
    assert contract["unearned_profit_balance"] == pytest.approx(unearned_before, **APPROX)
    assert client.get(f"/contracts/{cid}/receivable").json() == rec_before

    # a second settle attempt while one is pending is rejected
    dup = client.post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR8-S2",
            "requested_rebate_pct": 0.5,
        },
    )
    assert dup.status_code == 409


# --------------------------------------------------------------------------- #
# 4. maker != checker
# --------------------------------------------------------------------------- #
def test_requester_cannot_approve_their_own_settlement_rebate(client):
    ctx = active_contract(client, national_id="SR-9")
    cid = ctx["contract_id"]
    q = _quote(client, cid, requested_rebate_pct=0.3)
    req = client.post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR9-S",
            "requested_rebate_pct": 0.3,
        },
    ).json()

    dup = client.post(f"/approvals/{req['pending_approval']['id']}/approve")
    assert dup.status_code == 409
    assert client.get(f"/contracts/{cid}").json()["status"] == "active"


# --------------------------------------------------------------------------- #
# 5. a different approver executes the settlement, all hooks fire
# --------------------------------------------------------------------------- #
def test_different_approver_executes_settlement_and_all_hooks_fire(client, client_as, db):
    from app.models.ledger import LedgerEntry, LedgerEntryType

    ctx = active_contract(client, national_id="SR-10")
    cid = ctx["contract_id"]

    q = client_as("finance_officer").get(
        f"/contracts/{cid}/settlement-quote", params={"requested_rebate_pct": 0.5}
    ).json()
    expected_rebate = q["profit_rebate_amount"]
    assert expected_rebate > 0

    req = client_as("finance_officer").post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR10-S",
            "requested_rebate_pct": 0.5,
        },
    ).json()

    ok = client_as("credit_manager").post(f"/approvals/{req['pending_approval']['id']}/approve")
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"

    # contract closed via early settlement
    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "closed"
    assert contract["closure"]["reason"] == "early_settlement"
    assert contract["unearned_profit_balance"] == pytest.approx(0.0, **APPROX)

    rec = client.get(f"/contracts/{cid}/receivable").json()
    assert rec["outstanding_receivable"] == pytest.approx(0.0, **APPROX)

    # ledger — the rebated profit is recorded separately (S-4 hook)
    rebated = (
        db.query(LedgerEntry)
        .filter(
            LedgerEntry.contract_id == cid,
            LedgerEntry.entry_type == LedgerEntryType.profit_rebated,
        )
        .all()
    )
    assert len(rebated) == 1
    assert float(rebated[0].amount) == pytest.approx(expected_rebate, **APPROX)

    # accounting event — one early_settlement event
    ev = client.get(
        "/accounting/events", params={"contract_id": cid, "event_type": "early_settlement"}
    ).json()
    assert len(ev) == 1

    # audit — the settlement is recorded, referencing the approval
    logs = client.get(
        "/audit/events",
        params={"entity_type": "installment_contract", "entity_id": str(cid)},
    ).json()
    settled = [e for e in logs if e["action"] == "contract.settled"]
    assert len(settled) == 1
    assert settled[0]["after_value"]["approval_request_id"] == req["pending_approval"]["id"]


def test_rejecting_the_rebate_leaves_the_contract_active(client, client_as):
    ctx = active_contract(client, national_id="SR-11")
    cid = ctx["contract_id"]
    q = client_as("finance_officer").get(
        f"/contracts/{cid}/settlement-quote", params={"requested_rebate_pct": 0.6}
    ).json()
    req = client_as("finance_officer").post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR11-S",
            "requested_rebate_pct": 0.6,
        },
    ).json()

    rej = client_as("credit_manager").post(
        f"/approvals/{req['pending_approval']['id']}/reject", json={"reason": "too much"}
    )
    assert rej.status_code == 200
    assert client.get(f"/contracts/{cid}").json()["status"] == "active"


# --------------------------------------------------------------------------- #
# 6. server recomputes the quote at approval time (no stale value trusted)
# --------------------------------------------------------------------------- #
def test_quote_recomputed_at_approval_even_if_balance_changed(client, client_as):
    ctx = active_contract(client, national_id="SR-12")
    cid = ctx["contract_id"]

    q = client_as("finance_officer").get(
        f"/contracts/{cid}/settlement-quote", params={"requested_rebate_pct": 0.5}
    ).json()
    req = client_as("finance_officer").post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR12-S",
            "requested_rebate_pct": 0.5,
        },
    ).json()

    # customer pays an installment between request and approval — the payoff
    # the approver executes must reflect the fresh balance, not the stale quote
    client.post(
        f"/contracts/{cid}/payments",
        json={"amount": ctx["schedule"][0]["total"], "external_reference": "SR12-P1"},
    )

    ok = client_as("credit_manager").post(f"/approvals/{req['pending_approval']['id']}/approve")
    assert ok.status_code == 200
    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "closed"
    assert contract["closure"]["reason"] == "early_settlement"
    # the schedule is fully retired and nothing is left outstanding, regardless
    # of the mid-flight payment — the quote was recomputed server-side
    assert contract["unearned_profit_balance"] == pytest.approx(0.0, **APPROX)
    rec = client.get(f"/contracts/{cid}/receivable").json()
    assert rec["outstanding_receivable"] == pytest.approx(0.0, **APPROX)


# --------------------------------------------------------------------------- #
# 7. the new action type shows up in the approvals list / filter
# --------------------------------------------------------------------------- #
def test_settlement_rebate_appears_in_approvals_list(client, client_as):
    ctx = active_contract(client, national_id="SR-13")
    cid = ctx["contract_id"]
    q = client_as("finance_officer").get(
        f"/contracts/{cid}/settlement-quote", params={"requested_rebate_pct": 0.25}
    ).json()
    client_as("finance_officer").post(
        f"/contracts/{cid}/settle",
        json={
            "amount": q["final_payoff_amount"],
            "external_reference": "SR13-S",
            "requested_rebate_pct": 0.25,
        },
    )

    mgr = client_as("credit_manager")
    rows = mgr.get("/approvals", params={"action_type": ACTION_SETTLEMENT_REBATE}).json()
    assert len(rows) == 1
    assert rows[0]["entity_type"] == "installment_contract"
    assert rows[0]["entity_id"] == str(cid)
    assert rows[0]["payload"]["requested_rebate_pct"] == 0.25
