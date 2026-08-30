from datetime import date, timedelta

import pytest

from app.services import config_service as cfg
from tests.helpers import active_contract

APPROX = dict(abs=0.005)


def _assess(client, as_of):
    r = client.post("/jobs/assess-overdue", json={"as_of": as_of})
    assert r.status_code == 200, r.text
    return r.json()


def _contract(client, cid):
    return client.get(f"/contracts/{cid}").json()


def test_installment_inside_grace_period_gets_no_fee_but_is_marked_overdue(client):
    ctx = active_contract(client, national_id="OD-1")
    cid = ctx["contract_id"]
    # installment 1 due 2026-09-29; 6 days later, grace is 10
    result = _assess(client, "2026-10-05")

    assert result["late_fees_assessed"] == 0
    assert result["installments_marked_overdue"] == 1
    insts = {i["sequence_number"]: i for i in _contract(client, cid)["installments"]}
    assert insts[1]["status"] == "overdue"
    assert insts[1]["late_fee_outstanding"] == 0.0


def test_installment_past_grace_gets_exactly_two_percent_of_its_total(client):
    ctx = active_contract(client, national_id="OD-2")
    cid = ctx["contract_id"]
    first_total = ctx["schedule"][0]["total"]

    result = _assess(client, "2026-10-15")  # dpd 16 > grace 10
    assert result["late_fees_assessed"] == 1
    assert result["total_late_fee_amount"] == pytest.approx(first_total * 0.02, **APPROX)

    contract = _contract(client, cid)
    charges = contract["late_fee_charges"]
    assert len(charges) == 1
    assert charges[0]["amount"] == pytest.approx(first_total * 0.02, **APPROX)
    assert charges[0]["status"] == "assessed"

    rec = client.get(f"/contracts/{cid}/receivable").json()
    # late fee tracked separately — NOT folded into the receivable
    assert rec["outstanding_late_fees"] == pytest.approx(first_total * 0.02, **APPROX)
    assert rec["outstanding_receivable"] == pytest.approx(981.0, **APPROX)


def test_grace_period_boundary_is_strictly_greater_than(client):
    ctx = active_contract(client, national_id="OD-3")
    # derive the run dates from the actual first due date (clock-independent)
    due = date.fromisoformat(
        _contract(client, ctx["contract_id"])["installments"][0]["due_date"]
    )
    at_grace = (due + timedelta(days=10)).isoformat()    # dpd == grace(10) -> no fee
    past_grace = (due + timedelta(days=11)).isoformat()  # dpd 11 -> fee
    assert _assess(client, at_grace)["late_fees_assessed"] == 0
    assert _assess(client, past_grace)["late_fees_assessed"] == 1


def test_running_assess_twice_does_not_double_charge(client):
    ctx = active_contract(client, national_id="OD-4")
    cid = ctx["contract_id"]

    first = _assess(client, "2026-10-15")
    second = _assess(client, "2026-10-15")

    assert first["late_fees_assessed"] == 1
    assert second["late_fees_assessed"] == 0
    assert len(_contract(client, cid)["late_fee_charges"]) == 1


def test_grace_period_config_change_changes_whether_fee_triggers(client, set_config):
    ctx = active_contract(client, national_id="OD-5")
    cid = ctx["contract_id"]

    # widen the grace period so dpd 16 no longer triggers
    set_config(cfg.KEY_LATE_FEE_GRACE_DAYS, 30)
    assert _assess(client, "2026-10-15")["late_fees_assessed"] == 0

    # tighten it so the same run now triggers
    set_config(cfg.KEY_LATE_FEE_GRACE_DAYS, 5)
    result = _assess(client, "2026-10-15")
    assert result["late_fees_assessed"] == 1
    assert result["grace_period_days"] == 5


def test_late_fee_is_paid_before_profit_and_principal(client):
    ctx = active_contract(client, national_id="OD-6")
    cid = ctx["contract_id"]
    fee = round(ctx["schedule"][0]["total"] * 0.02, 2)

    _assess(client, "2026-10-15")

    before = client.get(f"/contracts/{cid}/receivable").json()
    pay = client.post(f"/contracts/{cid}/payments",
                      json={"amount": fee, "external_reference": "LF-PAY"}).json()

    alloc = pay["payment"]["allocations"][0]
    assert alloc["late_fee_amount"] == pytest.approx(fee, **APPROX)
    assert alloc["profit_amount"] == 0.0
    assert alloc["principal_amount"] == 0.0

    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after["outstanding_late_fees"] == pytest.approx(0.0, **APPROX)
    # principal + profit balance untouched by the late-fee payment
    assert after["outstanding_receivable"] == pytest.approx(
        before["outstanding_receivable"], **APPROX
    )
    charges = client.get(f"/contracts/{cid}").json()["late_fee_charges"]
    assert charges[0]["status"] == "paid"


def test_overdue_then_partial_payment_stays_overdue(client):
    ctx = active_contract(client, national_id="OD-7")
    cid = ctx["contract_id"]
    _assess(client, "2026-10-15")

    # small partial payment (less than installment 1's profit)
    client.post(f"/contracts/{cid}/payments",
                json={"amount": 3.00, "external_reference": "PARTIAL"})

    insts = {i["sequence_number"]: i for i in _contract(client, cid)["installments"]}
    assert insts[1]["status"] == "overdue"
