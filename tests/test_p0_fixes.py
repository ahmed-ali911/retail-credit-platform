"""P0 bug fixes — contract auto-close, overpayment rejection, return profit
reporting. All three were confirmed live (with screenshots) before this fix
and re-verified against a rebuilt running instance afterwards — see the
commit message; this file is the pytest-side regression coverage."""
import pytest

from app.models.closure import ClosureReason
from tests.helpers import active_contract

APPROX = dict(abs=0.01)


def _receivable(client, cid):
    r = client.get(f"/contracts/{cid}/receivable")
    assert r.status_code == 200, r.text
    return r.json()


def _pay(client, cid, amount, ref):
    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": amount, "external_reference": ref},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _pay_out_schedule(client, cid, schedule):
    """Pay every installment in full, on time (Sheet 2 of the scenario
    workbook: 12 on-time installments)."""
    last = None
    for i, row in enumerate(schedule):
        last = _pay(client, cid, row["total"], f"SHEET2-{cid}-{i}")
    return last


# --------------------------------------------------------------------------- #
# Bug 1 (root cause) — normal full repayment now auto-closes the contract
# --------------------------------------------------------------------------- #
def test_final_on_time_installment_auto_closes_the_contract(client):
    ctx = active_contract(client, national_id="P0-1")
    cid = ctx["contract_id"]

    result = _pay_out_schedule(client, cid, ctx["schedule"])

    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "closed"
    assert contract["closure"] is not None
    assert contract["closure"]["reason"] == "normal"
    assert contract["closure"]["financial_adjustment"] == pytest.approx(0.0, **APPROX)

    rec = _receivable(client, cid)
    assert rec["outstanding_receivable"] == pytest.approx(0.0, **APPROX)
    assert rec["outstanding_late_fees"] == pytest.approx(0.0, **APPROX)
    assert rec["total_installments_remaining"] == 0
    assert result is not None


def test_auto_close_creates_exactly_one_closure_reused_from_the_closure_table(client, db):
    from app.models.closure import ContractClosure

    ctx = active_contract(client, national_id="P0-1B")
    cid = ctx["contract_id"]
    _pay_out_schedule(client, cid, ctx["schedule"])

    closures = db.query(ContractClosure).filter_by(contract_id=cid).all()
    assert len(closures) == 1
    assert closures[0].reason == ClosureReason.normal
    assert closures[0].financial_adjustment == 0


def test_auto_close_fires_the_same_accounting_event_hook_as_other_closures(client, db):
    from app.models.accounting import AccountingEvent, AccountingEventType

    ctx = active_contract(client, national_id="P0-1C")
    cid = ctx["contract_id"]
    _pay_out_schedule(client, cid, ctx["schedule"])

    events = (
        db.query(AccountingEvent)
        .filter_by(contract_id=cid, event_type=AccountingEventType.contract_closed)
        .all()
    )
    assert len(events) == 1
    assert events[0].amount == 0


def test_auto_close_is_idempotent_and_never_double_closes(client, db):
    """Paying an already-closed contract again is rejected (409, contract
    inactive) rather than ever attempting a second closure."""
    from app.models.closure import ContractClosure

    ctx = active_contract(client, national_id="P0-1D")
    cid = ctx["contract_id"]
    _pay_out_schedule(client, cid, ctx["schedule"])

    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": 10, "external_reference": "AFTER-CLOSE"},
    )
    assert r.status_code == 409

    closures = db.query(ContractClosure).filter_by(contract_id=cid).all()
    assert len(closures) == 1


def test_partial_repayment_does_not_auto_close(client):
    """This must only fire on the payment that actually completes the
    schedule — not retroactively, and not early."""
    ctx = active_contract(client, national_id="P0-1E")
    cid = ctx["contract_id"]
    first_total = ctx["schedule"][0]["total"]
    _pay(client, cid, first_total, "P0-1E-P1")

    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "active"
    assert contract["closure"] is None


# --------------------------------------------------------------------------- #
# Once auto-closed, Return / settlement-quote correctly disappear (Step 9 rule)
# --------------------------------------------------------------------------- #
def test_auto_closed_contract_cannot_be_returned_settled_or_cancelled(client):
    ctx = active_contract(client, national_id="P0-2")
    cid = ctx["contract_id"]
    _pay_out_schedule(client, cid, ctx["schedule"])

    assert client.get(f"/contracts/{cid}/settlement-quote").status_code == 409
    assert client.post(f"/contracts/{cid}/return").status_code == 409
    assert client.post(f"/contracts/{cid}/cancel").status_code == 409


# --------------------------------------------------------------------------- #
# Bug 2 — a payment larger than current outstanding is rejected
# --------------------------------------------------------------------------- #
def test_payment_greater_than_outstanding_is_rejected_with_actual_amount(client):
    ctx = active_contract(client, national_id="P0-3")
    cid = ctx["contract_id"]
    rec = _receivable(client, cid)
    total_outstanding = rec["outstanding_receivable"] + rec["outstanding_late_fees"]

    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": total_outstanding + 1.00, "external_reference": "TOO-BIG"},
    )
    assert r.status_code == 422, r.text
    assert f"{total_outstanding:.2f}" in r.json()["detail"]

    # nothing allocated
    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "active"
    assert client.get(f"/contracts/{cid}/receivable").json() == rec


def test_payment_exactly_equal_to_outstanding_succeeds_and_closes(client):
    """Boundary case: == is accepted (only > is rejected), and since this is
    the whole remaining balance it also exercises Bug 1's auto-close."""
    ctx = active_contract(client, national_id="P0-4")
    cid = ctx["contract_id"]

    # Pay off every installment but the last one first, individually.
    for i, row in enumerate(ctx["schedule"][:-1]):
        _pay(client, cid, row["total"], f"P0-4-{i}")

    rec = _receivable(client, cid)
    total_outstanding = rec["outstanding_receivable"] + rec["outstanding_late_fees"]
    assert total_outstanding > 0

    r = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": total_outstanding, "external_reference": "P0-4-LAST"},
    )
    assert r.status_code == 200, r.text

    contract = client.get(f"/contracts/{cid}").json()
    assert contract["status"] == "closed"
    assert contract["closure"]["reason"] == "normal"
    final = _receivable(client, cid)
    assert final["outstanding_receivable"] == pytest.approx(0.0, **APPROX)


# --------------------------------------------------------------------------- #
# Bug 3 — profitability report reflects a return correctly
# --------------------------------------------------------------------------- #
def test_profitability_report_stops_accruing_profit_after_a_return(client):
    ctx = active_contract(client, national_id="CU-000012-STYLE")
    cid = ctx["contract_id"]
    first_profit = ctx["schedule"][0]["profit_component"]

    # some genuine profit is recognised before the return
    _pay(client, cid, ctx["schedule"][0]["total"], "P0-5-P1")

    p_before_return = client.get("/reports/profitability").json()
    assert p_before_return["total_contractual_profit"] == pytest.approx(81.0, **APPROX)

    r = client.post(f"/contracts/{cid}/return")
    assert r.status_code == 200, r.text

    p = client.get("/reports/profitability").json()
    # the return is not excluded outright (unlike cancellation)...
    assert p["contracts_counted"] == 1
    # ...but it no longer contributes the full contractual 81 — only what was
    # genuinely recognised before the return date.
    assert p["total_contractual_profit"] == pytest.approx(float(first_profit), **APPROX)
    assert p["total_recognized_profit"] == pytest.approx(float(first_profit), **APPROX)
    assert p["total_unearned_profit"] == pytest.approx(0.0, **APPROX)
    # the identity still holds
    assert p["total_recognized_profit"] + p["total_unearned_profit"] == pytest.approx(
        p["total_contractual_profit"], **APPROX
    )


def test_cancellation_still_excluded_outright_unlike_return(client):
    """Confirms the fix reuses, rather than replaces, the existing
    cancellation-exclusion condition."""
    from tests.helpers import created_contract

    ctx = created_contract(client, national_id="P0-6", down_payment_amount=300)
    cid = ctx["contract_id"]
    client.post(f"/contracts/{cid}/cancel")

    p = client.get("/reports/profitability").json()
    assert p["contracts_counted"] == 0
    assert p["total_contractual_profit"] == pytest.approx(0.0, **APPROX)


def test_return_with_no_prior_payment_contributes_nothing(client):
    """A same-day return before any installment was ever paid: recognized and
    contractual both collapse to zero — no phantom profit either way."""
    ctx = active_contract(client, national_id="P0-7")
    cid = ctx["contract_id"]

    r = client.post(f"/contracts/{cid}/return")
    assert r.status_code == 200, r.text

    p = client.get("/reports/profitability").json()
    assert p["contracts_counted"] == 1
    assert p["total_contractual_profit"] == pytest.approx(0.0, **APPROX)
    assert p["total_recognized_profit"] == pytest.approx(0.0, **APPROX)
    assert p["total_unearned_profit"] == pytest.approx(0.0, **APPROX)
