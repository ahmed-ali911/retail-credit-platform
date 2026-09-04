"""Step 13 — per-category sub-reports, the Aging report, and PDF/Excel export."""
import csv
import io
import zipfile
from datetime import date, timedelta

import pytest

from app.models.contract import Installment
from tests.helpers import (
    active_contract,
    approved_application,
    created_contract,
    make_customer,
    make_product,
)


def _pay(client, cid, amount, ref):
    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": amount, "external_reference": ref},
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# A — customers
# --------------------------------------------------------------------------- #
def test_customers_by_risk_groups_correctly(client):
    make_customer(client, national_id="R-LOW", risk_score=800)
    make_customer(client, national_id="R-MED", risk_score=610)
    make_customer(client, national_id="R-HI", risk_score=400)
    make_customer(client, national_id="R-NONE", risk_score=None)

    r = client.get("/reports/customers/by-risk").json()
    assert r["thresholds"] == {"low_min": 650, "medium_min": 600}
    assert r["counts"] == {"low": 1, "medium": 1, "high": 1, "unscored": 1}
    assert {c["national_id"]: c["band"] for c in r["rows"]} == {
        "R-LOW": "low", "R-MED": "medium", "R-HI": "high", "R-NONE": "unscored",
    }


def test_customers_by_exposure_is_full_sorted_list(client):
    a = active_contract(client, national_id="EXP-A")  # ~981 outstanding
    b = active_contract(client, national_id="EXP-B")
    # pay down B a little so A > B
    _pay(client, b["contract_id"], b["schedule"][0]["total"], "EXP-B-P1")
    make_customer(client, national_id="EXP-NONE", risk_score=700)  # no exposure

    r = client.get("/reports/customers/by-exposure").json()
    assert r["total"] == 2  # only customers with exposure > 0
    amounts = [row["total_outstanding"] for row in r["rows"]]
    assert amounts == sorted(amounts, reverse=True)
    _ = a


# --------------------------------------------------------------------------- #
# B — products
# --------------------------------------------------------------------------- #
def test_products_by_availability(client):
    p_ok = make_product(client, name="InStock")
    p_out = make_product(client, name="OutStock", stock_quantity=0)

    r = client.get("/reports/products/by-availability").json()
    assert r["available"] == 1
    assert r["sold_out"] == 1
    states = {row["name"]: row["state"] for row in r["rows"]}
    assert states["InStock"] == "available"
    assert states["OutStock"] == "sold_out"
    _ = (p_ok, p_out)


def test_products_by_category_totals_stock(client):
    make_product(client, name="A")  # helper category = appliances, stock 10
    make_product(client, name="B", stock_quantity=3)

    r = client.get("/reports/products/by-category").json()
    appliances = next(g for g in r["rows"] if g["category"] == "appliances")
    assert appliances["products"] == 2
    assert appliances["stock_quantity"] == 13
    assert appliances["available_quantity"] == 13


# --------------------------------------------------------------------------- #
# C — contracts
# --------------------------------------------------------------------------- #
def test_contracts_by_status_and_channel(client):
    active_contract(client, national_id="ST-1")
    created_contract(client, national_id="ST-2")

    by_status = client.get("/reports/contracts/by-status").json()["counts"]
    assert by_status["active"] == 1
    assert by_status["created"] == 1

    by_channel = client.get("/reports/contracts/by-channel").json()["counts"]
    # the helper originates applications on the "branch" channel
    assert by_channel.get("branch") == 2


# --------------------------------------------------------------------------- #
# E — collections
# --------------------------------------------------------------------------- #
def test_collections_sub_reports(client, client_as):
    ctx = active_contract(client, national_id="COL-13")
    client.post("/jobs/assess-overdue", json={"as_of": "2027-06-01"})
    case_id = client.get("/collections/cases").json()[0]["id"]

    # a promise-to-pay activity
    client_as("collections_officer").post(
        f"/collections/cases/{case_id}/activities",
        json={
            "activity_type": "promise_to_pay",
            "notes": "will pay Friday",
            "promised_amount": 50,
            "promised_date": "2027-07-01",
        },
    )

    status = client.get("/reports/collections/status-summary").json()["counts"]
    assert status["open"] == 1

    promises = client.get(
        "/reports/collections/promise-performance"
    ).json()["counts"]
    assert promises["pending"] == 1
    assert promises["kept"] == 0

    fees = client.get("/reports/collections/late-fees-summary").json()
    assert fees["charged_count"] >= 1
    assert fees["charged_amount"] > 0
    assert fees["waived_count"] == 0
    _ = ctx


# --------------------------------------------------------------------------- #
# F — Aging (NEW)
# --------------------------------------------------------------------------- #
def test_aging_report_buckets_match_manual_grouping(client, db):
    """Three overdue installments at DPD 10, 45, 120 -> buckets 0, 1, 3."""
    ctx = active_contract(client, national_id="AGE-1")
    installments = (
        db.query(Installment)
        .filter(Installment.contract_id == ctx["contract_id"])
        .order_by(Installment.sequence_number)
        .all()
    )
    today = date.today()
    for inst, dpd in zip(installments[:3], (10, 45, 120)):
        inst.due_date = today - timedelta(days=dpd)
    db.commit()

    report = client.get("/reports/aging").json()
    buckets = {b["label"]: b for b in report["buckets"]}
    assert buckets["1-30"]["installment_count"] == 1
    assert buckets["31-60"]["installment_count"] == 1
    assert buckets["61-90"]["installment_count"] == 0
    assert buckets["91+"]["installment_count"] == 1
    for b in report["buckets"]:
        if b["installment_count"]:
            assert b["outstanding_amount"] > 0

    # drill-down: bucket 1 (31-60) returns only its one installment
    detail = client.get("/reports/aging?bucket=1").json()
    assert detail["label"] == "31-60"
    assert len(detail["items"]) == 1
    assert 31 <= detail["items"][0]["dpd"] <= 60
    assert detail["items"][0]["contract_id"] == ctx["contract_id"]

    # bucket 2 (61-90) is empty
    assert client.get("/reports/aging?bucket=2").json()["items"] == []
    # out-of-range bucket -> 422
    assert client.get("/reports/aging?bucket=9").status_code == 422


# --------------------------------------------------------------------------- #
# PDF / Excel / CSV export
# --------------------------------------------------------------------------- #
def test_all_three_export_formats_for_aging(client, db):
    ctx = active_contract(client, national_id="AGE-EXP")
    inst = (
        db.query(Installment)
        .filter(Installment.contract_id == ctx["contract_id"])
        .order_by(Installment.sequence_number)
        .first()
    )
    inst.due_date = date.today() - timedelta(days=20)
    db.commit()

    csv_resp = client.get("/reports/aging?format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(csv_resp.text))
    assert reader.fieldnames == [
        "bucket", "label", "installment_count", "outstanding_amount",
    ]
    rows = list(reader)
    # Step 15, Part A — one row per bucket (4) + one totals row
    assert len(rows) == 5
    assert rows[-1]["bucket"] == "TOTAL (4 rows)"

    xlsx_resp = client.get("/reports/aging?format=xlsx")
    assert xlsx_resp.status_code == 200
    assert "spreadsheetml" in xlsx_resp.headers["content-type"]
    zf = zipfile.ZipFile(io.BytesIO(xlsx_resp.content))
    assert "xl/workbook.xml" in zf.namelist()  # valid .xlsx (zip container)

    pdf_resp = client.get("/reports/aging?format=pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert pdf_resp.content[:5] == b"%PDF-"
    assert len(pdf_resp.content) > 500

    assert client.get("/reports/aging?format=bogus").status_code == 422


def test_csv_shape_on_more_endpoints(client):
    active_contract(client, national_id="CSVX-1")
    make_customer(client, national_id="CSVX-C", risk_score=720)

    for path, headers in (
        ("/reports/customers/by-risk?format=csv",
         ["customer_id", "name", "national_id", "risk_score", "band"]),
        ("/reports/contracts/by-status?format=csv", ["status", "contracts"]),
        ("/reports/collections/late-fees-summary?format=csv",
         ["kind", "count", "amount"]),
        ("/reports/profitability?format=csv",
         ["dimension", "key", "contracts", "contractual_profit",
          "recognized_profit", "unearned_profit"]),
    ):
        resp = client.get(path)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        reader = csv.DictReader(io.StringIO(resp.text))
        assert reader.fieldnames == headers
        assert len(list(reader)) >= 1


def test_directory_endpoints_gain_xlsx_pdf(client):
    make_customer(client, national_id="DIR-1", name="Xyla Dir")
    for fmt, marker in (("xlsx", b"PK"), ("pdf", b"%PDF-")):
        r = client.get(f"/customers?search=xyla&format={fmt}")
        assert r.status_code == 200
        assert r.content[: len(marker)] == marker
    assert client.get("/customers?search=x&format=bogus").status_code == 422


# --------------------------------------------------------------------------- #
# RBAC (consistent with every other report endpoint)
# --------------------------------------------------------------------------- #
def test_new_report_endpoints_are_role_gated(client_as):
    for path in (
        "/reports/customers/by-risk",
        "/reports/customers/by-exposure",
        "/reports/products/by-availability",
        "/reports/products/by-category",
        "/reports/contracts/by-status",
        "/reports/contracts/by-channel",
        "/reports/collections/status-summary",
        "/reports/collections/promise-performance",
        "/reports/collections/late-fees-summary",
        "/reports/aging",
    ):
        assert client_as("sales_employee").get(path).status_code == 403
        assert client_as("credit_manager").get(path).status_code == 200
