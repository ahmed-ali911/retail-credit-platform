from app.services import config_service as cfg
from tests.helpers import (
    approved_application,
    make_application,
    make_customer,
    make_product,
)


def test_full_flow_offer_to_active_contract(client):
    ctx = approved_application(client, national_id="FLOW-1", cash_price=1200,
                               tenor_months=12)
    app_id = ctx["application"]["id"]

    # generate offer
    offer_resp = client.post(f"/applications/{app_id}/offer",
                             json={"down_payment_amount": 300})
    assert offer_resp.status_code == 201, offer_resp.text
    offer = offer_resp.json()
    assert offer["status"] == "presented"
    assert offer["tenor_months"] == 12
    # profit rate for 12 months (default fictitious table) = 0.09
    # financed principal = 1200 - 300 = 900 -> total_profit = 81.00
    assert offer["total_profit"] == 81.0
    assert offer["installment_sale_price"] == 1281.0
    assert len(offer["schedule_preview"]) == 12

    # GET returns the preview
    got = client.get(f"/offers/{offer['id']}").json()
    assert len(got["schedule_preview"]) == 12
    assert got["schedule_preview"][0]["profit_component"] >= \
        got["schedule_preview"][-1]["profit_component"]

    # accept with down payment confirmed
    acc = client.post(f"/offers/{offer['id']}/accept", json={
        "down_payment_confirmed": True,
        "down_payment_reference": "DP-REF-123",
        "down_payment_amount": 300,
    })
    assert acc.status_code == 200, acc.text
    body = acc.json()
    contract_id = body["contract_id"]
    assert body["sales_order_id"] > 0
    assert body["contract"]["status"] == "created"
    assert body["contract"]["unearned_profit_balance"] == 81.0
    assert len(body["contract"]["installments"]) == 12
    assert body["contract"]["installments"][0]["sequence_number"] == 1

    # offer is now accepted
    assert client.get(f"/offers/{offer['id']}").json()["status"] == "accepted"

    # confirm delivery -> active
    deliv = client.post(f"/contracts/{contract_id}/confirm-delivery")
    assert deliv.status_code == 200
    assert deliv.json()["status"] == "active"
    assert deliv.json()["activated_at"] is not None


def test_offer_requires_approved_application(client):
    # income below minimum -> rejected
    customer = make_customer(client, national_id="FLOW-REJ", monthly_income=100,
                             risk_score=700)
    product = make_product(client, cash_price=1200)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)
    submitted = client.post(f"/applications/{app['id']}/submit").json()
    assert submitted["status"] == "rejected"

    resp = client.post(f"/applications/{app['id']}/offer",
                       json={"down_payment_amount": 300})
    assert resp.status_code == 409


def test_down_payment_below_minimum_rejected(client):
    ctx = approved_application(client, national_id="FLOW-DP", cash_price=1200)
    app_id = ctx["application"]["id"]
    # minimum is 15% of 1200 = 180
    resp = client.post(f"/applications/{app_id}/offer",
                       json={"down_payment_amount": 100})
    assert resp.status_code == 422


def test_accept_without_confirmation_creates_nothing(client):
    ctx = approved_application(client, national_id="FLOW-NC", cash_price=1200)
    app_id = ctx["application"]["id"]
    offer = client.post(f"/applications/{app_id}/offer",
                        json={"down_payment_amount": 300}).json()

    resp = client.post(f"/offers/{offer['id']}/accept",
                       json={"down_payment_confirmed": False})
    assert resp.status_code == 422
    # offer untouched, no contract
    assert client.get(f"/offers/{offer['id']}").json()["status"] == "presented"


def test_unsupported_tenor_rejected(client):
    ctx = approved_application(client, national_id="FLOW-TEN", cash_price=1200,
                              tenor_months=12)
    app_id = ctx["application"]["id"]
    resp = client.post(f"/applications/{app_id}/offer",
                       json={"down_payment_amount": 300, "tenor_months": 9})
    assert resp.status_code == 422


def test_rate_table_config_change_changes_total_profit(client, set_config):
    ctx = approved_application(client, national_id="FLOW-CFG", cash_price=1200,
                              tenor_months=12)
    app_id = ctx["application"]["id"]

    first = client.post(f"/applications/{app_id}/offer",
                        json={"down_payment_amount": 300}).json()
    assert first["total_profit"] == 81.0  # 900 * 0.09

    # bump the 12-month rate in the tenor -> rate table
    set_config(cfg.KEY_TENOR_PROFIT_RATE_TABLE,
               {"6": 0.04, "12": 0.20, "18": 0.135, "24": 0.18, "36": 0.30})

    second = client.post(f"/applications/{app_id}/offer",
                         json={"down_payment_amount": 300}).json()
    assert second["total_profit"] == 180.0  # 900 * 0.20
    assert second["id"] != first["id"]
    # the superseded offer was expired
    assert client.get(f"/offers/{first['id']}").json()["status"] == "expired"
