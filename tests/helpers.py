"""Small builders so each test states only what it cares about.

DATE RULE — read before adding a test that simulates elapsed time
----------------------------------------------------------------
Never hardcode an absolute date (e.g. ``"2026-10-15"``) as an ``as_of`` for
``/jobs/assess-overdue``, as a simulated "later" run, or anywhere that stands
in for "some time after this test's contract was created". Contracts and their
installments are created relative to ``date.today()`` at test-run time, so a
fixed future literal drifts closer to the real schedule every day until it no
longer clears the grace period the test depends on — the test then fails for a
reason unrelated to any bug.

Instead compute the date from the record the test itself created:
``first_due_date(client, contract_id) + timedelta(days=N)`` (or backdate an
installment's ``due_date`` via the shared ``db`` session, as the reports tests
do). Pick ``N`` to preserve the test's intent — comfortably past / within the
10-day grace, past a reconciliation window, etc.
"""
from __future__ import annotations

from datetime import date, timedelta  # noqa: F401  (timedelta re-exported for tests)


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


def make_product(client, *, name="Fridge", cash_price=1200, installment_eligible=True,
                 stock_quantity=None):
    body = {
        "name": name,
        "category": "appliances",
        "cash_price": cash_price,
        "installment_eligible": installment_eligible,
    }
    if stock_quantity is not None:
        body["stock_quantity"] = stock_quantity
    resp = client.post("/products", json=body)
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


def make_contract(client, *, national_id="CT-1", cash_price=1200, tenor_months=12,
                  down_payment_amount=300, deliver=True):
    """Approved application -> offer -> accept -> (optionally) delivery.

    With deliver=False the contract is left in status 'created'.
    """
    ctx = approved_application(client, national_id=national_id, cash_price=cash_price,
                               tenor_months=tenor_months)
    app_id = ctx["application"]["id"]
    offer = client.post(f"/applications/{app_id}/offer",
                        json={"down_payment_amount": down_payment_amount}).json()
    acc = client.post(f"/offers/{offer['id']}/accept", json={
        "down_payment_confirmed": True,
        "down_payment_reference": f"DP-{national_id}",
    }).json()
    contract_id = acc["contract_id"]
    if deliver:
        deliv = client.post(f"/contracts/{contract_id}/confirm-delivery")
        assert deliv.status_code == 200, deliv.text
    return {
        **ctx,
        "offer": offer,
        "contract_id": contract_id,
        "down_payment_amount": down_payment_amount,
        "schedule": offer["schedule_preview"],
    }


def active_contract(client, **kw):
    return make_contract(client, deliver=True, **kw)


def created_contract(client, **kw):
    return make_contract(client, deliver=False, **kw)


def first_due_date(client, contract_id) -> date:
    """Due date of installment 1 for a contract — the anchor for any
    "simulated elapsed time" in overdue / collections / reconciliation tests.

    See the DATE RULE at the top of this module: compute ``as_of`` values as
    ``first_due_date(client, cid) + timedelta(days=N)`` rather than hardcoding
    an absolute date literal.
    """
    contract = client.get(f"/contracts/{contract_id}").json()
    return date.fromisoformat(contract["installments"][0]["due_date"])
