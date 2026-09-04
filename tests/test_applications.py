from tests.helpers import (
    approved_application,
    make_application,
    make_customer,
    make_product,
)


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


# --------------------------------------------------------------------------- #
# Step 9 — minimal review-queue listing (GET /applications?status=referred)
# --------------------------------------------------------------------------- #
def _referred(client, national_id):
    customer = make_customer(client, national_id=national_id, risk_score=620,
                             monthly_income=5000, existing_obligations=100)
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)
    submitted = client.post(f"/applications/{app['id']}/submit").json()
    assert submitted["status"] == "referred", submitted
    return submitted


def test_list_referred_applications_for_the_review_queue(client):
    a = _referred(client, "LS-REF-1")
    b = _referred(client, "LS-REF-2")
    # an approved one that must NOT show up in the referred filter
    approved_application(client, national_id="LS-OK")

    resp = client.get("/applications?status=referred")
    assert resp.status_code == 200
    rows = resp.json()
    ids = {r["id"] for r in rows}
    assert ids == {a["id"], b["id"]}
    row = rows[0]
    assert set(row) == {
        "id", "customer_id", "product_id", "requested_amount",
        "status", "submitted_at", "reference_code",  # reference_code: Step 14
    }
    assert row["status"] == "referred"
    assert row["submitted_at"] is not None
    assert row["reference_code"] == f"AP-{row['id']:06d}"


def test_review_queue_list_is_role_gated(client_as):
    assert client_as("sales_employee").get(
        "/applications?status=referred"
    ).status_code == 403
    assert client_as("credit_officer").get(
        "/applications?status=referred"
    ).status_code == 200
