"""ECL & provision — first slice.

The framing these tests lock in (per the external accounting review):
  * ECL is assessed from contract activation, not from delinquency;
  * the calculation path is a config switch (`ecl_methodology`), not hard-coded;
  * under the 3-stage path, DPD is a rebuttable presumption — not the sole
    determinant of a stage change;
  * PD/LGD render as an explicit "n/a", never 0 or a fabricated number;
  * the module is invisible to sales_employee / collections_officer;
  * one ECL run emits exactly one portfolio provision-movement accounting event.
"""
from datetime import date, timedelta

import pytest

from app.models.contract import Installment
from app.services import config_service as cfg
from tests.helpers import active_contract, first_due_date


def _run(client, as_of=None):
    body = {}
    if as_of is not None:
        body["as_of"] = as_of if isinstance(as_of, str) else as_of.isoformat()
    r = client.post("/ecl/run", json=body)
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


# --------------------------------------------------------------------------- #
# 1. ECL exists from day one, not from delinquency
# --------------------------------------------------------------------------- #
def test_newly_activated_contract_has_nonzero_ecl_under_default_methodology(client):
    ctx = active_contract(client, national_id="ECL-1")
    cid = ctx["contract_id"]

    detail = client.get(f"/ecl/contracts/{cid}")
    assert detail.status_code == 200, detail.text
    body = detail.json()

    assert body["methodology"] == "dpd_banded"
    assert body["dpd"] == 0
    assert body["dpd_bucket"] == "current"
    # a current (0 DPD) contract still carries a real, non-zero provision
    assert body["ead"] > 0
    assert body["ecl_amount"] is not None
    assert body["ecl_amount"] == pytest.approx(round(body["ead"] * 0.005, 2), abs=0.01)
    assert body["stage"] is None  # no staging under Path C


def test_dashboard_reports_day_one_provision_and_coverage(client):
    active_contract(client, national_id="ECL-DASH")
    d = client.get("/ecl/dashboard").json()
    assert d["active_methodology"] == "dpd_banded"
    assert d["total_ead"] > 0
    assert d["ecl_balance"] > 0
    assert d["contracts_assessed"] == 1
    assert d["ecl_coverage_pct"] == pytest.approx(0.005, abs=0.0005)
    assert d["stage_exposure"] == "n/a"  # not the 3-stage path


# --------------------------------------------------------------------------- #
# 2. methodology is a config switch, no code change
# --------------------------------------------------------------------------- #
def test_switching_to_simplified_lifetime_changes_basis_via_config(client, set_config):
    ctx = active_contract(client, national_id="ECL-2")
    cid = ctx["contract_id"]

    set_config(cfg.KEY_ECL_METHODOLOGY, "simplified_lifetime")
    _run(client)

    body = client.get(f"/ecl/contracts/{cid}").json()
    assert body["methodology"] == "simplified_lifetime"
    assert body["dpd_bucket"] is None  # no banding under Path A
    assert body["loss_rate"] == pytest.approx(0.10)
    assert body["ecl_amount"] == pytest.approx(round(body["ead"] * 0.10, 2), abs=0.01)


# --------------------------------------------------------------------------- #
# 3. three_stage — DPD alone is NOT the sole determinant
# --------------------------------------------------------------------------- #
def test_three_stage_dpd_alone_is_not_sufficient_for_stage_change(
    client, db, set_config
):
    with_signal = active_contract(client, national_id="ECL-3A")
    no_signal = active_contract(client, national_id="ECL-3B")

    # Only `with_signal` is overdue when the collections job runs, so only it
    # gets an open case (the corroborating risk signal)...
    _backdate_first_installment(db, with_signal["contract_id"], 45)
    as_of = first_due_date(client, with_signal["contract_id"]) + timedelta(days=45)
    client.post("/jobs/assess-overdue", json={"as_of": as_of.isoformat()})

    # ...then push `no_signal` past the same DPD threshold, but with no case /
    # broken promise to corroborate it.
    _backdate_first_installment(db, no_signal["contract_id"], 45)

    set_config(cfg.KEY_ECL_METHODOLOGY, "three_stage")
    _run(client)

    staged = client.get(f"/ecl/contracts/{with_signal['contract_id']}").json()
    rebutted = client.get(f"/ecl/contracts/{no_signal['contract_id']}").json()

    assert staged["dpd"] >= 30 and rebutted["dpd"] >= 30
    # the one with an open case is staged up; the one without is not — proving
    # DPD is a rebuttable presumption, not the sole determinant
    assert staged["stage"] == 2
    assert rebutted["stage"] == 1
    assert staged["stage"] != rebutted["stage"]
    assert "corroborated" in staged["stage_reason"]
    assert "rebutted" in rebutted["stage_reason"]


# --------------------------------------------------------------------------- #
# 4. PD / LGD / ECL render as explicit "n/a", never 0 or fabricated
# --------------------------------------------------------------------------- #
def test_pd_lgd_ecl_are_na_not_zero_under_three_stage(client, set_config):
    ctx = active_contract(client, national_id="ECL-4")
    cid = ctx["contract_id"]
    set_config(cfg.KEY_ECL_METHODOLOGY, "three_stage")
    _run(client)

    body = client.get(f"/ecl/contracts/{cid}").json()
    assert body["pd"] is None
    assert body["lgd"] is None
    assert body["ecl_amount"] is None
    assert body["pd_lgd_note"] == "n/a — no PD/LGD source configured"
    assert body["ecl_note"] == "n/a — no PD/LGD source configured"

    dash = client.get("/ecl/dashboard").json()
    assert dash["pd_lgd_note"] == "n/a — no PD/LGD source configured"
    assert dash["ecl_not_computable_count"] == 1
    # stage exposure IS meaningful under Path B (staging is live)
    assert isinstance(dash["stage_exposure"], dict)


# --------------------------------------------------------------------------- #
# 5. RBAC — invisible to sales_employee / collections_officer
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


# --------------------------------------------------------------------------- #
# 6. one run -> exactly one portfolio provision-movement accounting event
# --------------------------------------------------------------------------- #
def test_ecl_run_emits_exactly_one_accounting_event(client):
    active_contract(client, national_id="ECL-6A")
    active_contract(client, national_id="ECL-6B")

    summary = _run(client)
    assert summary["accounting_event_id"] is not None

    events = client.get(
        "/accounting/events", params={"event_type": "ecl_provision_movement"}
    ).json()
    assert len(events) == 1
    assert events[0]["id"] == summary["accounting_event_id"]
    assert events[0]["contract_id"] is None  # portfolio-level, not one contract
    assert events[0]["amount"] == pytest.approx(
        summary["total_provision_movement"], abs=0.01
    )

    # re-running for a later date does not duplicate the first run's event
    _run(client, as_of=date.today() + timedelta(days=1))
    events2 = client.get(
        "/accounting/events", params={"event_type": "ecl_provision_movement"}
    ).json()
    assert len(events2) == 2  # one per run, still exactly one each


def test_provision_movement_is_measured_against_the_prior_period(client, db):
    ctx = active_contract(client, national_id="ECL-7")
    cid = ctx["contract_id"]

    first = _run(client)  # baseline established
    # nothing changed -> a second run the next day shows ~no movement
    second = _run(client, as_of=date.today() + timedelta(days=1))
    assert second["total_provision_movement"] == pytest.approx(0.0, abs=0.01)

    # now push the contract overdue and re-run -> provision increases
    _backdate_first_installment(db, cid, 40)
    third = _run(client, as_of=date.today() + timedelta(days=2))
    assert third["total_provision_movement"] > 0
    _ = first


# --------------------------------------------------------------------------- #
# 7. unknown methodology raises rather than silently mis-provisioning
# --------------------------------------------------------------------------- #
def test_unknown_methodology_is_rejected(client, set_config):
    active_contract(client, national_id="ECL-8")
    set_config(cfg.KEY_ECL_METHODOLOGY, "made_up_model")
    r = client.post("/ecl/run", json={})
    assert r.status_code == 422
    assert "made_up_model" in r.text
