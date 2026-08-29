from app.services import config_service as cfg
from tests.helpers import active_contract


def _late_fee_id(client, contract_id):
    charges = client.get(f"/contracts/{contract_id}").json()["late_fee_charges"]
    assert charges, "expected a late fee charge"
    return charges[0]["id"]


def _charge_status(client, contract_id, charge_id):
    charges = client.get(f"/contracts/{contract_id}").json()["late_fee_charges"]
    return next(c["status"] for c in charges if c["id"] == charge_id)


def _assessed_contract(client, national_id):
    ctx = active_contract(client, national_id=national_id)
    client.post("/jobs/assess-overdue", json={"as_of": "2026-10-15"})  # past grace -> fee
    return ctx


# --------------------------------------------------------------------------- #
# maker == checker is forbidden
# --------------------------------------------------------------------------- #
def test_cannot_approve_your_own_waiver_request_even_as_admin(client):
    ctx = _assessed_contract(client, "APR-1")
    lf = _late_fee_id(client, ctx["contract_id"])
    req = client.post(f"/late-fees/{lf}/request-waiver", json={"reason": "goodwill"})
    assert req.status_code == 201
    approval_id = req.json()["id"]

    dup = client.post(f"/approvals/{approval_id}/approve")
    assert dup.status_code == 409
    assert _charge_status(client, ctx["contract_id"], lf) == "assessed"


def test_cannot_approve_your_own_config_request_even_as_admin(client):
    req = client.put(f"/config/parameters/{cfg.KEY_MIN_INCOME}", json={"value": 4321})
    assert req.status_code == 202
    dup = client.post(f"/approvals/{req.json()['id']}/approve")
    assert dup.status_code == 409


# --------------------------------------------------------------------------- #
# a different eligible user can decide, and it executes
# --------------------------------------------------------------------------- #
def test_different_user_approves_waiver_and_it_executes(client, client_as):
    ctx = _assessed_contract(client, "APR-2")
    cid = ctx["contract_id"]
    lf = _late_fee_id(client, cid)

    req = client_as("finance_officer").post(
        f"/late-fees/{lf}/request-waiver", json={"reason": "hardship"})
    assert req.status_code == 201

    ok = client_as("credit_manager").post(f"/approvals/{req.json()['id']}/approve")
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"
    assert _charge_status(client, cid, lf) == "waived"
    # waived fee drops out of the receivable's late-fee balance
    assert client.get(f"/contracts/{cid}/receivable").json()["outstanding_late_fees"] == 0.0


def test_rejecting_waiver_leaves_the_late_fee_unchanged(client, client_as):
    ctx = _assessed_contract(client, "APR-3")
    cid = ctx["contract_id"]
    lf = _late_fee_id(client, cid)

    req = client_as("finance_officer").post(
        f"/late-fees/{lf}/request-waiver", json={"reason": "maybe"})
    rej = client_as("credit_manager").post(
        f"/approvals/{req.json()['id']}/reject", json={"reason": "insufficient grounds"})
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
    assert _charge_status(client, cid, lf) == "assessed"


def test_config_change_executes_on_approval_and_reject_does_not(client, client_as):
    def value_of(key):
        params = {p["key"]: p["value"] for p in client.get("/config/parameters").json()}
        return params[key]

    original = value_of(cfg.KEY_MAX_DBR)

    # rejected -> unchanged
    r1 = client.put(f"/config/parameters/{cfg.KEY_MAX_DBR}", json={"value": 0.11})
    client_as("credit_manager").post(f"/approvals/{r1.json()['id']}/reject", json={})
    assert value_of(cfg.KEY_MAX_DBR) == original

    # approved -> changed
    r2 = client.put(f"/config/parameters/{cfg.KEY_MAX_DBR}", json={"value": 0.11})
    client_as("credit_manager").post(f"/approvals/{r2.json()['id']}/approve")
    assert value_of(cfg.KEY_MAX_DBR) == "0.11"


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #
def test_sales_employee_cannot_decide_or_list(client, client_as):
    ctx = _assessed_contract(client, "APR-4")
    lf = _late_fee_id(client, ctx["contract_id"])
    req = client.post(f"/late-fees/{lf}/request-waiver", json={"reason": "x"})
    aid = req.json()["id"]

    sales = client_as("sales_employee")
    assert sales.post(f"/approvals/{aid}/approve").status_code == 403
    assert sales.post(f"/approvals/{aid}/reject", json={}).status_code == 403
    assert sales.get("/approvals").status_code == 403


def test_request_waiver_rbac(client, client_as):
    ctx = _assessed_contract(client, "APR-5")
    lf = _late_fee_id(client, ctx["contract_id"])
    body = {"reason": "x"}
    assert client_as("sales_employee").post(
        f"/late-fees/{lf}/request-waiver", json=body).status_code == 403
    assert client_as("collections_officer").post(
        f"/late-fees/{lf}/request-waiver", json=body).status_code == 403
    assert client_as("finance_officer").post(
        f"/late-fees/{lf}/request-waiver", json=body).status_code == 201


def test_list_approvals_filters(client, client_as):
    ctx = _assessed_contract(client, "APR-6")
    lf = _late_fee_id(client, ctx["contract_id"])
    client.post(f"/late-fees/{lf}/request-waiver", json={"reason": "x"})
    client.put(f"/config/parameters/{cfg.KEY_MIN_INCOME}", json={"value": 500})

    mgr = client_as("credit_manager")
    assert {a["action_type"] for a in mgr.get("/approvals").json()} == {
        "late_fee.waive", "config.update"}
    only_cfg = mgr.get("/approvals", params={"action_type": "config.update"}).json()
    assert all(a["action_type"] == "config.update" for a in only_cfg)
    pend = mgr.get("/approvals", params={"status": "pending"}).json()
    assert all(a["status"] == "pending" for a in pend)
