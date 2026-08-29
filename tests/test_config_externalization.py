"""Proves the assessment thresholds are externalised, not hardcoded.

Same application inputs, different config value -> different decision.
"""
from app.services import config_service as cfg
from tests.helpers import make_application, make_customer, make_product


def test_changing_min_income_flips_outcome(client, set_config):
    customer = make_customer(client, national_id="EXT-1", monthly_income=5000,
                             existing_obligations=200, risk_score=700)
    product = make_product(client)

    # 1) default config -> approved
    app1 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    body1 = client.post(f"/applications/{app1['id']}/submit").json()
    assert body1["status"] == "approved"

    # 2) raise the minimum income above this applicant's income
    set_config(cfg.KEY_MIN_INCOME, 6000)

    app2 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    body2 = client.post(f"/applications/{app2['id']}/submit").json()
    assert body2["status"] == "rejected"

    # the decision-time snapshot records the value that was actually used
    assert body2["latest_assessment"]["config_snapshot"][cfg.KEY_MIN_INCOME] == 6000.0


def test_changing_max_dbr_flips_outcome(client, set_config):
    customer = make_customer(client, national_id="EXT-2", monthly_income=5000,
                             existing_obligations=200, risk_score=700)
    product = make_product(client)

    app1 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    assert client.post(f"/applications/{app1['id']}/submit").json()["status"] == "approved"

    # DBR here is (200 + 100) / 5000 = 0.06; tighten the ceiling below that
    set_config(cfg.KEY_MAX_DBR, 0.01)

    app2 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    assert client.post(f"/applications/{app2['id']}/submit").json()["status"] == "referred"


def test_config_parameters_exposed_via_api(client):
    resp = client.get("/config/parameters")
    assert resp.status_code == 200
    keys = {p["key"] for p in resp.json()}
    assert cfg.KEY_MIN_INCOME in keys
    assert cfg.KEY_MAX_DBR in keys


def test_config_update_via_api_changes_outcome(client):
    customer = make_customer(client, national_id="EXT-3", monthly_income=5000,
                             existing_obligations=200, risk_score=700)
    product = make_product(client)

    app1 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    assert client.post(f"/applications/{app1['id']}/submit").json()["status"] == "approved"

    upd = client.put(f"/config/parameters/{cfg.KEY_MIN_INCOME}",
                     json={"value": 9000, "value_type": "float"})
    assert upd.status_code == 200

    app2 = make_application(client, customer["id"], product["id"],
                            requested_amount=1200, requested_tenor_months=12)
    assert client.post(f"/applications/{app2['id']}/submit").json()["status"] == "rejected"
