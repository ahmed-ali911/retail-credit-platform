"""Step 15 — totals/counts on reports, richer detail data, Contracts Directory
query support, and Excel bank-statement upload."""
from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from tests.helpers import active_contract, created_contract, make_customer, make_product


def _pay(client, cid, amount, ref):
    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": amount, "external_reference": ref},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Part A — totals on reports
# --------------------------------------------------------------------------- #
def test_products_by_availability_totals_match_manual_sum(client):
    make_product(client, name="TotA1", stock_quantity=10)
    make_product(client, name="TotA2", stock_quantity=3)

    r = client.get("/reports/products/by-availability").json()
    manual_stock = sum(row["stock_quantity"] for row in r["rows"])
    manual_available = sum(row["available_quantity"] for row in r["rows"])
    assert r["totals"]["row_count"] == len(r["rows"])
    assert r["totals"]["stock_quantity"] == manual_stock
    assert r["totals"]["available_quantity"] == manual_available

    # the same totals reach the CSV export as an appended row
    csv_resp = client.get("/reports/products/by-availability?format=csv")
    lines = csv_resp.text.strip().splitlines()
    assert lines[-1].split(",")[0].startswith("TOTAL (")


def test_contracts_report_totals_match_manual_sum(client):
    a = active_contract(client, national_id="TOT-A")
    b = active_contract(client, national_id="TOT-B")

    r = client.get("/reports/contracts").json()
    manual = sum(row["installment_sale_price"] for row in r["items"])
    assert r["totals"]["row_count"] == 2
    assert r["totals"]["installment_sale_price"] == pytest.approx(manual, abs=0.01)
    assert manual == pytest.approx(
        a["offer"]["installment_sale_price"] + b["offer"]["installment_sale_price"],
        abs=0.01,
    )

    # totals reflect the FULL filtered set, not just one page
    r2 = client.get("/reports/contracts?limit=1").json()
    assert len(r2["items"]) == 1
    assert r2["totals"]["row_count"] == 2
    assert r2["totals"]["installment_sale_price"] == pytest.approx(manual, abs=0.01)


def test_customers_by_exposure_totals_are_over_the_full_list_not_the_page(client):
    a = active_contract(client, national_id="EXPTOT-A")
    b = active_contract(client, national_id="EXPTOT-B")
    _pay(client, b["contract_id"], b["schedule"][0]["total"], "EXPTOT-B-P1")

    r = client.get("/reports/customers/by-exposure").json()
    manual = sum(row["total_outstanding"] for row in r["rows"])
    assert r["total_outstanding_sum"] == pytest.approx(manual, abs=0.01)
    _ = (a, b)


def test_aging_report_totals_match_manual_sum(client, db):
    from datetime import date, timedelta

    from app.models.contract import Installment

    ctx = active_contract(client, national_id="AGETOT-1")
    inst = (
        db.query(Installment)
        .filter(Installment.contract_id == ctx["contract_id"])
        .order_by(Installment.sequence_number)
        .first()
    )
    inst.due_date = date.today() - timedelta(days=20)
    db.commit()

    r = client.get("/reports/aging").json()
    manual_count = sum(row["installment_count"] for row in r["rows"])
    manual_amount = sum(row["outstanding_amount"] for row in r["rows"])
    assert r["totals"]["installment_count"] == manual_count
    assert r["totals"]["outstanding_amount"] == pytest.approx(manual_amount, abs=0.01)
    assert manual_count == 1


# --------------------------------------------------------------------------- #
# Part B — Origination section data (finance_officer can now see it)
# --------------------------------------------------------------------------- #
def test_finance_officer_can_view_the_originating_application(client, client_as):
    ctx = active_contract(client, national_id="ORIG-1")
    app_id = ctx["application"]["id"]

    r = client_as("finance_officer").get(f"/applications/{app_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "branch"
    assert body["created_by"]


def test_contract_history_reuses_reports_contracts_customer_filter(client):
    """Part B's Customer detail 'full history' section reuses GET
    /reports/contracts?customer_id=X — including closed contracts, unlike the
    exposure endpoint which only lists outstanding balances."""
    ctx = created_contract(client, national_id="HIST-1", down_payment_amount=300)
    customer_id = ctx["customer"]["id"]
    cid = ctx["contract_id"]
    client.post(f"/contracts/{cid}/cancel")

    history = client.get(f"/reports/contracts?customer_id={customer_id}").json()
    assert history["total"] == 1
    assert history["items"][0]["status"] == "closed"

    exposure = client.get(f"/customers/{customer_id}/exposure").json()
    assert exposure["contracts"] == []  # closed -> no outstanding balance


# --------------------------------------------------------------------------- #
# Part C — Contracts Directory query support (contract_id filter)
# --------------------------------------------------------------------------- #
def test_reports_contracts_filters_by_contract_id(client):
    a = active_contract(client, national_id="DIRQ-A")
    b = active_contract(client, national_id="DIRQ-B")

    r = client.get(f"/reports/contracts?contract_id={a['contract_id']}").json()
    assert r["total"] == 1
    assert r["items"][0]["contract_id"] == a["contract_id"]
    _ = b


def test_reports_contracts_unknown_contract_id_returns_empty(client):
    r = client.get("/reports/contracts?contract_id=999999").json()
    assert r["total"] == 0
    assert r["items"] == []


# --------------------------------------------------------------------------- #
# Part E — Excel bank-statement upload
# --------------------------------------------------------------------------- #
def _xlsx_bytes(header: list[str], rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, content: bytes, filename: str = "statement.xlsx"):
    return client.post(
        "/reconciliation/bank-lines/upload",
        files={
            "file": (
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def test_excel_upload_ingests_and_matches_exactly_like_the_single_line_path(client, db):
    from app.models.payment import Payment, PaymentReconciliationStatus

    ctx = active_contract(client, national_id="XLS-1")
    amount = ctx["schedule"][0]["total"]
    pay = _pay(client, ctx["contract_id"], amount, "XLS-BANKREF-1")["payment"]

    content = _xlsx_bytes(
        ["bank_reference", "amount", "value_date"],
        [["XLS-BANKREF-1", amount, "2027-01-01"]],
    )
    r = _upload(client, content)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_processed"] == 1
    assert body["rows_ingested"] == 1
    assert body["rows_rejected"] == 0
    assert body["rejected"] == []
    # the exact same matching the single-line "Run matching" path uses
    assert body["matched"] == 1
    assert body["exceptions_created"] == 0

    assert (
        db.get(Payment, pay["id"]).reconciliation_status
        == PaymentReconciliationStatus.reconciled
    )


def test_excel_upload_column_order_is_flexible_and_case_insensitive(client):
    ctx = active_contract(client, national_id="XLS-2")
    amount = ctx["schedule"][0]["total"]
    _pay(client, ctx["contract_id"], amount, "XLS-BANKREF-2")

    # reordered + upper-cased header, plus an extra ignored column
    content = _xlsx_bytes(
        ["VALUE_DATE", "Bank_Reference", "Notes", "AMOUNT"],
        [["2027-01-01", "XLS-BANKREF-2", "wire transfer", amount]],
    )
    r = _upload(client, content)
    assert r.status_code == 200, r.text
    assert r.json()["rows_ingested"] == 1


def test_excel_upload_missing_required_column_rejects_the_whole_file(client):
    content = _xlsx_bytes(
        ["bank_reference", "amount"],  # value_date missing
        [["REF-1", 100]],
    )
    r = _upload(client, content)
    assert r.status_code == 422
    assert "value_date" in r.json()["detail"]

    # nothing was ingested
    status = client.get("/reconciliation/status").json()
    assert status["unmatched_bank_lines"] == 0


def test_excel_upload_bad_row_is_reported_not_silently_skipped(client):
    content = _xlsx_bytes(
        ["bank_reference", "amount", "value_date"],
        [
            ["GOOD-1", 50, "2027-01-01"],
            ["", 50, "2027-01-01"],  # missing bank_reference
            ["GOOD-2", "not-a-number", "2027-01-01"],  # bad amount
            ["GOOD-3", 50, "not-a-date"],  # bad date
        ],
    )
    r = _upload(client, content)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_processed"] == 4
    assert body["rows_ingested"] == 1
    assert body["rows_rejected"] == 3
    reasons = " ".join(row["reason"] for row in body["rejected"])
    assert "bank_reference" in reasons
    assert "amount" in reasons
    assert "value_date" in reasons
    # every rejected row is reported with its actual spreadsheet row number
    assert {row["row"] for row in body["rejected"]} == {3, 4, 5}


def test_excel_upload_rejects_non_xlsx_filename(client):
    r = _upload(client, b"not really a workbook", filename="statement.csv")
    assert r.status_code == 422


def test_excel_upload_is_role_gated(client_as):
    content = _xlsx_bytes(["bank_reference", "amount", "value_date"], [])
    files = {
        "file": (
            "statement.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    assert (
        client_as("sales_employee")
        .post("/reconciliation/bank-lines/upload", files=files)
        .status_code
        == 403
    )
    assert (
        client_as("finance_officer")
        .post("/reconciliation/bank-lines/upload", files=files)
        .status_code
        == 200
    )
