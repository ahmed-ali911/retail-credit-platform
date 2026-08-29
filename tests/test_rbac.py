"""RBAC — grouped by role-requirement pattern, not one test per endpoint."""
import pytest

from app.models.customer import Customer
from tests.helpers import (
    active_contract,
    make_application,
    make_customer,
    make_product,
)


# --------------------------------------------------------------------------- #
# 401 — no / invalid token on a protected endpoint
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/customers"),
        ("post", "/applications"),
        ("post", "/jobs/assess-overdue"),
        ("get", "/config/parameters"),
        ("get", "/applications/1"),
        ("get", "/audit/events"),
    ],
)
def test_no_token_is_401(client_as, method, path):
    anon = client_as(None)
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(anon, method)(path, **kwargs)
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 403 — valid token, wrong role
# --------------------------------------------------------------------------- #
def test_wrong_role_is_403_for_staff_only_endpoints(client_as):
    # POST /customers -> sales_employee / admin
    assert client_as("customer").post("/customers", json={}).status_code == 403
    assert client_as("finance_officer").post("/customers", json={}).status_code == 403

    # POST /jobs/assess-overdue -> admin only
    assert client_as("sales_employee").post("/jobs/assess-overdue", json={}).status_code == 403
    assert client_as("credit_manager").post("/jobs/assess-overdue", json={}).status_code == 403

    # GET/PUT /config/parameters -> admin only
    assert client_as("credit_officer").get("/config/parameters").status_code == 403
    assert client_as("finance_officer").put(
        "/config/parameters/late_fee_rate", json={"value": 0.05}
    ).status_code == 403

    # GET /audit/events -> admin / credit_manager
    assert client_as("sales_employee").get("/audit/events").status_code == 403


def test_wrong_role_is_403_for_closure_endpoints(client_as):
    admin = client_as("admin")
    ctx = active_contract(admin, national_id="RBAC-CL")
    cid = ctx["contract_id"]
    # settle / cancel / return -> finance_officer / credit_manager / admin
    for role in ("sales_employee", "credit_officer", "customer"):
        c = client_as(role)
        assert c.post(
            f"/contracts/{cid}/settle", json={"amount": 1, "external_reference": "x"}
        ).status_code == 403
        assert c.post(f"/contracts/{cid}/cancel").status_code == 403
        assert c.post(f"/contracts/{cid}/return").status_code == 403


# --------------------------------------------------------------------------- #
# correct role -> succeeds (wiring didn't break existing behaviour)
# --------------------------------------------------------------------------- #
def test_correct_roles_succeed(client_as):
    sales = client_as("sales_employee")
    admin = client_as("admin")

    customer = make_customer(sales, national_id="RBAC-OK-1")
    product = make_product(sales)
    app = make_application(sales, customer["id"], product["id"])
    assert sales.post(f"/applications/{app['id']}/submit").json()["status"] in {
        "approved", "rejected", "referred"
    }

    # admin-only job
    assert admin.post("/jobs/assess-overdue", json={"as_of": "2026-12-31"}).status_code == 200

    # admin config read
    assert admin.get("/config/parameters").status_code == 200


def test_finance_officer_can_settle(client_as):
    admin = client_as("admin")
    ctx = active_contract(admin, national_id="RBAC-FIN")
    cid = ctx["contract_id"]

    finance = client_as("finance_officer")
    q = finance.get(f"/contracts/{cid}/settlement-quote")
    assert q.status_code == 200
    r = finance.post(f"/contracts/{cid}/settle", json={
        "amount": q.json()["final_payoff_amount"], "external_reference": "FIN-1"})
    assert r.status_code == 200
    assert r.json()["status"] == "closed"


# --------------------------------------------------------------------------- #
# customer ownership: own vs someone else's  (someone else's -> 403)
# --------------------------------------------------------------------------- #
def test_customer_can_access_own_application_not_others(client, client_as, db, auth):
    c1 = make_customer(client, national_id="OWN-1")
    c2 = make_customer(client, national_id="OWN-2")
    product = make_product(client)
    app1 = make_application(client, c1["id"], product["id"])
    app2 = make_application(client, c2["id"], product["id"])

    db.get(Customer, c1["id"]).user_id = auth["users"]["customer"].id
    db.commit()

    cust = client_as("customer")
    assert cust.get(f"/applications/{app1['id']}").status_code == 200
    assert cust.get(f"/applications/{app2['id']}").status_code == 403


def test_customer_can_view_own_contract_receivable_not_others(client, client_as, db, auth):
    ctx = active_contract(client, national_id="OWN-CT")
    cid = ctx["contract_id"]
    other = active_contract(client, national_id="OWN-CT2")

    db.get(Customer, ctx["customer"]["id"]).user_id = auth["users"]["customer"].id
    db.commit()

    cust = client_as("customer")
    assert cust.get(f"/contracts/{cid}/receivable").status_code == 200
    assert cust.get(f"/contracts/{other['contract_id']}/receivable").status_code == 403


def test_config_parameters_rejects_non_admin(client_as):
    for role in ("credit_officer", "credit_manager", "sales_employee", "finance_officer", "customer"):
        assert client_as(role).get("/config/parameters").status_code == 403
    assert client_as("admin").get("/config/parameters").status_code == 200
