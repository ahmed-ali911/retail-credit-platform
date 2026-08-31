"""P0-3 — affordability correctness (fixes assessment finding S-2).

Part 1: the application-time estimate now uses the Pricing Engine's tenor->rate
        table + the configured minimum down payment (not `requested_amount / tenor`).
Part 2: the real peak installment is re-tested against `max_dbr` at offer
        generation; on failure the offer is blocked (or warned) per config.
"""
from app.models.credit_application import AssessmentResult, AssessmentSource
from app.services import config_service as cfg
from tests.helpers import (
    approved_application,
    make_application,
    make_customer,
    make_product,
)


def _submit(client, customer_id, product_id, *, amount, tenor):
    app = make_application(client, customer_id, product_id,
                           requested_amount=amount, requested_tenor_months=tenor)
    return client.post(f"/applications/{app['id']}/submit").json()


# --------------------------------------------------------------------------- #
# Part 1 — the rate table (not a flat proxy) drives the initial estimate
# --------------------------------------------------------------------------- #
def test_changing_the_rate_table_changes_the_estimate(client, set_config):
    """The old proxy was `requested_amount * factor / tenor` — rate-independent.
    If the estimate now moves when only the rate table moves, the table drives it."""
    customer = make_customer(client, national_id="AF-RT", monthly_income=8000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)

    first = _submit(client, customer["id"], product["id"], amount=1200, tenor=12)
    est_a = first["latest_assessment"]["estimated_installment"]
    assert first["latest_assessment"]["config_snapshot"]["tenor_profit_rate"] == 0.09

    set_config(cfg.KEY_TENOR_PROFIT_RATE_TABLE,
               {"6": 0.04, "12": 0.50, "18": 0.135, "24": 0.18, "36": 0.30})

    second = _submit(client, customer["id"], product["id"], amount=1200, tenor=12)
    est_b = second["latest_assessment"]["estimated_installment"]

    assert est_b != est_a
    assert est_b > est_a  # 50% profit rate > 9%
    assert second["latest_assessment"]["config_snapshot"]["tenor_profit_rate"] == 0.5


def test_different_tenors_give_different_estimates_and_can_flip_the_outcome(client, set_config):
    set_config(cfg.KEY_MAX_DBR, 0.03)
    customer = make_customer(client, national_id="AF-TEN", monthly_income=2000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)

    short = _submit(client, customer["id"], product["id"], amount=1200, tenor=12)
    long = _submit(client, customer["id"], product["id"], amount=1200, tenor=24)

    assert short["latest_assessment"]["estimated_installment"] != \
        long["latest_assessment"]["estimated_installment"]
    # 12-month burden is higher -> breaches the (tightened) DBR ceiling
    assert short["status"] == "referred"
    assert long["status"] == "approved"


def test_estimate_falls_back_to_flat_factor_for_an_unpriceable_tenor(client):
    customer = make_customer(client, national_id="AF-FB", monthly_income=8000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client)
    body = _submit(client, customer["id"], product["id"], amount=1200, tenor=9)  # 9 not in the table

    snap = body["latest_assessment"]["config_snapshot"]
    assert snap["installment_estimate_method"] == "flat_factor"
    # old proxy: 1200 * 1.0 / 9
    assert body["latest_assessment"]["estimated_installment"] == round(1200 / 9, 2)


# --------------------------------------------------------------------------- #
# Part 2 — offer-time re-check against the real peak installment
# --------------------------------------------------------------------------- #
def _rechecks(db, application_id):
    return (
        db.query(AssessmentResult)
        .filter_by(application_id=application_id,
                   source=AssessmentSource.offer_affordability_recheck)
        .all()
    )


def test_offer_proceeds_and_records_a_passing_recheck_when_affordable(client, db):
    ctx = approved_application(client, national_id="AF-OK")
    app_id = ctx["application"]["id"]

    resp = client.post(f"/applications/{app_id}/offer", json={"down_payment_amount": 300})
    assert resp.status_code == 201, resp.text

    rc = _rechecks(db, app_id)
    assert len(rc) == 1
    assert rc[0].decision == "pass"
    assert rc[0].config_snapshot["outcome"] == "pass"


def test_offer_blocked_when_small_down_payment_breaches_the_real_dbr(client, db, set_config):
    # tight ceiling: initial estimate passes, real front-loaded peak does not
    set_config(cfg.KEY_MAX_DBR, 0.24)
    customer = make_customer(client, national_id="AF-BLOCK", monthly_income=1000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client, cash_price=3000)
    app = _submit(client, customer["id"], product["id"], amount=3000, tenor=12)
    assert app["status"] == "approved"  # passed the initial (estimate) check

    resp = client.post(f"/applications/{app['id']}/offer",
                       json={"down_payment_amount": 450})  # the 15% minimum
    assert resp.status_code == 422, resp.text
    assert "debt-burden" in resp.json()["detail"].lower()

    # no offer created, but the failed re-check is on record
    rc = _rechecks(db, app["id"])
    assert len(rc) == 1
    assert rc[0].decision == "fail"
    assert rc[0].debt_burden_ratio > 0.24


def test_warn_only_lets_the_unaffordable_offer_through_but_still_records_the_failure(
    client, db, set_config
):
    set_config(cfg.KEY_MAX_DBR, 0.24)
    set_config(cfg.KEY_OFFER_AFFORDABILITY_GATE_MODE, "warn_only")
    customer = make_customer(client, national_id="AF-WARN", monthly_income=1000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client, cash_price=3000)
    app = _submit(client, customer["id"], product["id"], amount=3000, tenor=12)

    resp = client.post(f"/applications/{app['id']}/offer",
                       json={"down_payment_amount": 450})
    assert resp.status_code == 201, resp.text  # offer proceeds

    rc = _rechecks(db, app["id"])
    assert len(rc) == 1
    assert rc[0].decision == "fail"
    assert rc[0].config_snapshot[cfg.KEY_OFFER_AFFORDABILITY_GATE_MODE] == "warn_only"


def test_blocked_offer_writes_an_audit_event(client, db, set_config):
    from app.models.audit import AuditEvent

    set_config(cfg.KEY_MAX_DBR, 0.24)
    customer = make_customer(client, national_id="AF-AUD", monthly_income=1000,
                             existing_obligations=0, risk_score=700)
    product = make_product(client, cash_price=3000)
    app = _submit(client, customer["id"], product["id"], amount=3000, tenor=12)
    client.post(f"/applications/{app['id']}/offer", json={"down_payment_amount": 450})

    evs = db.query(AuditEvent).filter_by(
        action="offer.blocked_unaffordable", entity_id=str(app["id"])).all()
    assert len(evs) == 1
