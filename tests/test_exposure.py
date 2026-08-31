"""P0-4 — customer exposure aggregation (fixes assessment finding S-3)."""
from decimal import Decimal

import pytest

from app.models.customer import Customer
from app.services import config_service as cfg
from app.services.exposure import compute_exposure
from tests.helpers import make_application, make_customer, make_product

D = Decimal


def _make_contract(client, customer_id, product_id, ref):
    """A fresh delivered contract for the given customer (cash 1200, dp 300)."""
    app = make_application(client, customer_id, product_id,
                           requested_amount=1200, requested_tenor_months=12)
    sub = client.post(f"/applications/{app['id']}/submit").json()
    assert sub["status"] == "approved", sub
    offer = client.post(f"/applications/{app['id']}/offer",
                        json={"down_payment_amount": 300}).json()
    acc = client.post(f"/offers/{offer['id']}/accept",
                      json={"down_payment_confirmed": True,
                            "down_payment_reference": ref}).json()
    cid = acc["contract_id"]
    assert client.post(f"/contracts/{cid}/confirm-delivery").status_code == 200
    return cid


def _receivable_total(client, contract_id):
    r = client.get(f"/contracts/{contract_id}/receivable").json()
    return (D(str(r["outstanding_principal"]))
            + D(str(r["outstanding_profit"]))
            + D(str(r["outstanding_late_fees"])))


# --------------------------------------------------------------------------- #
# 1. Pure compute_exposure — zero / one / multiple contracts
# --------------------------------------------------------------------------- #
def test_compute_exposure_zero_one_and_multiple_contracts(client, db):
    customer = make_customer(client, national_id="EX-CALC", monthly_income=9000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)

    zero = compute_exposure(db, customer["id"])
    assert zero.total_outstanding == D("0")
    assert zero.contracts == []
    assert zero.aggregation_level == "company_wide"

    c1 = _make_contract(client, customer["id"], product["id"], "EX-CALC-1")
    one = compute_exposure(db, customer["id"])
    assert len(one.contracts) == 1
    assert one.contracts[0].contract_id == c1
    assert one.total_outstanding == _receivable_total(client, c1)

    c2 = _make_contract(client, customer["id"], product["id"], "EX-CALC-2")
    multi = compute_exposure(db, customer["id"])
    assert len(multi.contracts) == 2
    assert multi.total_outstanding == (
        _receivable_total(client, c1) + _receivable_total(client, c2)
    )


# --------------------------------------------------------------------------- #
# 2. The new assessment rule
# --------------------------------------------------------------------------- #
def test_customer_with_no_other_contracts_is_unaffected(client):
    customer = make_customer(client, national_id="EX-NONE", monthly_income=5000,
                             existing_obligations=100, risk_score=700)
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)
    body = client.post(f"/applications/{app['id']}/submit").json()
    assert body["status"] == "approved"
    assert body["latest_assessment"]["triggered_rules"] == []  # exposure passed


def test_exposure_breach_routes_to_manual_review(client, set_config):
    set_config(cfg.KEY_MAX_CUSTOMER_EXPOSURE, 2000)
    customer = make_customer(client, national_id="EX-BREACH", monthly_income=9000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)
    _make_contract(client, customer["id"], product["id"], "EX-B1")  # ~981 outstanding

    # current 981 + new financed 1200*0.85 = 1020  ->  2001 > 2000  -> referred
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)
    body = client.post(f"/applications/{app['id']}/submit").json()
    assert body["status"] == "referred"

    rules = {r["rule"]: r for r in body["latest_assessment"]["triggered_rules"]}
    assert rules["customer_exposure"]["outcome"] == "referred"
    assert "exceeds maximum" in rules["customer_exposure"]["reason"]
    snap = body["latest_assessment"]["config_snapshot"]
    assert snap[cfg.KEY_MAX_CUSTOMER_EXPOSURE] == 2000.0
    assert snap[cfg.KEY_EXPOSURE_AGGREGATION_LEVEL] == "company_wide"


def test_closed_contracts_are_excluded_from_exposure(client, db, set_config):
    set_config(cfg.KEY_MAX_CUSTOMER_EXPOSURE, 2000)
    customer = make_customer(client, national_id="EX-CLOSED", monthly_income=9000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)
    c1 = _make_contract(client, customer["id"], product["id"], "EX-CL1")

    q = client.get(f"/contracts/{c1}/settlement-quote").json()
    client.post(f"/contracts/{c1}/settle",
                json={"amount": q["final_payoff_amount"], "external_reference": "EX-SET"})

    assert compute_exposure(db, customer["id"]).total_outstanding == D("0")

    # limit is still 2000 but the closed contract no longer counts -> approved
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)
    assert client.post(f"/applications/{app['id']}/submit").json()["status"] == "approved"


def test_config_change_to_max_exposure_flips_an_otherwise_identical_application(
    client, set_config
):
    customer = make_customer(client, national_id="EX-FLIP", monthly_income=9000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)
    _make_contract(client, customer["id"], product["id"], "EX-FL1")

    set_config(cfg.KEY_MAX_CUSTOMER_EXPOSURE, 2000)
    app1 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    assert client.post(f"/applications/{app1['id']}/submit").json()["status"] == "referred"

    set_config(cfg.KEY_MAX_CUSTOMER_EXPOSURE, 50000)
    app2 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    assert client.post(f"/applications/{app2['id']}/submit").json()["status"] == "approved"


# --------------------------------------------------------------------------- #
# 3. The visibility endpoint
# --------------------------------------------------------------------------- #
def test_exposure_endpoint_matches_a_manual_sum_across_two_contracts(client):
    customer = make_customer(client, national_id="EX-EP", monthly_income=9000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)
    c1 = _make_contract(client, customer["id"], product["id"], "EX-EP1")
    c2 = _make_contract(client, customer["id"], product["id"], "EX-EP2")

    r = client.get(f"/customers/{customer['id']}/exposure")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["aggregation_level"] == "company_wide"
    assert {c["contract_id"] for c in body["contracts"]} == {c1, c2}

    manual = 0.0
    for cid in (c1, c2):
        rec = client.get(f"/contracts/{cid}/receivable").json()
        manual += (rec["outstanding_principal"] + rec["outstanding_profit"]
                   + rec["outstanding_late_fees"])
    assert body["total_outstanding"] == pytest.approx(manual, abs=0.005)


def test_exposure_endpoint_rbac(client, client_as):
    customer = make_customer(client, national_id="EX-RBAC", monthly_income=5000,
                             existing_obligations=0, risk_score=700)
    cid = customer["id"]
    for role in ("credit_officer", "credit_manager", "finance_officer", "admin"):
        assert client_as(role).get(f"/customers/{cid}/exposure").status_code == 200
    assert client_as("sales_employee").get(f"/customers/{cid}/exposure").status_code == 403
    assert client_as(None).get(f"/customers/{cid}/exposure").status_code == 401


def test_owning_customer_sees_own_exposure_not_others(client, client_as, db, auth):
    c1 = make_customer(client, national_id="EX-OWN1", monthly_income=5000,
                       existing_obligations=0, risk_score=700)
    c2 = make_customer(client, national_id="EX-OWN2", monthly_income=5000,
                       existing_obligations=0, risk_score=700)
    db.get(Customer, c1["id"]).user_id = auth["users"]["customer"].id
    db.commit()

    cust = client_as("customer")
    assert cust.get(f"/customers/{c1['id']}/exposure").status_code == 200
    assert cust.get(f"/customers/{c2['id']}/exposure").status_code == 403
