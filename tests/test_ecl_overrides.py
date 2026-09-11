"""ECL manual overrides — MANDATORY maker-checker.

  * Stage Override and Parameter Override are separate actions.
  * The automated assessment is NEVER mutated — the override is its own record;
    ``final_*`` reflects an ACTIVE override, ``automated_*`` does not.
  * Maker != Checker is enforced (409 on self-approval).
  * An ACTIVE approved override survives future automatic runs.
  * Overrides expire on ``effective_to`` and can be cancelled.
  * Every step is on the immutable audit trail.
"""
from datetime import date, timedelta

import pytest

from app.models.contract import Installment
from tests.helpers import active_contract


def _run(client, as_of=None, post=False):
    body = {"post": post}
    if as_of is not None:
        body["as_of"] = as_of.isoformat() if not isinstance(as_of, str) else as_of
    r = client.post("/ecl/run", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _detail(client, cid):
    return client.get(f"/ecl/contracts/{cid}").json()


def _stage_override(client, cid, **kw):
    body = {
        "stage": 3,
        "reason_code": "UNLIKELY_TO_PAY",
        "justification": "Borrower disclosed job loss; recovery unlikely.",
        "evidence_ref": "DOC-123",
    }
    body.update(kw)
    return client.post(f"/ecl/assessments/{cid}/request-stage-override", json=body)


# --------------------------------------------------------------------------- #
# stage override
# --------------------------------------------------------------------------- #
def test_stage_override_keeps_automated_result_and_changes_only_final(client, client_as):
    ctx = active_contract(client, national_id="OV-1")
    cid = ctx["contract_id"]
    _run(client)
    before = _detail(client, cid)
    assert before["automated_stage"] == 1

    r = _stage_override(client, cid, stage=3)
    assert r.status_code == 201, r.text
    approval_id = r.json()["id"]

    client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    _run(client, as_of=date.today() + timedelta(days=1))

    after = _detail(client, cid)
    assert after["automated_stage"] == 1          # untouched
    assert after["override_stage"] == 3
    assert after["final_stage"] == 3
    assert after["final_ecl"] > after["automated_ecl"]
    assert after["override_adjustment"] == pytest.approx(
        after["final_ecl"] - after["calculated_ecl"], abs=0.01
    )


def test_maker_cannot_approve_their_own_override(client):
    ctx = active_contract(client, national_id="OV-2")
    cid = ctx["contract_id"]
    _run(client)
    approval_id = _stage_override(client, cid).json()["id"]

    r = client.post(f"/approvals/{approval_id}/approve")
    assert r.status_code == 409
    assert "your own request" in r.text
    assert _detail(client, cid)["final_stage"] == _detail(client, cid)["automated_stage"]


def test_rejected_override_leaves_final_equal_to_automated(client, client_as):
    ctx = active_contract(client, national_id="OV-3")
    cid = ctx["contract_id"]
    _run(client)
    approval_id = _stage_override(client, cid).json()["id"]

    client_as("credit_manager").post(
        f"/approvals/{approval_id}/reject", json={"reason": "insufficient evidence"}
    )
    _run(client, as_of=date.today() + timedelta(days=1))

    body = _detail(client, cid)
    assert body["final_stage"] == body["automated_stage"]
    ov = client.get("/ecl/overrides", params={"contract_id": cid}).json()[0]
    assert ov["status"] == "REJECTED"


def test_active_override_survives_a_later_automatic_run(client, client_as):
    ctx = active_contract(client, national_id="OV-4")
    cid = ctx["contract_id"]
    _run(client)
    approval_id = _stage_override(client, cid, stage=2).json()["id"]
    client_as("credit_manager").post(f"/approvals/{approval_id}/approve")

    for n in range(1, 4):
        _run(client, as_of=date.today() + timedelta(days=n))
        body = _detail(client, cid)
        assert body["automated_stage"] == 1
        assert body["final_stage"] == 2               # override still in force


# --------------------------------------------------------------------------- #
# parameter override
# --------------------------------------------------------------------------- #
def test_parameter_override_pins_pd_and_lgd(client, client_as):
    ctx = active_contract(client, national_id="OV-5")
    cid = ctx["contract_id"]
    _run(client)

    r = client.post(
        f"/ecl/assessments/{cid}/request-parameter-override",
        json={
            "pd_12m": 0.30,
            "lgd": 0.70,
            "reason_code": "MODEL_LIMITATION",
            "justification": "Model does not capture the sector shock here.",
            "evidence_ref": "MEMO-9",
        },
    )
    assert r.status_code == 201, r.text
    approval_id = r.json()["id"]
    client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    _run(client, as_of=date.today() + timedelta(days=1))

    body = _detail(client, cid)
    assert body["automated_pd_12m"] == pytest.approx(0.02)   # untouched
    assert body["override_pd_12m"] == pytest.approx(0.30)
    assert body["final_pd_12m"] == pytest.approx(0.30)
    assert body["final_lgd"] == pytest.approx(0.70)
    assert body["final_ecl"] == pytest.approx(round(body["final_ead"] * 0.30 * 0.70, 2), abs=0.01)


def test_parameter_override_rejects_a_non_configured_parameter(client):
    ctx = active_contract(client, national_id="OV-6")
    cid = ctx["contract_id"]
    _run(client)
    r = client.post(
        f"/ecl/assessments/{cid}/request-parameter-override",
        json={
            "reason_code": "OTHER",
            "justification": "trying to override nothing valid",
        },
    )
    assert r.status_code == 422


def test_parameter_override_is_rejected_outside_three_stage(client, set_ecl_config):
    """Regression for the pre-commit review finding: unlike
    request_stage_override, request_parameter_override had no methodology
    gate — the request would be accepted and, once approved, the override
    would show ACTIVE while leaving final_ecl == automated_ecl untouched (a
    silent no-op on an approved control action, confirmed empirically before
    this fix: _assess_one's non-three_stage branch always sets
    final_ecl = automated_ecl and never reads override_pd_12m/lgd/ead)."""
    set_ecl_config(methodology="simplified_lifetime")
    ctx = active_contract(client, national_id="OV-12")
    cid = ctx["contract_id"]
    _run(client)

    r = client.post(
        f"/ecl/assessments/{cid}/request-parameter-override",
        json={
            "lgd": 0.95,
            "reason_code": "MODEL_LIMITATION",
            "justification": "Attempting to pin LGD outside the three_stage methodology.",
            "evidence_ref": "DOC-12",
        },
    )
    assert r.status_code == 409, r.text
    assert "three_stage" in r.json()["detail"]

    # no approval request, no override row, and no pending-override lock left
    # behind by the rejected attempt
    assert client.get("/ecl/overrides", params={"contract_id": cid}).json() == []
    r2 = client.post(
        f"/ecl/assessments/{cid}/request-parameter-override",
        json={
            "lgd": 0.9,
            "reason_code": "MODEL_LIMITATION",
            "justification": "A second attempt should hit the same gate, not a pending-lock 409.",
            "evidence_ref": "DOC-12b",
        },
    )
    assert r2.status_code == 409
    assert "three_stage" in r2.json()["detail"]


def test_evidence_is_required_by_policy(client):
    ctx = active_contract(client, national_id="OV-7")
    cid = ctx["contract_id"]
    _run(client)
    r = _stage_override(client, cid, evidence_ref=None)
    assert r.status_code == 422
    assert "evidence" in r.text.lower()


# --------------------------------------------------------------------------- #
# lifecycle — expiry / cancel
# --------------------------------------------------------------------------- #
def test_override_expires_after_its_window(client, client_as):
    ctx = active_contract(client, national_id="OV-8")
    cid = ctx["contract_id"]
    _run(client)
    approval_id = _stage_override(
        client, cid, stage=2,
        effective_from=date.today().isoformat(),
        effective_to=(date.today() + timedelta(days=5)).isoformat(),
    ).json()["id"]
    client_as("credit_manager").post(f"/approvals/{approval_id}/approve")

    _run(client, as_of=date.today() + timedelta(days=1))
    assert _detail(client, cid)["final_stage"] == 2

    client.post("/ecl/jobs/expire-overrides",
                params={"as_of": (date.today() + timedelta(days=10)).isoformat()})
    _run(client, as_of=date.today() + timedelta(days=11))

    body = _detail(client, cid)
    assert body["final_stage"] == body["automated_stage"]
    ov = client.get("/ecl/overrides", params={"contract_id": cid}).json()[0]
    assert ov["status"] == "EXPIRED"


def test_override_can_be_cancelled(client, client_as):
    ctx = active_contract(client, national_id="OV-9")
    cid = ctx["contract_id"]
    _run(client)
    approval_id = _stage_override(client, cid, stage=2).json()["id"]
    client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    ov_id = client.get("/ecl/overrides", params={"contract_id": cid}).json()[0]["id"]

    r = client.post(f"/ecl/overrides/{ov_id}/cancel", json={"reason": "raised in error"})
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"

    _run(client, as_of=date.today() + timedelta(days=1))
    assert _detail(client, cid)["final_stage"] == _detail(client, cid)["automated_stage"]


# --------------------------------------------------------------------------- #
# audit + accounting
# --------------------------------------------------------------------------- #
def test_override_lifecycle_is_on_the_audit_trail(client, client_as):
    ctx = active_contract(client, national_id="OV-10")
    cid = ctx["contract_id"]
    _run(client)
    approval_id = _stage_override(client, cid).json()["id"]
    client_as("credit_manager").post(f"/approvals/{approval_id}/approve")

    events = client.get("/audit/events", params={"entity_type": "ecl_override"}).json()
    actions = {e["action"] for e in events}
    assert "ecl.override_approved" in actions


def test_override_adjustment_emits_its_own_accounting_event_on_post(client, client_as):
    ctx = active_contract(client, national_id="OV-11")
    cid = ctx["contract_id"]
    _run(client)
    approval_id = _stage_override(client, cid, stage=3).json()["id"]
    client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    _run(client, as_of=date.today() + timedelta(days=1), post=True)

    ev = client.get(
        "/accounting/events",
        params={"event_type": "ecl_provision_override_adjustment"},
    ).json()
    assert len(ev) == 1
    assert ev[0]["contract_id"] is not None
    assert ev[0]["amount"] > 0
