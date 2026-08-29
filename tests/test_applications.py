from tests.helpers import make_application, make_customer, make_product


def test_application_lifecycle_and_get(client):
    customer = make_customer(client, national_id="LC-1")
    product = make_product(client)

    app = make_application(client, customer["id"], product["id"], channel="online")
    assert app["status"] == "draft"
    assert app["channel"] == "online"

    submitted = client.post(f"/applications/{app['id']}/submit").json()
    assert submitted["status"] in {"approved", "rejected", "referred"}

    fetched = client.get(f"/applications/{app['id']}").json()
    assert fetched["status"] == submitted["status"]
    assert fetched["latest_assessment"] is not None
    assert len(fetched["assessments"]) == 1


def test_channel_is_required(client):
    customer = make_customer(client, national_id="LC-2")
    product = make_product(client)
    resp = client.post("/applications", json={
        "customer_id": customer["id"],
        "product_id": product["id"],
        "requested_amount": 1000,
        "requested_tenor_months": 12,
    })
    assert resp.status_code == 422


def test_cannot_submit_twice(client):
    customer = make_customer(client, national_id="LC-3")
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"])
    client.post(f"/applications/{app['id']}/submit")
    again = client.post(f"/applications/{app['id']}/submit")
    assert again.status_code == 409


def test_application_rejects_ineligible_product(client):
    customer = make_customer(client, national_id="LC-4")
    product = make_product(client, installment_eligible=False)
    resp = client.post("/applications", json={
        "customer_id": customer["id"],
        "product_id": product["id"],
        "requested_amount": 1000,
        "requested_tenor_months": 12,
        "channel": "branch",
    })
    assert resp.status_code == 422


def test_unknown_customer_or_product(client):
    resp = client.post("/applications", json={
        "customer_id": 999, "product_id": 999,
        "requested_amount": 1000, "requested_tenor_months": 12, "channel": "branch",
    })
    assert resp.status_code == 404
