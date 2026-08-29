import pytest

from tests.helpers import active_contract

APPROX = dict(abs=0.005)


def _receivable(client, cid):
    r = client.get(f"/contracts/{cid}/receivable")
    assert r.status_code == 200, r.text
    return r.json()


def _installments(client, cid):
    c = client.get(f"/contracts/{cid}").json()
    return {i["sequence_number"]: i for i in c["installments"]}, c


def test_receivable_starts_at_full_financed_amount(client):
    ctx = active_contract(client, national_id="PF-1")
    rec = _receivable(client, ctx["contract_id"])
    assert rec["outstanding_principal"] == pytest.approx(900.0, **APPROX)
    assert rec["outstanding_profit"] == pytest.approx(81.0, **APPROX)
    assert rec["outstanding_receivable"] == pytest.approx(981.0, **APPROX)
    assert rec["outstanding_late_fees"] == 0.0
    assert rec["total_installments_paid"] == 0
    assert rec["total_installments_remaining"] == 12


def test_full_payment_of_one_installment(client):
    ctx = active_contract(client, national_id="PF-2")
    cid = ctx["contract_id"]
    sched = ctx["schedule"]
    first_total = sched[0]["total"]

    before = _receivable(client, cid)
    res = client.post(f"/contracts/{cid}/payments",
                      json={"amount": first_total, "external_reference": "PAY-1"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["replayed"] is False
    assert body["payment"]["status"] == "applied"
    alloc = body["payment"]["allocations"]
    assert len(alloc) == 1
    assert alloc[0]["principal_amount"] == pytest.approx(sched[0]["principal_component"], **APPROX)
    assert alloc[0]["profit_amount"] == pytest.approx(sched[0]["profit_component"], **APPROX)

    insts, contract = _installments(client, cid)
    assert insts[1]["status"] == "paid"
    # unearned profit fell by exactly the profit component paid
    assert contract["unearned_profit_balance"] == pytest.approx(
        81.0 - sched[0]["profit_component"], **APPROX
    )

    after = _receivable(client, cid)
    drop = before["outstanding_receivable"] - after["outstanding_receivable"]
    assert drop == pytest.approx(first_total, **APPROX)
    assert after["total_installments_paid"] == 1
    assert after["total_installments_remaining"] == 11


def test_idempotent_replay_does_not_double_allocate(client):
    ctx = active_contract(client, national_id="PF-3")
    cid = ctx["contract_id"]
    amount = ctx["schedule"][0]["total"]

    first = client.post(f"/contracts/{cid}/payments",
                        json={"amount": amount, "external_reference": "DUP"}).json()
    after_first = _receivable(client, cid)

    replay = client.post(f"/contracts/{cid}/payments",
                         json={"amount": amount, "external_reference": "DUP"}).json()
    after_replay = _receivable(client, cid)

    assert replay["replayed"] is True
    assert replay["payment"]["id"] == first["payment"]["id"]
    assert after_replay["outstanding_receivable"] == pytest.approx(
        after_first["outstanding_receivable"], **APPROX
    )


def test_payment_spanning_two_installments_settles_oldest_in_full_first(client):
    ctx = active_contract(client, national_id="PF-4")
    cid = ctx["contract_id"]
    s = ctx["schedule"]

    # installment 2 in full + installment 3's profit + 5.00 of installment 3 principal
    amount = round(s[1]["total"] + s[2]["profit_component"] + 5.00, 2)

    # first clear installment 1 so 2 is the oldest open one
    client.post(f"/contracts/{cid}/payments",
                json={"amount": s[0]["total"], "external_reference": "SP-0"})

    client.post(f"/contracts/{cid}/payments",
                json={"amount": amount, "external_reference": "SP-1"})

    insts, _ = _installments(client, cid)
    # oldest open installment (2) fully paid — principal included
    assert insts[2]["status"] == "paid"
    assert insts[2]["principal_outstanding"] == pytest.approx(0.0, **APPROX)
    # newer installment (3): profit fully paid, only 5.00 of principal paid
    assert insts[3]["profit_outstanding"] == pytest.approx(0.0, **APPROX)
    assert insts[3]["principal_paid"] == pytest.approx(5.0, **APPROX)
    assert insts[3]["status"] == "partially_paid"


def test_overpayment_marks_payment_overpaid(client):
    ctx = active_contract(client, national_id="PF-5")
    cid = ctx["contract_id"]
    res = client.post(f"/contracts/{cid}/payments",
                      json={"amount": 100000, "external_reference": "BIG"}).json()
    assert res["payment"]["status"] == "overpaid"
    assert res["payment"]["unallocated_amount"] == pytest.approx(100000 - 981.0, **APPROX)
    rec = _receivable(client, cid)
    assert rec["outstanding_receivable"] == pytest.approx(0.0, **APPROX)
    assert rec["total_installments_remaining"] == 0


def test_payment_requires_active_contract(client):
    from tests.helpers import approved_application

    ctx = approved_application(client, national_id="PF-6")
    app_id = ctx["application"]["id"]
    offer = client.post(f"/applications/{app_id}/offer",
                        json={"down_payment_amount": 300}).json()
    acc = client.post(f"/offers/{offer['id']}/accept", json={
        "down_payment_confirmed": True, "down_payment_reference": "DP-6"}).json()
    cid = acc["contract_id"]  # status 'created', not delivered

    res = client.post(f"/contracts/{cid}/payments",
                      json={"amount": 50, "external_reference": "EARLY"})
    assert res.status_code == 409
