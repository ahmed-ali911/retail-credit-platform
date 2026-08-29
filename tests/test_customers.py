from tests.helpers import make_customer


def test_create_customer_with_profile(client):
    body = make_customer(client, national_id="ID-CUST-1")
    assert body["id"] > 0
    assert body["status"] == "Active"
    assert body["profile"]["monthly_income"] == 5000
    assert body["profile"]["customer_id"] == body["id"]


def test_duplicate_national_id_rejected(client):
    make_customer(client, national_id="DUP-1")
    resp = client.post("/customers", json={
        "name": "Other",
        "national_id": "DUP-1",
        "profile": {"monthly_income": 1000},
    })
    assert resp.status_code == 409


def test_customer_and_profile_are_separate_rows(client, db):
    from app.models.customer import Customer, CustomerProfile

    make_customer(client, national_id="SEP-1")
    assert db.query(Customer).count() == 1
    assert db.query(CustomerProfile).count() == 1
