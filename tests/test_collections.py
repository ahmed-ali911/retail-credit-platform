import pytest

from app.models.customer import Customer
from tests.helpers import active_contract


def _cases(client, contract_id, status=None):
    params = {"contract_id": contract_id}
    if status:
        params["status"] = status
    r = client.get("/collections/cases", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _make_overdue(client, national_id, as_of="2026-10-05"):
    ctx = active_contract(client, national_id=national_id)
    r = client.post("/jobs/assess-overdue", json={"as_of": as_of})
    assert r.status_code == 200, r.text
    return ctx, r.json()


def test_overdue_opens_exactly_one_case_even_when_run_repeatedly(client):
    ctx, first = _make_overdue(client, "COL-1")
    cid = ctx["contract_id"]
    assert first["collection_cases_opened"] == 1

    second = client.post("/jobs/assess-overdue", json={"as_of": "2026-10-06"}).json()
    third = client.post("/jobs/assess-overdue", json={"as_of": "2026-10-07"}).json()
    assert second["collection_cases_opened"] == 0
    assert third["collection_cases_opened"] == 0

    open_cases = _cases(client, cid, status="open")
    assert len(open_cases) == 1
    assert str(open_cases[0]["opened_reason"]).startswith("installment ")


def test_payment_clearing_overdue_closes_the_case(client):
    ctx, _ = _make_overdue(client, "COL-2")
    cid = ctx["contract_id"]
    assert _cases(client, cid, status="open")

    first_total = ctx["schedule"][0]["total"]
    client.post(f"/contracts/{cid}/payments",
                json={"amount": first_total, "external_reference": "COL2-PAY"})

    assert _cases(client, cid, status="open") == []
    closed = _cases(client, cid, status="closed")
    assert len(closed) == 1
    assert closed[0]["closed_at"] is not None


def test_promise_to_pay_stores_fields_other_types_leave_them_null(client):
    ctx, _ = _make_overdue(client, "COL-3")
    case_id = _cases(client, ctx["contract_id"], status="open")[0]["id"]

    call = client.post(f"/collections/cases/{case_id}/activities", json={
        "activity_type": "call", "notes": "left voicemail"})
    assert call.status_code == 201
    assert call.json()["promised_amount"] is None
    assert call.json()["promised_date"] is None
    assert call.json()["promise_status"] is None

    ptp = client.post(f"/collections/cases/{case_id}/activities", json={
        "activity_type": "promise_to_pay", "notes": "will pay Friday",
        "promised_amount": 90.0, "promised_date": "2026-10-20"})
    assert ptp.status_code == 201
    body = ptp.json()
    assert body["promised_amount"] == pytest.approx(90.0)
    assert body["promised_date"] == "2026-10-20"
    assert body["promise_status"] == "pending"

    detail = client.get(f"/collections/cases/{case_id}").json()
    assert len(detail["activities"]) == 2


def test_promise_to_pay_requires_amount_and_date(client):
    ctx, _ = _make_overdue(client, "COL-4")
    case_id = _cases(client, ctx["contract_id"], status="open")[0]["id"]
    r = client.post(f"/collections/cases/{case_id}/activities", json={
        "activity_type": "promise_to_pay", "notes": "vague"})
    assert r.status_code == 422


def test_rbac_only_collections_officer_or_admin_can_log_activity(client, client_as):
    ctx, _ = _make_overdue(client, "COL-5")
    case_id = _cases(client, ctx["contract_id"], status="open")[0]["id"]
    body = {"activity_type": "call", "notes": "x"}

    assert client_as("sales_employee").post(
        f"/collections/cases/{case_id}/activities", json=body).status_code == 403
    assert client_as("credit_officer").post(
        f"/collections/cases/{case_id}/activities", json=body).status_code == 403
    assert client_as("collections_officer").post(
        f"/collections/cases/{case_id}/activities", json=body).status_code == 201


def test_rbac_case_list_roles(client_as):
    assert client_as("sales_employee").get("/collections/cases").status_code == 403
    for role in ("collections_officer", "credit_manager", "admin"):
        assert client_as(role).get("/collections/cases").status_code == 200


def test_owning_customer_can_view_own_case_not_others(client, client_as, db, auth):
    ctx, _ = _make_overdue(client, "COL-6")
    other, _ = _make_overdue(client, "COL-7")
    case_id = _cases(client, ctx["contract_id"], status="open")[0]["id"]
    other_case_id = _cases(client, other["contract_id"], status="open")[0]["id"]

    db.get(Customer, ctx["customer"]["id"]).user_id = auth["users"]["customer"].id
    db.commit()

    cust = client_as("customer")
    assert cust.get(f"/collections/cases/{case_id}").status_code == 200
    assert cust.get(f"/collections/cases/{other_case_id}").status_code == 403
