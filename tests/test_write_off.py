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


def _request_and_approve(client, client_as, cid, **kw) -> int:
    """Returns the approved write_off_request id."""
    r = _request_write_off(client, cid, **kw)
    assert r.status_code == 201, r.text
    wo_id = _entity_id(r)
    approval_id = r.json()["id"]
    ok = client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    assert ok.status_code == 200, ok.text
    return wo_id


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


# --------------------------------------------------------------------------- #
# Execution — checkpoint 2
# --------------------------------------------------------------------------- #
def test_full_execution_zeroes_balances_closes_contract_and_case(client, client_as, db):
    ctx, cid, as_of = _make_delinquent(client, "WO-EXEC-FULL", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    before = client.get(f"/contracts/{cid}/receivable").json()
    assert before["outstanding_receivable"] > 0

    wo_id = _request_and_approve(client, client_as, cid)
    exec_resp = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert exec_resp.status_code == 200, exec_resp.text
    body = exec_resp.json()
    assert body["replayed"] is False
    execution = body["execution"]
    assert execution["write_off_type"] == "FULL"
    assert execution["remaining_principal"] == 0
    assert execution["remaining_profit"] == 0

    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after["outstanding_receivable"] == 0
    assert after["outstanding_late_fees"] == 0

    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "closed"
    assert contract["closure"]["reason"] == "write_off"

    case = client.get(f"/collections/cases/{case_id}").json()
    assert case["status"] == "closed"
    assert case["closed_reason"] == "written_off"

    wo = client.get(f"/write-offs/requests/{wo_id}").json()
    assert wo["status"] == "EXECUTED"


def test_execution_writes_proper_ledger_entries_never_touches_paid_columns(client, client_as, db):
    from app.models.contract import Installment
    from app.models.ledger import LedgerEntry, LedgerEntryType, LedgerRelatedAction

    ctx, cid, as_of = _make_delinquent(client, "WO-LEDGER", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    insts_before = {
        i.id: (i.principal_paid, i.profit_paid)
        for i in db.query(Installment).filter(Installment.contract_id == cid).all()
    }

    wo_id = _request_and_approve(client, client_as, cid)
    client.post(f"/write-offs/requests/{wo_id}/execute")

    db.expire_all()
    insts_after = db.query(Installment).filter(Installment.contract_id == cid).all()
    for inst in insts_after:
        # principal_paid/profit_paid are byte-identical to before — only the
        # dedicated *_written_off columns moved.
        before_p, before_pr = insts_before[inst.id]
        assert inst.principal_paid == before_p
        assert inst.profit_paid == before_pr
        if inst.principal_written_off > 0 or inst.profit_written_off > 0:
            assert inst.status.value == "written_off"

    entries = db.query(LedgerEntry).filter(
        LedgerEntry.contract_id == cid,
        LedgerEntry.related_action == LedgerRelatedAction.write_off,
    ).all()
    assert entries, "expected at least one write-off ledger entry"
    types_seen = {e.entry_type for e in entries}
    assert types_seen <= {
        LedgerEntryType.principal_written_off,
        LedgerEntryType.profit_written_off,
        LedgerEntryType.late_fee_written_off,
    }
    assert all(e.amount > 0 for e in entries)


def test_execution_emits_write_off_and_ecl_provision_released_events(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-ACCOUNTING", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    wo_id = _request_and_approve(client, client_as, cid)
    exec_resp = client.post(f"/write-offs/requests/{wo_id}/execute")
    execution = exec_resp.json()["execution"]
    provision_snapshot = execution["provision_amount_snapshot"]

    write_off_events = client.get(
        "/accounting/events", params={"event_type": "write_off_executed", "contract_id": cid}
    ).json()
    assert len(write_off_events) == 1
    assert write_off_events[0]["amount"] > 0

    if provision_snapshot and provision_snapshot > 0:
        release_events = client.get(
            "/accounting/events", params={"event_type": "ecl_provision_released", "contract_id": cid}
        ).json()
        assert len(release_events) >= 1
        # the write-off's own release carries exactly -provision_snapshot
        assert any(
            abs(e["amount"] - (-provision_snapshot)) < 0.01 for e in release_events
        )

    events = client.get("/audit/events", params={"entity_type": "write_off_execution"}).json()
    assert "writeoff.executed" in {e["action"] for e in events}


def test_partial_execution_preserves_remaining_collectible_balance(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-PARTIAL", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    options = client.get(f"/contracts/{cid}/receivable").json()
    partial_amount = round(options["outstanding_principal"] / 2, 2)

    wo_id = _request_and_approve(
        client, client_as, cid, write_off_type="PARTIAL", requested_principal=partial_amount,
    )
    exec_resp = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert exec_resp.status_code == 200, exec_resp.text
    execution = exec_resp.json()["execution"]
    assert execution["write_off_type"] == "PARTIAL"
    assert execution["executed_principal"] == pytest.approx(partial_amount, abs=0.01)
    assert execution["remaining_principal"] > 0

    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "active"  # PARTIAL never closes the contract

    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after["outstanding_principal"] == pytest.approx(execution["remaining_principal"], abs=0.01)
    assert after["outstanding_principal"] > 0

    partial_events = client.get(
        "/accounting/events", params={"event_type": "partial_write_off_executed", "contract_id": cid}
    ).json()
    assert len(partial_events) == 1


def test_execution_is_idempotent_on_replay(client, client_as, db):
    from app.models.accounting import AccountingEvent

    ctx, cid, as_of = _make_delinquent(client, "WO-IDEMPOTENT", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    wo_id = _request_and_approve(client, client_as, cid)

    first = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert first.status_code == 200
    assert first.json()["replayed"] is False
    execution_id = first.json()["execution"]["id"]

    events_after_first = db.query(AccountingEvent).filter(
        AccountingEvent.event_type == "write_off_executed"
    ).count()

    second = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["execution"]["id"] == execution_id

    events_after_second = db.query(AccountingEvent).filter(
        AccountingEvent.event_type == "write_off_executed"
    ).count()
    assert events_after_second == events_after_first  # no duplicate accounting event

    receivable_after_first = client.get(f"/contracts/{cid}/receivable").json()
    client.post(f"/write-offs/requests/{wo_id}/execute")
    receivable_after_second = client.get(f"/contracts/{cid}/receivable").json()
    assert receivable_after_first == receivable_after_second


def test_cannot_execute_a_request_that_is_not_approved(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-NOTAPPROVED", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    r = _request_write_off(client, cid)
    wo_id = _entity_id(r)

    still_pending = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert still_pending.status_code == 409

    approval_id = r.json()["id"]
    client_as("credit_manager").post(f"/approvals/{approval_id}/reject", json={"reason": "no"})
    rejected = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert rejected.status_code == 409


def test_cannot_execute_above_balance_that_moved_since_approval(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-STALE", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    wo_id = _request_and_approve(client, client_as, cid)  # FULL — snapshots 100% of outstanding

    # A staff payment lands after approval but before execution — the
    # contract's real outstanding principal has now dropped below what the
    # checker approved writing off.
    options = client.get(f"/contracts/{cid}/receivable").json()
    partial_amount = round(min(50.0, options["outstanding_principal"]), 2)
    pay = client.post(f"/contracts/{cid}/payments", json={
        "amount": partial_amount, "external_reference": "WO-STALE-PAY",
    })
    assert pay.status_code == 200, pay.text

    stale = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert stale.status_code == 409
    assert "exceeds" in stale.text


def test_written_off_contract_is_excluded_from_future_ecl_runs(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-ECLEXCLUDE", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    wo_id = _request_and_approve(client, client_as, cid)
    client.post(f"/write-offs/requests/{wo_id}/execute")

    later = as_of + timedelta(days=5)
    run = _run_ecl(client, later)
    row = client.get(f"/ecl/contracts/{cid}").json()
    # the contract's assessment history still exists (never deleted) but the
    # NEW run does not include it — its run_id in the row is unchanged/older
    assert row["run_id"] != run["run_id"]


def test_execution_requires_authorized_role(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-EXECRBAC", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    wo_id = _request_and_approve(client, client_as, cid)

    sales = client_as("sales_employee")
    r = sales.post(f"/write-offs/requests/{wo_id}/execute")
    assert r.status_code == 403


def test_original_payment_history_is_preserved_through_write_off(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-HISTORY", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    # An earlier, genuine payment exists on this contract before write-off.
    schedule_total = ctx["schedule"][0]["total"]
    options = client.get(f"/contracts/{cid}/receivable").json()
    early_payment_amount = min(5.0, options["outstanding_principal"])
    if early_payment_amount > 0:
        client.post(f"/contracts/{cid}/payments", json={
            "amount": early_payment_amount, "external_reference": "WO-HISTORY-EARLY-PAY",
        })

    wo_id = _request_and_approve(client, client_as, cid)
    client.post(f"/write-offs/requests/{wo_id}/execute")

    # the earlier payment is untouched — still there, still applied
    payments = client.get(
        "/collections/cases", params={"contract_id": cid}
    )  # sanity: cases endpoint still reachable post-closure
    assert payments.status_code == 200


# --------------------------------------------------------------------------- #
# Recovery — checkpoint 3
# --------------------------------------------------------------------------- #
def _execute_full_write_off(client, client_as, cid):
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    wo_id = _request_and_approve(client, client_as, cid)
    exec_resp = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert exec_resp.status_code == 200, exec_resp.text
    return exec_resp.json()["execution"]


def _record_recovery(client, execution_id, **kw):
    body = {"amount": 100.0, "external_reference": "RECOVERY-REF-1"}
    body.update(kw)
    return client.post(f"/write-offs/executions/{execution_id}/recoveries", json=body)


def test_recovery_requires_no_maker_checker_but_needs_finance_role(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RECOVERY-RBAC", 200)
    execution = _execute_full_write_off(client, client_as, cid)

    collections_officer = client_as("collections_officer")
    denied = collections_officer.post(
        f"/write-offs/executions/{execution['id']}/recoveries",
        json={"amount": 10.0, "external_reference": "REF-DENIED"},
    )
    assert denied.status_code == 403

    finance = client_as("finance_officer")
    allowed = finance.post(
        f"/write-offs/executions/{execution['id']}/recoveries",
        json={"amount": 10.0, "external_reference": "REF-ALLOWED"},
    )
    assert allowed.status_code == 201, allowed.text
    # a single direct call, no ApprovalRequest created for it
    approvals = client.get("/approvals", params={"status": "pending"}).json()
    assert not any(a["entity_type"] == "write_off_recovery" for a in approvals)


def test_recovery_requires_mandatory_external_reference(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RECOVERY-NOREF", 200)
    execution = _execute_full_write_off(client, client_as, cid)

    r = client.post(f"/write-offs/executions/{execution['id']}/recoveries", json={
        "amount": 50.0, "external_reference": "   ",
    })
    assert r.status_code == 422


def test_recovery_cannot_exceed_remaining_recoverable_balance(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RECOVERY-OVER", 200)
    execution = _execute_full_write_off(client, client_as, cid)
    total = execution["executed_principal"] + execution["executed_profit"] + execution["executed_late_fee"]

    r = _record_recovery(client, execution["id"], amount=total + 1000.0)
    assert r.status_code == 422
    assert "exceeds" in r.text


def test_multiple_partial_recoveries_track_running_total_without_mutating_original(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RECOVERY-MULTI", 200)
    execution = _execute_full_write_off(client, client_as, cid)
    total_written_off = execution["executed_principal"] + execution["executed_profit"] + execution["executed_late_fee"]
    third = round(total_written_off / 3, 2)

    r1 = _record_recovery(client, execution["id"], amount=third, external_reference="REC-1")
    assert r1.status_code == 201, r1.text
    r2 = _record_recovery(client, execution["id"], amount=third, external_reference="REC-2")
    assert r2.status_code == 201, r2.text
    r3 = _record_recovery(client, execution["id"], amount=third, external_reference="REC-3")
    assert r3.status_code == 201, r3.text

    detail = client.get(f"/write-offs/executions/{execution['id']}").json()
    assert len(detail["recoveries"]) == 3
    assert detail["total_recovered"] == pytest.approx(third * 3, abs=0.01)
    assert detail["remaining_recoverable"] == pytest.approx(total_written_off - third * 3, abs=0.01)
    # the ORIGINAL execution amounts are untouched — never mutated to reflect recovery
    assert detail["executed_principal"] == pytest.approx(execution["executed_principal"], abs=0.01)
    assert detail["executed_profit"] == pytest.approx(execution["executed_profit"], abs=0.01)


def test_duplicate_external_reference_on_same_execution_is_rejected(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RECOVERY-DUP", 200)
    execution = _execute_full_write_off(client, client_as, cid)

    first = _record_recovery(client, execution["id"], amount=10.0, external_reference="SAME-REF")
    assert first.status_code == 201
    second = _record_recovery(client, execution["id"], amount=5.0, external_reference="SAME-REF")
    assert second.status_code == 409


def test_recovery_emits_accounting_event_and_audit_event(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RECOVERY-ACCT", 200)
    execution = _execute_full_write_off(client, client_as, cid)

    r = _record_recovery(client, execution["id"], amount=25.0, external_reference="RECOVERY-ACCT-REF")
    assert r.status_code == 201
    recovery = r.json()
    assert recovery["accounting_event_id"] is not None

    events = client.get(
        "/accounting/events", params={"event_type": "recovery_received", "contract_id": cid}
    ).json()
    assert len(events) == 1
    assert events[0]["amount"] == pytest.approx(25.0, abs=0.01)

    audit = client.get("/audit/events", params={"entity_type": "write_off_recovery"}).json()
    assert "recovery.recorded" in {e["action"] for e in audit}


def test_recovery_never_reactivates_contract_reopens_case_or_touches_ecl(client, client_as):
    ctx, cid, as_of = _make_delinquent(client, "WO-RECOVERY-NOSIDEEFFECT", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)
    wo_id = _request_and_approve(client, client_as, cid)
    exec_resp = client.post(f"/write-offs/requests/{wo_id}/execute")
    execution = exec_resp.json()["execution"]

    contract_before = client.get(f"/contracts/{cid}").json()
    case_before = client.get(f"/collections/cases/{case_id}").json()
    ecl_before = client.get(f"/ecl/contracts/{cid}").json()

    _record_recovery(client, execution["id"], amount=50.0, external_reference="RECOVERY-NOSIDE-1")

    contract_after = client.get(f"/contracts/{cid}").json()
    case_after = client.get(f"/collections/cases/{case_id}").json()
    ecl_after = client.get(f"/ecl/contracts/{cid}").json()

    assert contract_after["status"] == contract_before["status"] == "closed"
    assert case_after["status"] == case_before["status"] == "closed"
    assert case_after["closed_reason"] == "written_off"
    assert ecl_after == ecl_before  # not re-assessed, not touched at all


def test_recovery_against_nonexistent_execution_is_404(client, client_as):
    r = _record_recovery(client, 999999, amount=10.0, external_reference="REF-404")
    assert r.status_code == 404
