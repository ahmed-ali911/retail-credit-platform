from app.models.audit import AuditEvent
from tests.helpers import active_contract, make_application, make_customer, make_product


def _events(db, **filters):
    q = db.query(AuditEvent)
    for k, v in filters.items():
        q = q.filter(getattr(AuditEvent, k) == v)
    return q.all()


def test_application_submit_writes_audit_event(client, db):
    customer = make_customer(client, national_id="AUD-1")
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"])
    client.post(f"/applications/{app['id']}/submit")

    evs = _events(db, action="application.submitted", entity_id=str(app["id"]))
    assert len(evs) == 1
    assert evs[0].before_value == {"status": "draft"}
    assert evs[0].after_value["decision"] in {"approved", "rejected", "referred"}
    assert evs[0].user_id is not None


def test_contract_settlement_writes_audit_event(client, db):
    ctx = active_contract(client, national_id="AUD-2")
    cid = ctx["contract_id"]
    q = client.get(f"/contracts/{cid}/settlement-quote").json()
    client.post(f"/contracts/{cid}/settle", json={
        "amount": q["final_payoff_amount"], "external_reference": "AUD2"})

    evs = _events(db, action="contract.settled", entity_id=str(cid))
    assert len(evs) == 1
    assert evs[0].after_value["status"] == "closed"


def test_config_update_writes_audit_event_on_approval(client, client_as, db):
    # Step 6: the request creates a pending approval, no config.updated yet
    upd = client.put("/config/parameters/late_fee_grace_period_days", json={"value": 20})
    assert upd.status_code == 202
    assert _events(db, action="config.updated") == []
    assert len(_events(db, action="approval.requested")) == 1

    # approval by a different eligible user applies it and fires config.updated
    client_as("credit_manager").post(f"/approvals/{upd.json()['id']}/approve")
    evs = _events(db, action="config.updated", entity_id="late_fee_grace_period_days")
    assert len(evs) == 1
    assert evs[0].before_value == {"value": "10"}
    assert evs[0].after_value["value"] == "20"
    assert evs[0].after_value["approval_request_id"] == upd.json()["id"]


def test_overdue_job_writes_audit_events(client, db):
    ctx = active_contract(client, national_id="AUD-3")
    client.post("/jobs/assess-overdue", json={"as_of": "2026-12-20"})

    assert len(_events(db, action="overdue.assessed")) == 1
    assert len(_events(db, action="late_fee.assessed")) >= 1


def test_audit_events_endpoint_filters_and_is_restricted(client, client_as, db):
    ctx = active_contract(client, national_id="AUD-4")
    cid = ctx["contract_id"]
    q = client.get(f"/contracts/{cid}/settlement-quote").json()
    client.post(f"/contracts/{cid}/settle", json={
        "amount": q["final_payoff_amount"], "external_reference": "AUD4"})

    # admin can read, filtered
    r = client.get("/audit/events", params={
        "entity_type": "installment_contract", "entity_id": cid})
    assert r.status_code == 200
    actions = {e["action"] for e in r.json()}
    assert "contract.settled" in actions
    assert all(e["entity_id"] == str(cid) for e in r.json())

    # credit_manager can read
    assert client_as("credit_manager").get("/audit/events").status_code == 200
    # sales_employee cannot
    assert client_as("sales_employee").get("/audit/events").status_code == 403
