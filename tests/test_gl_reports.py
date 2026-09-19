"""Chart of Accounts — Checkpoint 3: accounts/mappings API, journal read
endpoints, posting retry, and the five GL reports (JSON + CSV/XLSX/PDF
export, reusing reports.py's infrastructure).
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta
from decimal import Decimal

from app.models.gl import GLJournal, JournalStatus
from app.services import coa as coa_service
from tests.helpers import active_contract, first_due_date


def _assess_overdue(client, as_of):
    r = client.post("/jobs/assess-overdue", json={"as_of": as_of.isoformat()})
    assert r.status_code == 200, r.text


def _run_ecl(client, as_of=None, post=False):
    body = {"post": post}
    if as_of is not None:
        body["as_of"] = as_of.isoformat()
    r = client.post("/ecl/run", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Accounts API
# --------------------------------------------------------------------------- #
def test_list_accounts_returns_the_eighteen_seeded_demo_accounts(client):
    r = client.get("/gl/accounts")
    assert r.status_code == 200, r.text
    assert len(r.json()) == 18


def test_accounts_endpoint_rbac(client_as):
    assert client_as("sales_employee").get("/gl/accounts").status_code == 403
    assert client_as("finance_officer").get("/gl/accounts").status_code == 200
    assert client_as("collections_officer").get("/gl/accounts").status_code == 403


def test_propose_and_approve_account_create_via_api(client, client_as):
    r = client.post("/gl/accounts/propose-create", json={
        "account_code": "8000", "account_name": "API Test Account",
        "account_type": "ASSET", "normal_balance": "DEBIT",
    })
    assert r.status_code == 201, r.text
    ok = client_as("credit_manager").post(f"/approvals/{r.json()['id']}/approve")
    assert ok.status_code == 200, ok.text

    accounts = client.get("/gl/accounts").json()
    codes = {a["account_code"] for a in accounts}
    assert "8000" in codes


def test_propose_account_create_rejects_a_role_outside_finance_officer_admin(client_as):
    r = client_as("credit_manager").post("/gl/accounts/propose-create", json={
        "account_code": "8001", "account_name": "Should Be Blocked",
        "account_type": "ASSET", "normal_balance": "DEBIT",
    })
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Mappings API
# --------------------------------------------------------------------------- #
def test_list_active_mappings_returns_all_confirmed_event_types(client):
    r = client.get("/gl/mappings")
    assert r.status_code == 200, r.text
    assert len(r.json()) == 23


def test_mapping_version_history_via_api(client, client_as, db):
    bank = coa_service.get_account_by_code(db, "1000")
    income = coa_service.get_account_by_code(db, "4300")
    r = client_as("finance_officer").post("/gl/mappings/propose-change", json={
        "account_event_type": "recovery_received",
        "classification": "POSTABLE",
        "lines": [
            {"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"},
            {"posting_side": "CREDIT", "account_id": income.id, "amount_source": "absolute_event_amount"},
        ],
    })
    assert r.status_code == 201, r.text
    ok = client.post(f"/approvals/{r.json()['id']}/approve")  # default client == admin
    assert ok.status_code == 200, ok.text

    versions = client.get("/gl/mappings/recovery_received/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["lines"][1]["amount_source"] == "absolute_event_amount"


# --------------------------------------------------------------------------- #
# Journals API
# --------------------------------------------------------------------------- #
def test_journal_list_and_detail_via_api(client, db):
    ctx = active_contract(client, national_id="GLR-1")
    cid = ctx["contract_id"]

    listed = client.get("/gl/journals", params={"contract_id": cid}).json()
    assert len(listed) >= 2  # contract_activated + down_payment_received (+ ecl day-one)

    detail = client.get(f"/gl/journals/{listed[0]['id']}").json()
    assert detail["journal_status"] == "READY"
    assert len(detail["lines"]) >= 2
    assert detail["event"]["contract_id"] == cid


def test_journal_lookup_by_accounting_event_id_matches_the_reference_suffix(client, db):
    ctx = active_contract(client, national_id="GLR-BY-EVENT")
    cid = ctx["contract_id"]
    listed = client.get("/gl/journals", params={"contract_id": cid}).json()
    journal = listed[0]
    event_id = int(journal["journal_reference"].removeprefix("GLJ-"))
    assert event_id == journal["accounting_event_id"]

    r = client.get(f"/gl/journals/by-event/{event_id}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == journal["id"]


def test_retry_endpoint_rejects_a_journal_that_never_balanced(client, db):
    ctx = active_contract(client, national_id="GLR-2")
    journal = db.query(GLJournal).filter(GLJournal.journal_status == JournalStatus.ready).first()
    assert journal is not None
    r = client.post(f"/gl/journals/{journal.id}/retry")
    assert r.status_code == 409  # it's READY, not a failed posting attempt


def test_retry_endpoint_successfully_reposts_a_genuine_posting_failure(client, db):
    ctx = active_contract(client, national_id="GLR-3")
    journal = db.query(GLJournal).filter(JournalStatus.ready == GLJournal.journal_status).first()
    assert journal is not None
    # simulate what a REAL (non-mock) provider rejection would leave behind:
    # a journal that WAS balanced (is_balanced stays True) but the posting
    # attempt itself failed.
    journal.journal_status = JournalStatus.failed
    journal.error_message = "simulated provider rejection"
    db.commit()

    r = client.post(f"/gl/journals/{journal.id}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["journal_status"] == "POSTED"
    assert r.json()["external_gl_reference"].startswith("MOCK-GL-")


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def test_chart_of_accounts_report_json_and_csv(client):
    j = client.get("/gl/reports/chart-of-accounts").json()
    assert j["totals"]["row_count"] == 18

    csv_resp = client.get("/gl/reports/chart-of-accounts", params={"format": "csv"})
    assert csv_resp.status_code == 200
    rows = list(csv.reader(io.StringIO(csv_resp.text)))
    # header + 18 data rows + 1 totals row == 20
    assert len(rows) == 20


def test_mapping_register_report_has_one_row_per_line_and_seed_summary_only_types_present(client):
    j = client.get("/gl/reports/mappings").json()
    event_types_seen = {r["event_type"] for r in j["rows"]}
    assert "ecl_provision_movement" in event_types_seen
    assert "recovery_adjustment" in event_types_seen
    summary_row = next(r for r in j["rows"] if r["event_type"] == "ecl_provision_movement")
    assert summary_row["classification"] == "SUMMARY_ONLY"
    assert summary_row["posting_side"] == ""


def test_general_ledger_running_balance_is_correct(client, db):
    ctx = active_contract(client, national_id="GLR-LEDGER")  # cash 1200, DP 300
    cid = ctx["contract_id"]
    bank = coa_service.get_account_by_code(db, "1000")

    j = client.get("/gl/reports/ledger", params={"account_id": bank.id, "contract_id": cid}).json()
    assert j["rows"], "expected at least the down-payment line against Bank"
    running = 0.0
    for row in j["rows"]:
        running += row["debit"] - row["credit"]
        assert row["running_balance"] == running
    assert j["closing_balance"] == running


def test_general_ledger_requires_an_account_id(client):
    r = client.get("/gl/reports/ledger")
    assert r.status_code == 422


def test_trial_balance_totals_and_zero_difference_when_everything_resolves(client):
    active_contract(client, national_id="GLR-TB-1")
    active_contract(client, national_id="GLR-TB-2")

    j = client.get("/gl/reports/trial-balance").json()
    assert j["total_debits"] == j["total_credits"]
    assert j["difference"] == 0.0
    assert j["unmapped_event_count"] == 0
    assert j["unmapped_event_total"] == 0.0


def test_trial_balance_surfaces_unmapped_and_failed_counts_separately(client, client_as, db, auth):
    maker = auth["users"]["finance_officer"].id
    from datetime import datetime, timezone

    from app.models.contract import InstallmentContract
    from app.models.accounting import AccountingEventType
    from app.services import accounting as accounting_service

    ctx = active_contract(client, national_id="GLR-TB-UNMAPPED")
    cid = ctx["contract_id"]
    contract = db.get(InstallmentContract, cid)
    long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc)
    accounting_service.emit(
        db, event_type=AccountingEventType.late_fee_waived,
        event_reference="late-fee-waived-TB-TEST",
        contract=contract, amount=Decimal("5.00"), event_date=long_ago,
    )
    db.commit()

    j = client.get("/gl/reports/trial-balance").json()
    assert j["unmapped_event_count"] >= 1
    assert j["unmapped_event_total"] >= 5.0
    # a balanced trial balance never silently counts the unmapped event as posted
    assert j["total_debits"] == j["total_credits"]


def test_unmapped_failed_report_lists_the_unmapped_event_with_a_reason(client, db):
    from datetime import datetime, timezone

    from app.models.contract import InstallmentContract
    from app.models.accounting import AccountingEventType
    from app.services import accounting as accounting_service

    ctx = active_contract(client, national_id="GLR-UF-1")
    cid = ctx["contract_id"]
    contract = db.get(InstallmentContract, cid)
    long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc)
    accounting_service.emit(
        db, event_type=AccountingEventType.late_fee_waived,
        event_reference="late-fee-waived-UF-TEST",
        contract=contract, amount=Decimal("5.00"), event_date=long_ago,
    )
    db.commit()

    j = client.get("/gl/reports/unmapped-failed").json()
    row = next(r for r in j["rows"] if r["event_reference"] == "late-fee-waived-UF-TEST")
    assert row["journal_status"] == "UNMAPPED"
    assert row["reason"]
    assert row["retry_eligible"] is False  # generation failure, not a posting failure


def test_report_export_row_count_matches_on_screen_totals(client):
    j = client.get("/gl/reports/mappings").json()
    on_screen_rows = j["totals"]["row_count"]

    csv_resp = client.get("/gl/reports/mappings", params={"format": "csv"})
    csv_rows = list(csv.reader(io.StringIO(csv_resp.text)))
    # header + N data rows + 1 totals row
    assert len(csv_rows) - 2 == on_screen_rows

    xlsx_resp = client.get("/gl/reports/mappings", params={"format": "xlsx"})
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    pdf_resp = client.get("/gl/reports/mappings", params={"format": "pdf"})
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"


def test_reports_rbac(client_as):
    assert client_as("sales_employee").get("/gl/reports/chart-of-accounts").status_code == 403
    assert client_as("credit_manager").get("/gl/reports/chart-of-accounts").status_code == 200
