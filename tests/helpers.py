"""Small builders so each test states only what it cares about."""
from __future__ import annotations


def make_customer(client, *, national_id="ID-1", monthly_income=5000,
                  existing_obligations=200, risk_score=700, **overrides):
    payload = {
        "name": "Test Customer",
        "national_id": national_id,
        "phone": "+96500000000",
        "email": "test@example.com",
        "risk_score": risk_score,
        "profile": {
            "employer_name": "ACME",
            "employment_type": "full_time",
            "monthly_income": monthly_income,
            "existing_monthly_obligations": existing_obligations,
            "address_line": "1 Main St",
            "city": "Kuwait City",
            "contact_phone": "+96500000000",
        },
    }
    payload.update(overrides)
    resp = client.post("/customers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_product(client, *, name="Fridge", cash_price=1200, installment_eligible=True):
    resp = client.post("/products", json={
        "name": name,
        "category": "appliances",
        "cash_price": cash_price,
        "installment_eligible": installment_eligible,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_application(client, customer_id, product_id, *, requested_amount=1200,
                     requested_tenor_months=12, channel="branch"):
    resp = client.post("/applications", json={
        "customer_id": customer_id,
        "product_id": product_id,
        "requested_amount": requested_amount,
        "requested_tenor_months": requested_tenor_months,
        "channel": channel,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def approved_application(client, *, national_id="APP-OK", cash_price=1200,
                         tenor_months=12, risk_score=700, monthly_income=5000):
    """Customer + product + a submitted application that lands on 'approved'."""
    customer = make_customer(client, national_id=national_id, risk_score=risk_score,
                             monthly_income=monthly_income, existing_obligations=100)
    product = make_product(client, cash_price=cash_price)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=cash_price,
                           requested_tenor_months=tenor_months)
    submitted = client.post(f"/applications/{app['id']}/submit").json()
    assert submitted["status"] == "approved", submitted
    return {"customer": customer, "product": product, "application": submitted}
