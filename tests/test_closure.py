import pytest

from app.services import config_service as cfg
from tests.helpers import active_contract, created_contract

APPROX = dict(abs=0.005)


def _receivable(client, cid):
    return client.get(f"/contracts/{cid}/receivable").json()


def _quote(client, cid):
    r = client.get(f"/contracts/{cid}/settlement-quote")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Early settlement
# --------------------------------------------------------------------------- #
def test_settlement_quote_reconciles_on_partially_paid_contract(client):
    ctx = active_contract(client, national_id="CL-1")
    cid = ctx["contract_id"]
    first_total = ctx["schedule"][0]["total"]

    # pay installment 1 so the contract is partially paid
    client.post(f"/contracts/{cid}/payments",
                json={"amount": first_total, "external_reference": "CL1-P1"})

    q = _quote(client, cid)
    # principal + late fees + profit-still-charged == final payoff, exactly
    assert q["outstanding_principal"] + q["outstanding_late_fees"] + \
        q["profit_still_charged"] == pytest.approx(q["final_payoff_amount"], **APPROX)
    # rebate + still-charged == the full unearned profit
    assert q["profit_rebate_amount"] + q["profit_still_charged"] == pytest.approx(
        q["unearned_profit_total"], **APPROX
    )
    # unearned profit total matches the contract's unearned_profit_balance
    contract = client.get(f"/contracts/{cid}").json()
    assert q["unearned_profit_total"] == pytest.approx(
        contract["unearned_profit_balance"], **APPROX
    )


def test_settle_with_exact_quoted_amount_closes_and_zeroes_receivable(client):
    ctx = active_contract(client, national_id="CL-2")
    cid = ctx["contract_id"]
    q = _quote(client, cid)

    r = client.post(f"/contracts/{cid}/settle", json={
        "amount": q["final_payoff_amount"], "external_reference": "CL2-SETTLE"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "closed"
    assert body["closure"]["reason"] == "early_settlement"

    rec = _receivable(client, cid)
    assert rec["outstanding_principal"] == pytest.approx(0.0, **APPROX)
    assert rec["outstanding_profit"] == pytest.approx(0.0, **APPROX)
    assert rec["outstanding_receivable"] == pytest.approx(0.0, **APPROX)
    assert rec["total_installments_remaining"] == 0

    contract = client.get(f"/contracts/{cid}").json()
    assert contract["unearned_profit_balance"] == pytest.approx(0.0, **APPROX)
    assert contract["closure"]["reason"] == "early_settlement"


def test_settle_with_wrong_amount_is_rejected(client):
    ctx = active_contract(client, national_id="CL-3")
    cid = ctx["contract_id"]
    q = _quote(client, cid)

    r = client.post(f"/contracts/{cid}/settle", json={
        "amount": round(q["final_payoff_amount"] + 25.00, 2),
        "external_reference": "CL3-BAD"})
    assert r.status_code == 422
    # contract untouched
    assert client.get(f"/contracts/{cid}").json()["status"] == "active"


def test_rebate_pct_config_change_changes_quoted_payoff(client, set_config):
    ctx = active_contract(client, national_id="CL-4")
    cid = ctx["contract_id"]

    base = _quote(client, cid)["final_payoff_amount"]

    set_config(cfg.KEY_EARLY_SETTLEMENT_REBATE_PCT, 0.0)  # nothing waived
    no_rebate = _quote(client, cid)["final_payoff_amount"]

    set_config(cfg.KEY_EARLY_SETTLEMENT_REBATE_PCT, 1.0)  # all profit waived
    full_rebate = _quote(client, cid)["final_payoff_amount"]

    assert no_rebate > base > full_rebate
    # with the whole profit waived, payoff == outstanding principal (+ late fees)
    assert full_rebate == pytest.approx(_quote(client, cid)["outstanding_principal"],
                                        **APPROX)


# --------------------------------------------------------------------------- #
# Cancellation (pre-delivery)
# --------------------------------------------------------------------------- #
def test_cancel_before_delivery_computes_refund_and_closes(client):
    ctx = created_contract(client, national_id="CL-5", down_payment_amount=300)
    cid = ctx["contract_id"]

    r = client.post(f"/contracts/{cid}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "closed"
    # default down_payment_refund_pct_cancellation = 1.0
    assert body["down_payment_refund"] == pytest.approx(300.0, **APPROX)
    assert body["closure"]["reason"] == "cancellation"
    assert body["closure"]["financial_adjustment"] == pytest.approx(300.0, **APPROX)


def test_cancel_after_delivery_returns_409_pointing_at_return(client):
    ctx = active_contract(client, national_id="CL-6")
    cid = ctx["contract_id"]
    r = client.post(f"/contracts/{cid}/cancel")
    assert r.status_code == 409
    assert "return" in r.json()["detail"].lower()


def test_cancel_refund_pct_config_change(client, set_config):
    set_config(cfg.KEY_DP_REFUND_PCT_CANCELLATION, 0.25)
    ctx = created_contract(client, national_id="CL-7", down_payment_amount=400)
    cid = ctx["contract_id"]
    body = client.post(f"/contracts/{cid}/cancel").json()
    assert body["down_payment_refund"] == pytest.approx(100.0, **APPROX)


# --------------------------------------------------------------------------- #
# Return (post-delivery)
# --------------------------------------------------------------------------- #
def test_return_after_delivery_computes_adjustment_and_closes(client):
    ctx = active_contract(client, national_id="CL-8")
    cid = ctx["contract_id"]
    payoff = _quote(client, cid)["final_payoff_amount"]

    r = client.post(f"/contracts/{cid}/return")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "closed"
    assert body["closure"]["reason"] == "return"
    # default down_payment_refund_pct_return = 0.0
    assert body["down_payment_refund"] == pytest.approx(0.0, **APPROX)
    assert body["settlement_shape_payoff"] == pytest.approx(payoff, **APPROX)
    # net adjustment = refund - payoff  (negative => customer still owes)
    assert body["net_adjustment"] == pytest.approx(0.0 - payoff, **APPROX)
    assert body["closure"]["financial_adjustment"] == pytest.approx(-payoff, **APPROX)
    # the assumption in effect is visible
    assert body["ownership_transfers_on_delivery"] is True

    rec = _receivable(client, cid)
    assert rec["outstanding_receivable"] == pytest.approx(0.0, **APPROX)


def test_return_before_delivery_returns_409_pointing_at_cancel(client):
    ctx = created_contract(client, national_id="CL-9")
    cid = ctx["contract_id"]
    r = client.post(f"/contracts/{cid}/return")
    assert r.status_code == 409
    assert "cancel" in r.json()["detail"].lower()


def test_ownership_assumption_echoed_from_config(client, set_config):
    set_config(cfg.KEY_OWNERSHIP_TRANSFERS_ON_DELIVERY, False)
    ctx = active_contract(client, national_id="CL-10")
    body = client.post(f"/contracts/{ctx['contract_id']}/return").json()
    assert body["ownership_transfers_on_delivery"] is False


# --------------------------------------------------------------------------- #
# Already-closed guard
# --------------------------------------------------------------------------- #
def test_closed_contract_cannot_be_closed_again(client):
    ctx = active_contract(client, national_id="CL-11")
    cid = ctx["contract_id"]
    q = _quote(client, cid)
    settled = client.post(f"/contracts/{cid}/settle", json={
        "amount": q["final_payoff_amount"], "external_reference": "CL11-S"})
    assert settled.status_code == 200

    assert client.post(f"/contracts/{cid}/settle", json={
        "amount": q["final_payoff_amount"], "external_reference": "CL11-S2"}).status_code == 409
    assert client.post(f"/contracts/{cid}/cancel").status_code == 409
    assert client.post(f"/contracts/{cid}/return").status_code == 409
    assert client.get(f"/contracts/{cid}/settlement-quote").status_code == 409


def test_exactly_one_closure_per_contract(client, db):
    from app.models.closure import ContractClosure

    ctx = active_contract(client, national_id="CL-12")
    cid = ctx["contract_id"]
    q = _quote(client, cid)
    client.post(f"/contracts/{cid}/settle", json={
        "amount": q["final_payoff_amount"], "external_reference": "CL12-S"})

    closures = db.query(ContractClosure).filter_by(contract_id=cid).all()
    assert len(closures) == 1
    assert closures[0].reason.value == "early_settlement"
