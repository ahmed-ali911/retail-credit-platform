"""Fixes for the 5 gaps from docs/business-rules-catalogue.md §24.

Overdue-driven tests backdate installment due dates via the shared `db`
session and drive the run with an explicit `as_of`, so they are independent
of wall-clock date.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.contract import Installment
from app.models.customer import Customer
from app.services import config_service as cfg
from tests.helpers import active_contract, make_customer


def _backdate(db, contract_id, seqs_days: dict[int, int]):
    """Set each listed installment's due_date to `days` before today."""
    insts = {
        i.sequence_number: i
        for i in db.query(Installment).filter(Installment.contract_id == contract_id).all()
    }
    for seq, days in seqs_days.items():
        insts[seq].due_date = date.today() - timedelta(days=days)
    db.commit()


def _assess(client, as_of=None):
    body = {"as_of": as_of.isoformat()} if as_of else {}
    r = client.post("/jobs/assess-overdue", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Gap 1 — POST /products role gate
# --------------------------------------------------------------------------- #
_PRODUCT_BODY = {"name": "Widget", "category": "other", "cash_price": 500}


def test_non_officer_cannot_create_a_product(client_as):
    for role in ("sales_employee", "customer", "collections_officer", "finance_officer"):
        r = client_as(role).post("/products", json=_PRODUCT_BODY)
        assert r.status_code == 403, f"{role} should be 403, got {r.status_code}"


def test_credit_officer_and_admin_can_create_a_product(client_as):
    assert client_as("credit_officer").post("/products", json=_PRODUCT_BODY).status_code == 201
    assert client_as("credit_manager").post("/products", json=_PRODUCT_BODY).status_code == 201
    assert client_as("admin").post("/products", json=_PRODUCT_BODY).status_code == 201


# --------------------------------------------------------------------------- #
# Gap 2 — POST /contracts/{id}/payments ownership check for role=customer
# --------------------------------------------------------------------------- #
def test_customer_cannot_pay_a_contract_that_isnt_theirs(client, client_as, db, auth):
    mine = active_contract(client, national_id="GAP2-MINE")
    theirs = active_contract(client, national_id="GAP2-THEIRS")

    db.get(Customer, mine["customer"]["id"]).user_id = auth["users"]["customer"].id
    db.commit()

    cust = client_as("customer")
    first_total = mine["schedule"][0]["total"]

    # someone else's contract -> 403
    r_other = cust.post(
        f"/contracts/{theirs['contract_id']}/payments",
        json={"amount": first_total, "external_reference": "GAP2-X"},
    )
    assert r_other.status_code == 403

    # own contract -> succeeds
    r_own = cust.post(
        f"/contracts/{mine['contract_id']}/payments",
        json={"amount": first_total, "external_reference": "GAP2-OK"},
    )
    assert r_own.status_code == 200, r_own.text


def test_non_owner_staff_roles_still_gated_on_payments(client, client_as):
    ctx = active_contract(client, national_id="GAP2-STAFF")
    body = {"amount": 10, "external_reference": "GAP2-S"}
    # credit_officer / credit_manager / collections_officer are not payment staff
    for role in ("credit_officer", "credit_manager", "collections_officer"):
        assert client_as(role).post(
            f"/contracts/{ctx['contract_id']}/payments", json=body
        ).status_code == 403
    # finance_officer is
    assert client_as("finance_officer").post(
        f"/contracts/{ctx['contract_id']}/payments", json=body
    ).status_code == 200


# --------------------------------------------------------------------------- #
# Gap 3 — Promise-to-Pay lifecycle (kept / broken / manual override)
# --------------------------------------------------------------------------- #
def _open_case_id(client, contract_id):
    cases = client.get("/collections/cases", params={"contract_id": contract_id, "status": "open"}).json()
    assert cases, "expected an open collections case"
    return cases[0]["id"]


def _log_promise(client, case_id, amount, promised_date):
    r = client.post(
        f"/collections/cases/{case_id}/activities",
        json={
            "activity_type": "promise_to_pay",
            "notes": "will pay",
            "promised_amount": amount,
            "promised_date": promised_date.isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _activities(client, case_id):
    return client.get(f"/collections/cases/{case_id}").json()["activities"]


def test_payment_covering_a_pending_promise_marks_it_kept(client, db):
    ctx = active_contract(client, national_id="GAP3-KEPT")
    cid = ctx["contract_id"]
    _backdate(db, cid, {1: 40})
    _assess(client, as_of=date.today())
    case_id = _open_case_id(client, cid)

    promise = _log_promise(client, case_id, amount=5.0, promised_date=date.today() + timedelta(days=7))
    assert promise["promise_status"] == "pending"

    # a payment of >= the promised amount (but small enough not to clear overdue)
    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": 5.0, "external_reference": "GAP3-KEPT-PAY"},
    )
    assert r.status_code == 200, r.text

    acts = _activities(client, case_id)
    kept = next(a for a in acts if a["id"] == promise["id"])
    assert kept["promise_status"] == "kept"
    # the transition was logged as its own activity
    assert any(
        a["activity_type"] == "other" and "met" in (a["notes"] or "").lower()
        for a in acts
    )


def test_overdue_check_marks_an_unmet_past_due_promise_broken(client, db):
    ctx = active_contract(client, national_id="GAP3-BROKEN")
    cid = ctx["contract_id"]
    _backdate(db, cid, {1: 40})
    _assess(client, as_of=date.today())
    case_id = _open_case_id(client, cid)

    promise = _log_promise(client, case_id, amount=50.0, promised_date=date.today() - timedelta(days=1))

    # no payment made -> the extended overdue check marks it broken
    result = _assess(client, as_of=date.today())
    assert result["promises_broken"] == 1

    acts = _activities(client, case_id)
    broken = next(a for a in acts if a["id"] == promise["id"])
    assert broken["promise_status"] == "broken"
    assert any(
        a["activity_type"] == "other" and "broken" in (a["notes"] or "").lower()
        for a in acts
    )

    # a second run does not re-transition an already-broken promise
    assert _assess(client, as_of=date.today())["promises_broken"] == 0


def test_staff_manual_override_of_promise_status_still_works(client, client_as, db):
    ctx = active_contract(client, national_id="GAP3-OVERRIDE")
    cid = ctx["contract_id"]
    _backdate(db, cid, {1: 40})
    _assess(client, as_of=date.today())
    case_id = _open_case_id(client, cid)
    promise = _log_promise(client, case_id, amount=50.0, promised_date=date.today() - timedelta(days=1))
    _assess(client, as_of=date.today())  # auto-marks it broken

    # staff correct it back to kept, with a reason
    r = client_as("collections_officer").post(
        f"/collections/activities/{promise['id']}/promise-status",
        json={"status": "kept", "reason": "customer paid cash at the branch, receipt 12345"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["promise_status"] == "kept"

    acts = _activities(client, case_id)
    assert any(
        a["activity_type"] == "other" and "manually set to kept" in (a["notes"] or "")
        for a in acts
    )

    # a reason is required
    bad = client_as("collections_officer").post(
        f"/collections/activities/{promise['id']}/promise-status",
        json={"status": "pending"},
    )
    assert bad.status_code == 422

    # wrong role is rejected
    assert client_as("sales_employee").post(
        f"/collections/activities/{promise['id']}/promise-status",
        json={"status": "kept", "reason": "x"},
    ).status_code == 403


# --------------------------------------------------------------------------- #
# Gap 4 — late_fee_max_per_contract is now enforced when > 0
# --------------------------------------------------------------------------- #
def test_late_fee_per_contract_cap_skips_fees_beyond_the_max(client, db, set_config):
    ctx = active_contract(client, national_id="GAP4-CAP")
    cid = ctx["contract_id"]

    fees = [round(s["total"] * 0.02, 2) for s in ctx["schedule"][:4]]
    cap = round(fees[0] + fees[1], 2)  # exactly two fees fit
    set_config(cfg.KEY_LATE_FEE_MAX_PER_CONTRACT, cap)

    _backdate(db, cid, {1: 40, 2: 39, 3: 38, 4: 37})
    result = _assess(client, as_of=date.today())

    assert result["late_fees_assessed"] == 2
    assert result["late_fees_skipped_contract_cap"] == 2

    charges = client.get(f"/contracts/{cid}").json()["late_fee_charges"]
    assert len(charges) == 2
    assert sum(Decimal(str(c["amount"])) for c in charges) <= Decimal(str(cap))


def test_zero_cap_means_no_cap_regression(client, db):
    """Shipped default (0) must not change behaviour."""
    ctx = active_contract(client, national_id="GAP4-ZERO")
    cid = ctx["contract_id"]
    _backdate(db, cid, {1: 40, 2: 39, 3: 38})
    result = _assess(client, as_of=date.today())
    assert result["late_fees_assessed"] == 3
    assert result["late_fees_skipped_contract_cap"] == 0


# --------------------------------------------------------------------------- #
# Gap 5 — a waived fee no longer consumes the installment's one-fee allowance
# --------------------------------------------------------------------------- #
def test_waived_fee_allows_a_fresh_charge_on_the_same_installment(client, client_as, db):
    ctx = active_contract(client, national_id="GAP5-WAIVE")
    cid = ctx["contract_id"]
    _backdate(db, cid, {1: 40})

    first = _assess(client, as_of=date.today())
    assert first["late_fees_assessed"] == 1
    charge_id = client.get(f"/contracts/{cid}").json()["late_fee_charges"][0]["id"]

    # waive it via the existing maker-checker flow
    req = client.post(f"/late-fees/{charge_id}/request-waiver", json={"reason": "goodwill"})
    assert req.status_code == 201, req.text
    ok = client_as("credit_manager").post(f"/approvals/{req.json()['id']}/approve")
    assert ok.status_code == 200
    charges = client.get(f"/contracts/{cid}").json()["late_fee_charges"]
    assert charges[0]["status"] == "waived"

    # the installment is still overdue -> a NEW fee is now allowed
    second = _assess(client, as_of=date.today())
    assert second["late_fees_assessed"] == 1

    charges = client.get(f"/contracts/{cid}").json()["late_fee_charges"]
    assert len(charges) == 2
    statuses = sorted(c["status"] for c in charges)
    assert statuses == ["assessed", "waived"]

    # and it is still "once" — a third run does not add another
    third = _assess(client, as_of=date.today())
    assert third["late_fees_assessed"] == 0
