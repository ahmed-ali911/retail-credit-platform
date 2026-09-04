"""Step 11 — reporting layer (report endpoints + 5 dashboard summaries + CSV)."""
import csv
import io

import pytest

from tests.helpers import (
    active_contract,
    approved_application,
    make_application,
    make_customer,
    make_product,
)

APPROX = dict(abs=0.01)


def _pay(client, cid, amount, ref):
    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": amount, "external_reference": ref},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# A — /reports/contracts
# --------------------------------------------------------------------------- #
def test_contracts_report_filters_by_status(client):
    a = active_contract(client, national_id="RC-A")
    b = active_contract(client, national_id="RC-B")
    # close one via settlement so its status differs
    q = client.get(f"/contracts/{b['contract_id']}/settlement-quote").json()
    client.post(
        f"/contracts/{b['contract_id']}/settle",
        json={"amount": q["final_payoff_amount"], "external_reference": "RC-B-S"},
    )

    active = client.get("/reports/contracts?status=active").json()
    assert {r["contract_id"] for r in active["items"]} == {a["contract_id"]}
    closed = client.get("/reports/contracts?status=closed").json()
    assert {r["contract_id"] for r in closed["items"]} == {b["contract_id"]}
    allrows = client.get("/reports/contracts").json()
    assert allrows["total"] == 2


def test_contracts_report_filters_by_date_range(client):
    active_contract(client, national_id="RC-D1")
    # everything is created "today"
    future = client.get("/reports/contracts?date_from=2099-01-01").json()
    assert future["total"] == 0
    past = client.get("/reports/contracts?date_to=2000-01-01").json()
    assert past["total"] == 0
    now = client.get("/reports/contracts?date_from=2020-01-01").json()
    assert now["total"] == 1


def test_contracts_report_is_role_gated(client_as):
    assert client_as("sales_employee").get("/reports/contracts").status_code == 403
    assert client_as("finance_officer").get("/reports/contracts").status_code == 200


def test_contracts_report_csv_is_well_formed(client):
    active_contract(client, national_id="RC-CSV-1")
    active_contract(client, national_id="RC-CSV-2")

    resp = client.get("/reports/contracts?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    reader = csv.DictReader(io.StringIO(resp.text))
    assert reader.fieldnames == [
        "contract_id", "status", "customer_id", "customer_name",
        "product_id", "product_name", "category", "tenor_months",
        "installment_sale_price", "created_at",
        # Step 15, Part C — added so the Contracts Directory can reuse this
        # same query (outstanding total, next due date)
        "outstanding_total", "next_due_date",
    ]
    rows = list(reader)
    # Step 15, Part A — exports carry a totals row: 2 contracts + 1 totals row
    assert len(rows) == 3
    assert rows[-1]["contract_id"] == "TOTAL (2 rows)"


# --------------------------------------------------------------------------- #
# B — /reports/profitability
# --------------------------------------------------------------------------- #
def test_profitability_totals_reconcile_for_a_known_contract(client):
    ctx = active_contract(client, national_id="PROF-1")  # total_profit 81.00
    cid = ctx["contract_id"]

    # fresh contract: recognized 0, unearned == contractual
    p = client.get("/reports/profitability").json()
    assert p["total_contractual_profit"] == pytest.approx(81.0, **APPROX)
    assert p["total_recognized_profit"] == pytest.approx(0.0, **APPROX)
    assert p["total_unearned_profit"] == pytest.approx(81.0, **APPROX)
    assert p["total_recognized_profit"] + p["total_unearned_profit"] == pytest.approx(
        p["total_contractual_profit"], **APPROX
    )

    # pay the first installment -> some profit recognised; identity still holds
    first_total = ctx["schedule"][0]["total"]
    _pay(client, cid, first_total, "PROF-1-P1")
    p2 = client.get("/reports/profitability").json()
    assert p2["total_recognized_profit"] > 0
    assert p2["total_recognized_profit"] + p2["total_unearned_profit"] == pytest.approx(
        p2["total_contractual_profit"], **APPROX
    )
    # broken down by tenor (12) and category (appliances)
    assert "12" in p2["by_tenor"]
    assert "appliances" in p2["by_category"]
    assert p2["by_tenor"]["12"]["recognized_profit"] + p2["by_tenor"]["12"][
        "unearned_profit"
    ] == pytest.approx(p2["by_tenor"]["12"]["contractual_profit"], **APPROX)


# --------------------------------------------------------------------------- #
# D — five tab summaries
# --------------------------------------------------------------------------- #
def test_summary_executive_shape_and_values(client):
    active_contract(client, national_id="EX-1")
    active_contract(client, national_id="EX-2")

    s = client.get("/reports/summary/executive").json()
    assert set(s) == {
        "total_customers", "active_contracts", "total_outstanding_receivable",
        "total_profit_recognized", "approval_rate", "decisions_considered",
    }
    assert s["total_customers"] == 2
    assert s["active_contracts"] == 2
    assert s["total_outstanding_receivable"] == pytest.approx(981.0 * 2, abs=0.5)
    assert s["approval_rate"] == 1.0  # both applications approved


def test_summary_operations_counts_today(client):
    ctx = active_contract(client, national_id="OPS-1")
    _pay(client, ctx["contract_id"], 50, "OPS-1-P1")

    s = client.get("/reports/summary/operations").json()
    assert s["payments_today_count"] == 1
    assert s["payments_today_amount"] == pytest.approx(50.0, **APPROX)
    assert s["applications_submitted_today"] == 1
    assert s["overdue_installments"] == 0
    assert s["open_reconciliation_exceptions"] == 0


def test_summary_portfolio_status_and_dpd(client, db):
    active_contract(client, national_id="PF-1")
    ctx = active_contract(client, national_id="PF-2")

    s = client.get("/reports/summary/portfolio").json()
    assert s["contracts_by_status"]["active"] == 2
    assert s["average_contract_size"] == pytest.approx(1281.0, abs=0.5)
    dpd = s["dpd_distribution"]
    assert set(dpd["buckets"]) == {"1-30", "31-60", "61-90", "91+"}
    # fresh contracts: every due date is in the future -> all "current"
    assert dpd["current"] == 2
    assert sum(dpd["buckets"].values()) == 0

    # backdate PF-2's first installment ~45 days -> the "31-60" bucket
    from datetime import date, timedelta
    from app.models.contract import Installment

    inst = (
        db.query(Installment)
        .filter(Installment.contract_id == ctx["contract_id"])
        .order_by(Installment.sequence_number)
        .first()
    )
    inst.due_date = date.today() - timedelta(days=45)
    db.commit()

    dpd2 = client.get("/reports/summary/portfolio").json()["dpd_distribution"]
    assert dpd2["current"] == 1
    assert dpd2["buckets"]["31-60"] == 1


def test_summary_collections(client):
    ctx = active_contract(client, national_id="COL-1")
    client.post("/jobs/assess-overdue", json={"as_of": "2027-06-01"})
    cases = client.get("/collections/cases").json()
    assert cases, "expected a collection case to have been opened"

    s = client.get("/reports/summary/collections").json()
    assert s["open_cases"] == 1
    assert s["late_fees_charged_count"] >= 1
    assert s["late_fees_charged_amount"] > 0
    assert s["late_fees_waived_count"] == 0
    assert s["promise_to_pay_kept"] == 0
    assert s["promise_to_pay_broken"] == 0
    _ = ctx


def test_summary_credit_risk_bands_and_top_exposure(client):
    # low (>=650), medium (600-649), high (<600)
    make_customer(client, national_id="CR-LOW", risk_score=800)
    make_customer(client, national_id="CR-MED", risk_score=620)
    make_customer(client, national_id="CR-HIGH", risk_score=500)
    make_customer(client, national_id="CR-NONE", risk_score=None)
    # one customer with real exposure (approved_application seeds risk_score 700)
    ctx = active_contract(client, national_id="CR-EXP")

    s = client.get("/reports/summary/credit-risk").json()
    bands = s["customers_by_risk_band"]
    assert bands["low"] == 2  # CR-LOW + CR-EXP
    assert bands["medium"] == 1
    assert bands["high"] == 1
    assert bands["unscored"] == 1
    assert s["risk_band_thresholds"] == {"low_min": 650, "medium_min": 600}
    top = s["top_customers_by_exposure"]
    assert len(top) == 1
    assert top[0]["total_outstanding"] > 0
    _ = ctx


def test_summary_endpoints_are_role_gated(client_as):
    for path in (
        "/reports/summary/executive",
        "/reports/summary/operations",
        "/reports/summary/portfolio",
        "/reports/summary/collections",
        "/reports/summary/credit-risk",
    ):
        assert client_as("sales_employee").get(path).status_code == 403
        assert client_as("credit_manager").get(path).status_code == 200


# --------------------------------------------------------------------------- #
# C — CSV export on the existing directory screens
# --------------------------------------------------------------------------- #
def test_customer_directory_csv_export(client):
    make_customer(client, national_id="CD-1", name="Zed Alpha")
    make_customer(client, national_id="CD-2", name="Zed Beta")

    resp = client.get("/customers?search=zed&format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    assert reader.fieldnames == ["id", "name", "national_id", "status", "risk_score"]
    assert len(list(reader)) == 2


def test_product_directory_csv_export(client):
    make_product(client, name="CSV Fridge")
    resp = client.get("/products?search=csv&format=csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    assert "available_quantity" in reader.fieldnames
    assert len(list(reader)) == 1


def test_collections_case_csv_export_and_date_filter(client):
    active_contract(client, national_id="CC-CSV")
    client.post("/jobs/assess-overdue", json={"as_of": "2027-06-01"})

    resp = client.get("/collections/cases?format=csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    assert reader.fieldnames == [
        "id", "contract_id", "status", "opened_at", "opened_reason", "closed_at",
    ]
    assert len(list(reader)) == 1

    # opened "today" -> a future date_from excludes it
    empty = client.get("/collections/cases?date_from=2099-01-01").json()
    assert empty == []
