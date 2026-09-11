"""ECL & Provision engine — three-stage primary, simplified-lifetime retained.

Framing locked in by these tests:
  * ECL is assessed from contract activation, not from delinquency;
  * the stage is decided by a CONFIGURABLE engine (Stage-3 triggers first, then
    SICR), of which DPD is one trigger among quantitative + qualitative ones —
    not a hard-coded DPD ladder;
  * every PD / LGD / threshold is a placeholder from the *versioned*
    ``ECLConfiguration`` (BUSINESS / RISK MODEL DECISION REQUIRED) — none is
    claimed to be an IFRS 9 value;
  * the AUTOMATED result, any manual OVERRIDE and the FINAL result are stored
    separately — the automated result is never mutated;
  * a run is immutable; POSTing it emits the accounting events;
  * the module is invisible to sales / collections / credit officers.
"""
from datetime import date, timedelta

import pytest

from app.models.contract import Installment
from app.models.customer import Customer
from tests.helpers import active_contract, first_due_date


def _run(client, as_of=None, post=False):
    body = {"post": post}
    if as_of is not None:
        body["as_of"] = as_of if isinstance(as_of, str) else as_of.isoformat()
    r = client.post("/ecl/run", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _detail(client, cid):
    r = client.get(f"/ecl/contracts/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def _backdate_first_installment(db, contract_id, days):
    inst = (
        db.query(Installment)
        .filter(Installment.contract_id == contract_id)
        .order_by(Installment.sequence_number)
        .first()
    )
    inst.due_date = date.today() - timedelta(days=days)
    db.commit()


def _set_risk_score(db, customer_national_id, score):
    c = db.query(Customer).filter(Customer.national_id == customer_national_id).first()
    c.risk_score = score
    db.commit()


# --------------------------------------------------------------------------- #
# 1. ECL exists from day one
# --------------------------------------------------------------------------- #
def test_new_contract_is_stage_1_from_activation(client):
    ctx = active_contract(client, national_id="ECL-1")
    body = _detail(client, ctx["contract_id"])

    assert body["methodology"] == "three_stage"
    assert body["dpd"] == 0
    assert body["ead"] > 0
    assert body["risk_rating"] == "B"          # risk_score 700 -> grade B (placeholder map)
    assert body["automated_stage"] == 1
    assert body["final_stage"] == 1
    # ECL = EAD x 12-month PD x LGD  (placeholders 2% / 40%)
    assert body["automated_ecl"] == pytest.approx(round(body["ead"] * 0.02 * 0.40, 2), abs=0.01)
    assert body["final_ecl"] == body["automated_ecl"]
    assert "Stage 1" in body["stage_reason"]


def test_dashboard_reports_day_one_provision_and_coverage(client):
    active_contract(client, national_id="ECL-DASH")
    d = client.get("/ecl/dashboard").json()
    assert d["active_methodology"] == "three_stage"
    assert d["ecl_config_version"] == 1
    assert d["total_exposure"] > 0
    assert d["total_ecl"] > 0
    assert d["contracts_assessed"] == 1
    assert d["coverage_ratio"] == pytest.approx(0.008, abs=0.001)
    assert isinstance(d["stage_exposure"], dict)
    assert d["stage_exposure"]["1"] > 0


# --------------------------------------------------------------------------- #
# 2. methodology is a versioned-config switch, not a code change
# --------------------------------------------------------------------------- #
def test_switching_to_simplified_lifetime_via_config_version(client, set_ecl_config):
    ctx = active_contract(client, national_id="ECL-2")
    cid = ctx["contract_id"]

    cfg = set_ecl_config(methodology="simplified_lifetime")
    assert cfg.version == 2
    _run(client)

    body = _detail(client, cid)
    assert body["methodology"] == "simplified_lifetime"
    assert body["final_stage"] is None                 # no staging under Path A
    assert body["loss_rate"] == pytest.approx(0.10)
    assert body["final_ecl"] == pytest.approx(round(body["ead"] * 0.10, 2), abs=0.01)


def test_unknown_methodology_is_rejected(client, set_ecl_config):
    with pytest.raises(ValueError, match="made_up_model"):
        set_ecl_config(methodology="made_up_model")


# --------------------------------------------------------------------------- #
# 3. stage engine — DPD is ONE trigger, not the only one
# --------------------------------------------------------------------------- #
def test_default_dpd_trigger_moves_contract_to_stage_3(client, db):
    ctx = active_contract(client, national_id="ECL-3")
    cid = ctx["contract_id"]
    _backdate_first_installment(db, cid, 95)
    _run(client, as_of=date.today() + timedelta(days=1))

    body = _detail(client, cid)
    assert body["dpd"] >= 90
    assert body["automated_stage"] == 3
    # Stage 3 uses the elevated lifetime PD / LGD (placeholders 45% / 50%)
    assert body["automated_ecl"] == pytest.approx(round(body["ead"] * 0.45 * 0.50, 2), abs=0.01)
    assert "Credit-impaired" in body["stage_reason"]
    fired = [t for t in body["stage_triggers"] if t["triggered"]]
    assert any(t["id"] == "dpd_default" for t in fired)


def test_stage_2_can_be_driven_by_a_qualitative_signal_with_zero_dpd(client, db):
    """An open collections case pushes a contract to Stage 2 even at DPD 0 —
    proving the stage is not a pure DPD function."""
    overdue = active_contract(client, national_id="ECL-SICR-A")
    clean = active_contract(client, national_id="ECL-SICR-B")

    _backdate_first_installment(db, overdue["contract_id"], 40)
    as_of = first_due_date(client, overdue["contract_id"]) + timedelta(days=40)
    client.post("/jobs/assess-overdue", json={"as_of": as_of.isoformat()})
    # put the overdue installment back so DPD is 0 again but the case stays open
    _backdate_first_installment(db, overdue["contract_id"], -5)

    _run(client, as_of=date.today())
    staged = _detail(client, overdue["contract_id"])
    performing = _detail(client, clean["contract_id"])

    assert staged["dpd"] == 0 and performing["dpd"] == 0
    assert staged["automated_stage"] == 2
    assert performing["automated_stage"] == 1
    fired = [t["id"] for t in staged["stage_triggers"] if t["triggered"]]
    assert "collections_case" in fired


def test_rating_deterioration_since_origination_triggers_sicr(client, db):
    ctx = active_contract(client, national_id="ECL-RATING")
    cid = ctx["contract_id"]
    # origination rating was B (score 700). Drop the score to grade E.
    _set_risk_score(db, "ECL-RATING", 520)
    _run(client)

    body = _detail(client, cid)
    assert body["dpd"] == 0
    assert body["risk_rating"] == "E"
    assert body["origination_rating"] == "B"
    assert body["automated_stage"] == 2
    fired = [t["id"] for t in body["stage_triggers"] if t["triggered"]]
    assert "rating_deterioration" in fired


# --------------------------------------------------------------------------- #
# 4. "Why is this customer Stage 2/3?" — always answerable
# --------------------------------------------------------------------------- #
def test_stage_explainability_is_mandatory_for_stage_2_and_3(client, db):
    ctx = active_contract(client, national_id="ECL-WHY")
    cid = ctx["contract_id"]
    _backdate_first_installment(db, cid, 45)
    _run(client, as_of=date.today() + timedelta(days=1))

    body = _detail(client, cid)
    assert body["automated_stage"] == 2
    assert body["stage_reason"]                    # a human sentence
    triggers = body["stage_triggers"]
    assert triggers and all({"id", "name", "triggered", "detail"} <= set(t) for t in triggers)
    assert any(t["triggered"] for t in triggers)


# --------------------------------------------------------------------------- #
# 5. provision movement — opening / calculated / adjustment / closing
# --------------------------------------------------------------------------- #
def test_provision_increase_is_tracked_as_a_movement(client, db):
    ctx = active_contract(client, national_id="ECL-MOVE")
    cid = ctx["contract_id"]
    _run(client)                                       # baseline (Stage 1)

    _backdate_first_installment(db, cid, 40)
    summary = _run(client, as_of=date.today() + timedelta(days=2))
    body = _detail(client, cid)

    assert body["opening_provision"] == pytest.approx(round(body["ead"] * 0.02 * 0.40, 2), abs=0.01)
    assert body["closing_provision"] > body["opening_provision"]
    assert body["movement_type"] == "increased"
    assert summary["total_provision_movement"] > 0


def test_provision_release_when_the_model_loss_rate_is_lowered(client, db, set_ecl_config):
    """Release path via the simplified-lifetime rate (no staging, so no curing
    interferes) — Finance revises the lifetime loss rate down."""
    ctx = active_contract(client, national_id="ECL-REL")
    cid = ctx["contract_id"]
    set_ecl_config(methodology="simplified_lifetime", lifetime_loss_rate=0.20)
    _run(client)
    high = _detail(client, cid)["closing_provision"]
    assert high == pytest.approx(round(_detail(client, cid)["ead"] * 0.20, 2), abs=0.01)

    set_ecl_config(lifetime_loss_rate=0.05)
    summary = _run(client, as_of=date.today() + timedelta(days=1))
    body = _detail(client, cid)

    assert body["closing_provision"] < high
    assert body["movement_type"] == "released"
    assert summary["total_provision_movement"] < 0


# --------------------------------------------------------------------------- #
# 6. runs are immutable; POST emits the accounting events
# --------------------------------------------------------------------------- #
def test_posting_a_run_emits_one_portfolio_event_plus_per_contract_events(client, db):
    a = active_contract(client, national_id="ECL-6A")
    active_contract(client, national_id="ECL-6B")
    _backdate_first_installment(db, a["contract_id"], 40)

    summary = _run(client, as_of=date.today() + timedelta(days=1), post=True)
    assert summary["status"] == "POSTED"
    assert summary["accounting_event_id"] is not None

    roll = client.get(
        "/accounting/events", params={"event_type": "ecl_provision_movement"}
    ).json()
    assert len(roll) == 1
    assert roll[0]["contract_id"] is None
    assert roll[0]["amount"] == pytest.approx(summary["total_provision_movement"], abs=0.01)

    created = client.get(
        "/accounting/events", params={"event_type": "ecl_provision_created"}
    ).json()
    assert len(created) == 2                          # first provision for each contract


def test_a_posted_run_cannot_be_posted_again(client):
    active_contract(client, national_id="ECL-IMMUT")
    summary = _run(client, post=True)
    r = client.post(f"/ecl/runs/{summary['run_id']}/post")
    assert r.status_code == 200
    assert r.json()["status"] == "POSTED"             # idempotent, not a re-emit
    events = client.get(
        "/accounting/events", params={"event_type": "ecl_provision_movement"}
    ).json()
    assert len(events) == 1


def test_reruns_keep_full_history(client, db):
    ctx = active_contract(client, national_id="ECL-HIST")
    cid = ctx["contract_id"]
    _run(client)
    _backdate_first_installment(db, cid, 40)
    _run(client, as_of=date.today() + timedelta(days=1))
    _backdate_first_installment(db, cid, 95)
    _run(client, as_of=date.today() + timedelta(days=2))

    runs = client.get("/ecl/runs").json()
    assert len(runs) == 3
    hist = _detail(client, cid)["history"]
    # 3 runs + the day-one origination assessment
    assert len(hist) == 4
    assert [h["automated_stage"] for h in hist][:3] == [3, 2, 1]   # newest first
    assert hist[-1]["run_id"] is None                             # origination row


# --------------------------------------------------------------------------- #
# 7. stage curing — no snap back from Stage 3 to Stage 1 on one payment
# --------------------------------------------------------------------------- #
def test_stage_3_does_not_immediately_cure_to_stage_1(client, db):
    ctx = active_contract(client, national_id="ECL-CURE")
    cid = ctx["contract_id"]
    _backdate_first_installment(db, cid, 95)
    _run(client, as_of=date.today() + timedelta(days=1))
    assert _detail(client, cid)["automated_stage"] == 3

    # the arrears clear, DPD back to 0 — but the cure rules are not yet met
    _backdate_first_installment(db, cid, -10)
    _run(client, as_of=date.today() + timedelta(days=2))
    body = _detail(client, cid)
    assert body["dpd"] == 0
    assert body["automated_stage"] == 3               # held
    assert "Cure not yet complete" in body["cure_note"]


# --------------------------------------------------------------------------- #
# 8. configuration versioning — past runs stay reproducible
# --------------------------------------------------------------------------- #
def test_config_change_activates_a_new_version_and_stamps_runs(client, set_ecl_config):
    active_contract(client, national_id="ECL-CFG")
    first = _run(client)
    assert first["ecl_config_version"] == 1

    set_ecl_config(lifetime_loss_rate=0.25, methodology="simplified_lifetime")
    second = _run(client, as_of=date.today() + timedelta(days=1))
    assert second["ecl_config_version"] == 2

    runs = {r["run_id"]: r for r in client.get("/ecl/runs").json()}
    assert runs[first["run_id"]]["ecl_config_version"] == 1
    assert runs[second["run_id"]]["ecl_config_version"] == 2

    cfg = client.get("/ecl/config").json()
    assert cfg["active"]["ecl_config_version"] == 2
    assert {v["version"] for v in cfg["versions"]} == {1, 2}


def test_config_update_endpoint_is_maker_checker(client, client_as, auth):
    active_contract(client, national_id="ECL-CFG-MC")

    # maker proposes
    r = client.put("/ecl/config", json={"changes": {"lifetime_loss_rate": 0.2}})
    assert r.status_code == 201, r.text
    approval_id = r.json()["id"]
    assert client.get("/ecl/config").json()["active"]["lifetime_loss_rate"] == pytest.approx(0.10)

    # same user cannot approve
    assert client.post(f"/approvals/{approval_id}/approve").status_code == 409

    # a different authorised user can
    checker = client_as("credit_manager")
    assert checker.post(f"/approvals/{approval_id}/approve").status_code == 200
    assert client.get("/ecl/config").json()["active"]["lifetime_loss_rate"] == pytest.approx(0.20)
    assert client.get("/ecl/config").json()["active"]["ecl_config_version"] == 2


# --------------------------------------------------------------------------- #
# 9. RBAC
# --------------------------------------------------------------------------- #
def test_ecl_module_is_role_gated(client, client_as):
    active_contract(client, national_id="ECL-5")

    for role in ("sales_employee", "collections_officer", "credit_officer"):
        c = client_as(role)
        assert c.get("/ecl/dashboard").status_code == 403
        assert c.get("/ecl/assessments").status_code == 403
        assert c.post("/ecl/run", json={}).status_code == 403

    for role in ("finance_officer", "credit_manager", "admin"):
        c = client_as(role)
        assert c.get("/ecl/dashboard").status_code == 200
        assert c.get("/ecl/assessments").status_code == 200
