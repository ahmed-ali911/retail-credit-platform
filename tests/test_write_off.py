"""Write-off & Recovery — checkpoint 1 scope: eligibility engine, write-off
REQUEST creation, and maker-checker wiring. Financial EXECUTION is a later
checkpoint by explicit instruction — approval here only moves a request to
APPROVED; it never touches a balance, ledger entry, or accounting event (see
services/write_off.py's module docstring).

Confirmed design points this file exists to prove:
  * a Stage-3 / severely-delinquent contract is NOT automatically eligible —
    eligibility is a multi-indicator, explainable, informational verdict;
  * ELIGIBLE allows a normal request; NOT_ELIGIBLE blocks one; NOT_ELIGIBLE
    and UNAVAILABLE both allow a controlled EXCEPTION request (mandatory
    exception_justification), still through the same maker-checker;
  * both the system verdict and the exception rationale are preserved,
    side by side, on the request row;
  * maker != checker (the existing generic rule, reused unchanged).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.ecl import ECLAssessment
from tests.helpers import active_contract, created_contract, first_due_date


def _assess_overdue(client, as_of):
    r = client.post("/jobs/assess-overdue", json={"as_of": as_of.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _run_ecl(client, as_of):
    r = client.post("/ecl/run", json={"as_of": as_of.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _make_delinquent(client, national_id: str, days_past_due: int):
    """Backdates a fresh contract's first installment and runs BOTH the
    overdue job and an ECL run at that same as_of, so the resulting
    ECLAssessment.dpd/final_stage actually reflect it (origination's own
    assessment, taken at contract activation, is always DPD 0 / Stage 1)."""
    ctx = active_contract(client, national_id=national_id)
    cid = ctx["contract_id"]
    as_of = first_due_date(client, cid) + timedelta(days=days_past_due)
    _assess_overdue(client, as_of)
    _run_ecl(client, as_of)
    return ctx, cid, as_of


def _open_case_id(client, cid) -> int:
    cases = client.get("/collections/cases", params={"contract_id": cid, "status": "open"}).json()
    assert cases, "expected an open collection case"
    return cases[0]["id"]


def _log_activity(client, case_id, activity_type="call"):
    r = client.post(f"/collections/cases/{case_id}/activities", json={
        "activity_type": activity_type, "notes": "attempted contact",
    })
    assert r.status_code == 201, r.text
    return r.json()


def _request_write_off(client, cid, **kw):
    body = {
        "write_off_type": "FULL",
        "reason_code": "COLLECTIONS_EXHAUSTED",
        "justification": "Exhausted all reasonable collection efforts.",
    }
    body.update(kw)
    return client.post(f"/write-offs/contracts/{cid}/requests", json=body)


def _entity_id(resp) -> int:
    return int(resp.json()["entity_id"])


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #
def test_fresh_contract_is_not_eligible(client):
    ctx = active_contract(client, national_id="WO-FRESH")
    cid = ctx["contract_id"]
    elig = client.get(f"/write-offs/eligibility/{cid}").json()
    assert elig["status"] in ("NOT_ELIGIBLE", "UNAVAILABLE")
    assert elig["dpd"] == 0


def test_stage_3_alone_does_not_make_a_contract_eligible(client):
    """DPD 100 clears ECL's own default-DPD threshold (90) -> genuinely
    Stage 3, but is below write-off's own, separately-configured, DPD
    threshold (180) -> NOT_ELIGIBLE. Proves Stage 3 is not an automatic
    write-off trigger."""
    ctx, cid, as_of = _make_delinquent(client, "WO-STAGE3", 100)
    elig = client.get(f"/write-offs/eligibility/{cid}").json()

    default_row = next(i for i in elig["indicators"] if i["id"] == "default_status")
    assert default_row["result"] == "satisfied"  # confirms genuinely Stage 3
    assert elig["ecl_stage"] == 3

    dpd_row = next(i for i in elig["indicators"] if i["id"] == "dpd_threshold")
    assert dpd_row["result"] == "not_satisfied"
    assert elig["status"] == "NOT_ELIGIBLE"


def test_fully_qualifying_contract_is_eligible(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-ELIGIBLE", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    elig = client.get(f"/write-offs/eligibility/{cid}").json()
    assert elig["status"] == "ELIGIBLE"
    assert all(
        i["result"] == "satisfied"
        for i in elig["indicators"]
        if i["category"] != "informational"
    )


def test_active_promise_to_pay_blocks_eligibility(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-ACTIVEPTP", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    client.post(f"/collections/cases/{case_id}/activities", json={
        "activity_type": "promise_to_pay", "notes": "customer promised",
        "promised_amount": 50.0, "promised_date": (date.today() + timedelta(days=10)).isoformat(),
    })

    elig = client.get(f"/write-offs/eligibility/{cid}").json()
    ptp_row = next(i for i in elig["indicators"] if i["id"] == "no_active_promise_to_pay")
    assert ptp_row["result"] == "not_satisfied"
    assert elig["status"] == "NOT_ELIGIBLE"


def test_unavailable_indicators_are_never_fabricated(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-INFO", 200)
    elig = client.get(f"/write-offs/eligibility/{cid}").json()
    for ind_id in ("legal_status", "dispute_status", "restructuring_status"):
        row = next(i for i in elig["indicators"] if i["id"] == ind_id)
        assert row["result"] == "unavailable"
        assert row["category"] == "informational"


# --------------------------------------------------------------------------- #
# Write-off request — normal vs exception path
# --------------------------------------------------------------------------- #
def test_eligible_contract_allows_a_normal_request(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-NORMAL", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    r = _request_write_off(client, cid)
    assert r.status_code == 201, r.text
    wo = client.get(f"/write-offs/requests/{_entity_id(r)}").json()
    assert wo["eligibility_status"] == "ELIGIBLE"
    assert wo["is_exception"] is False
    assert wo["exception_justification"] is None
    assert wo["status"] == "PENDING"
    # FULL auto-computes requested amounts from the outstanding snapshot
    assert wo["requested_principal"] == pytest.approx(wo["snapshot_principal_outstanding"], abs=0.01)


def test_not_eligible_blocks_a_normal_request(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-BLOCKED", 100)
    r = _request_write_off(client, cid)
    assert r.status_code == 422, r.text
    assert "NOT_ELIGIBLE" in r.text


def test_not_eligible_allows_a_controlled_exception_request(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-EXCEPTION", 100)
    r = _request_write_off(
        client, cid,
        reason_code="MANAGEMENT_DECISION",
        exception_justification="Board-approved exception — fraud indicators present despite DPD.",
    )
    assert r.status_code == 201, r.text
    wo = client.get(f"/write-offs/requests/{_entity_id(r)}").json()
    assert wo["eligibility_status"] == "NOT_ELIGIBLE"
    assert wo["is_exception"] is True
    assert wo["exception_justification"]
    # both preserved, side by side, permanently
    assert wo["eligibility_snapshot"]


def test_exception_without_justification_is_still_blocked(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-NOEXCEPTJUST", 100)
    r = _request_write_off(client, cid, exception_justification="   ")
    assert r.status_code == 422


def test_unavailable_eligibility_also_requires_the_exception_path(client, db):
    ctx = active_contract(client, national_id="WO-UNAVAIL")
    cid = ctx["contract_id"]
    # Force the "no ECL assessment exists" branch — origination normally
    # always creates one; remove it to exercise UNAVAILABLE directly.
    db.query(ECLAssessment).filter(ECLAssessment.contract_id == cid).delete()
    db.commit()

    elig = client.get(f"/write-offs/eligibility/{cid}").json()
    assert elig["status"] == "UNAVAILABLE"

    blocked = _request_write_off(client, cid)
    assert blocked.status_code == 422

    allowed = _request_write_off(client, cid, exception_justification="No ECL data — manual review confirms uncollectible.")
    assert allowed.status_code == 201


def test_requested_amount_above_outstanding_component_is_rejected(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-OVERAMOUNT", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    r = _request_write_off(
        client, cid, write_off_type="PARTIAL", requested_principal=999999.0,
    )
    assert r.status_code == 422
    assert "exceeds" in r.text


def test_contract_must_be_active(client):
    ctx = created_contract(client, national_id="WO-NOTACTIVE")  # not yet delivered
    r = _request_write_off(client, ctx["contract_id"])
    assert r.status_code == 409


def test_duplicate_pending_request_is_blocked(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-DUP", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    first = _request_write_off(client, cid)
    assert first.status_code == 201
    second = _request_write_off(client, cid)
    assert second.status_code == 409


# --------------------------------------------------------------------------- #
# Maker-checker
# --------------------------------------------------------------------------- #
def test_maker_cannot_approve_own_write_off_request(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-SELFAPPROVE", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    r = _request_write_off(client, cid)
    approval_id = r.json()["id"]

    own = client.post(f"/approvals/{approval_id}/approve")
    assert own.status_code == 409
    assert "your own request" in own.text


def test_checker_approval_moves_status_to_approved_without_touching_balances(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-APPROVE", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    before = client.get(f"/contracts/{cid}/receivable").json()
    r = _request_write_off(client, cid)
    approval_id = r.json()["id"]

    ok = client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    assert ok.status_code == 200, ok.text

    wo = client.get(f"/write-offs/requests/{_entity_id(r)}").json()
    assert wo["status"] == "APPROVED"
    assert wo["approved_by"] is not None

    # deliberately no financial effect yet — execution is a later checkpoint
    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after == before

    events = client.get("/audit/events", params={"entity_type": "write_off_request"}).json()
    actions = {e["action"] for e in events}
    assert "writeoff.approved" in actions
    assert "writeoff.requested" in actions
    assert "writeoff.eligibility_evaluated" in actions


def test_checker_rejection_moves_status_to_rejected(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-REJECT", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    r = _request_write_off(client, cid)
    approval_id = r.json()["id"]

    rej = client_as("credit_manager").post(
        f"/approvals/{approval_id}/reject", json={"reason": "insufficient evidence"}
    )
    assert rej.status_code == 200, rej.text

    wo = client.get(f"/write-offs/requests/{_entity_id(r)}").json()
    assert wo["status"] == "REJECTED"

    events = client.get("/audit/events", params={"entity_type": "write_off_request"}).json()
    assert "writeoff.rejected" in {e["action"] for e in events}


def test_cancel_a_pending_request(client):
    ctx, cid, as_of = _make_delinquent(client, "WO-CANCEL", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    r = _request_write_off(client, cid)
    wo_id = _entity_id(r)

    cancelled = client.post(f"/write-offs/requests/{wo_id}/cancel", json={"reason": "raised in error"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    # a new request can now be made — no lingering "pending" block
    again = _request_write_off(client, cid)
    assert again.status_code == 201


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #
def test_sales_employee_cannot_view_or_request_write_off(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RBAC", 200)
    sales = client_as("sales_employee")
    assert sales.get(f"/write-offs/eligibility/{cid}").status_code == 403
    assert sales.post(f"/write-offs/contracts/{cid}/requests", json={
        "write_off_type": "FULL", "reason_code": "COLLECTIONS_EXHAUSTED",
        "justification": "Attempted request from an unauthorized role.",
    }).status_code == 403


def test_collections_officer_can_view_and_request(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-COLLOFFICER", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    officer = client_as("collections_officer")
    assert officer.get(f"/write-offs/eligibility/{cid}").status_code == 200
    assert officer.post(f"/write-offs/contracts/{cid}/requests", json={
        "write_off_type": "FULL", "reason_code": "COLLECTIONS_EXHAUSTED",
        "justification": "Collections officer requesting after exhausting contact attempts.",
    }).status_code == 201


# --------------------------------------------------------------------------- #
# Collections structured closure reason (adjustment #4)
# --------------------------------------------------------------------------- #
def test_normal_case_closure_records_the_cleared_reason(client):
    ctx = active_contract(client, national_id="WO-CLEARED")
    cid = ctx["contract_id"]
    as_of = first_due_date(client, cid) + timedelta(days=6)
    _assess_overdue(client, as_of)
    case_id = _open_case_id(client, cid)

    first_total = ctx["schedule"][0]["total"]
    client.post(f"/contracts/{cid}/payments", json={"amount": first_total, "external_reference": "WO-CLEARED-PAY"})

    detail = client.get(f"/collections/cases/{case_id}").json()
    assert detail["status"] == "closed"
    assert detail["closed_reason"] == "cleared"
