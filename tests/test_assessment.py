"""Credit Assessment Engine behaviour under the default (fictitious) config."""
from tests.helpers import make_application, make_customer, make_product


def test_application_approved_under_default_config(client):
    customer = make_customer(client, national_id="APR-1", monthly_income=5000,
                             existing_obligations=200, risk_score=700)
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)

    result = client.post(f"/applications/{app['id']}/submit")
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "approved"
    assert body["latest_assessment"]["decision"] == "approved"
    # nothing failed -> no triggered rules
    assert body["latest_assessment"]["triggered_rules"] == []
    assert body["latest_assessment"]["estimated_installment"] == 100.0


def test_rejected_when_income_below_configured_minimum(client):
    customer = make_customer(client, national_id="REJ-1", monthly_income=250,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)

    body = client.post(f"/applications/{app['id']}/submit").json()
    assert body["status"] == "rejected"

    rules = {r["rule"]: r for r in body["latest_assessment"]["triggered_rules"]}
    assert rules["minimum_income"]["outcome"] == "rejected"
    assert "below minimum" in rules["minimum_income"]["reason"]


def test_referred_when_dbr_threshold_breached(client):
    # income 1000, installment = 6000 / 12 = 500, obligations 0 -> DBR 0.50 > 0.40
    customer = make_customer(client, national_id="REF-1", monthly_income=1000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client, cash_price=6000)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=6000, requested_tenor_months=12)

    body = client.post(f"/applications/{app['id']}/submit").json()
    assert body["status"] == "referred"

    rules = {r["rule"]: r for r in body["latest_assessment"]["triggered_rules"]}
    assert rules["debt_burden_ratio"]["outcome"] == "referred"
    assert body["latest_assessment"]["debt_burden_ratio"] == 0.5


def test_referred_when_risk_score_in_referral_band(client):
    customer = make_customer(client, national_id="REF-2", monthly_income=5000,
                             existing_obligations=100, risk_score=620)
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)

    body = client.post(f"/applications/{app['id']}/submit").json()
    assert body["status"] == "referred"
    rules = {r["rule"]: r for r in body["latest_assessment"]["triggered_rules"]}
    assert rules["risk_band"]["outcome"] == "referred"


def test_rejected_beats_referred_in_precedence(client):
    # income below min (reject) AND risk in referral band (refer) -> rejected wins
    customer = make_customer(client, national_id="PREC-1", monthly_income=100,
                             existing_obligations=0, risk_score=620)
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)

    body = client.post(f"/applications/{app['id']}/submit").json()
    assert body["status"] == "rejected"
