"""P0-5 — bank reconciliation (fixes assessment finding S-5).

Reconciliation only *observes*: it never blocks or alters a payment, an
allocation or a closure. These tests lock in the matching rules, idempotency,
and the maker-checker manual-resolution path.
"""
from datetime import date, timedelta

from app.models.payment import Payment, PaymentReconciliationStatus
from app.services import config_service as cfg
from tests.helpers import active_contract

TODAY = date.today().isoformat()


def _pay(client, contract_id, amount, ref):
    res = client.post(
        f"/contracts/{contract_id}/payments",
        json={"amount": amount, "external_reference": ref},
    )
    assert res.status_code == 200, res.text
    return res.json()["payment"]


def _first_total(ctx):
    return ctx["schedule"][0]["total"]


def _recon_status(db, payment_id):
    return db.get(Payment, payment_id).reconciliation_status


# --------------------------------------------------------------------------- #
# matching rules
# --------------------------------------------------------------------------- #
def test_exact_reference_auto_reconciles(client, db):
    ctx = active_contract(client, national_id="REC-1")
    cid = ctx["contract_id"]
    amount = _first_total(ctx)
    payment = _pay(client, cid, amount, "BANKREF-1")

    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "BANKREF-1", "amount": amount, "value_date": TODAY},
    )
    run = client.post("/reconciliation/run")
    assert run.status_code == 200
    assert run.json() == {
        "lines_processed": 1,
        "matched": 1,
        "exceptions_created": 0,
    }

    assert _recon_status(db, payment["id"]) == PaymentReconciliationStatus.reconciled


def test_fallback_amount_and_date_reconciles(client, db):
    ctx = active_contract(client, national_id="REC-2")
    cid = ctx["contract_id"]
    amount = _first_total(ctx)
    payment = _pay(client, cid, amount, "MERCHANT-REF-2")
    # derive the line's value_date from the payment's actual (UTC) received date
    # so the test doesn't straddle the local/UTC midnight boundary
    pay_date = payment["received_at"][:10]

    # bank_reference does NOT match any payment reference -> falls through to
    # the amount + value-date rule (placeholder tolerance: same calendar day).
    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "UNRELATED-9", "amount": amount, "value_date": pay_date},
    )
    run = client.post("/reconciliation/run").json()
    assert run == {"lines_processed": 1, "matched": 1, "exceptions_created": 0}

    assert _recon_status(db, payment["id"]) == PaymentReconciliationStatus.reconciled


def test_no_match_creates_open_exception(client):
    ctx = active_contract(client, national_id="REC-3")
    cid = ctx["contract_id"]
    _pay(client, cid, _first_total(ctx), "REF-3")

    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "NOPE", "amount": 12345.67, "value_date": TODAY},
    )
    run = client.post("/reconciliation/run").json()
    assert run == {"lines_processed": 1, "matched": 0, "exceptions_created": 1}

    exc = client.get("/reconciliation/exceptions").json()
    assert len(exc) == 1
    assert exc[0]["reason"] == "no_match"
    assert exc[0]["status"] == "open"


def test_amount_mismatch_flags_payment_as_exception(client, db):
    ctx = active_contract(client, national_id="REC-4")
    cid = ctx["contract_id"]
    amount = _first_total(ctx)
    payment = _pay(client, cid, amount, "REF-4")

    client.post(
        "/reconciliation/bank-lines",
        json={
            "bank_reference": "REF-4",
            "amount": round(amount + 10, 2),
            "value_date": TODAY,
        },
    )
    run = client.post("/reconciliation/run").json()
    assert run == {"lines_processed": 1, "matched": 0, "exceptions_created": 1}

    exc = client.get("/reconciliation/exceptions").json()
    assert exc[0]["reason"] == "amount_mismatch"

    assert _recon_status(db, payment["id"]) == PaymentReconciliationStatus.exception


# --------------------------------------------------------------------------- #
# idempotency
# --------------------------------------------------------------------------- #
def test_running_twice_does_not_double_match_or_duplicate_exceptions(client):
    ctx = active_contract(client, national_id="REC-5")
    cid = ctx["contract_id"]
    amount = _first_total(ctx)
    _pay(client, cid, amount, "REF-5A")

    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "REF-5A", "amount": amount, "value_date": TODAY},
    )
    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "GHOST", "amount": 99.99, "value_date": TODAY},
    )

    first = client.post("/reconciliation/run").json()
    assert first == {"lines_processed": 2, "matched": 1, "exceptions_created": 1}

    second = client.post("/reconciliation/run").json()
    assert second == {"lines_processed": 0, "matched": 0, "exceptions_created": 0}

    assert len(client.get("/reconciliation/exceptions").json()) == 1
    status = client.get("/reconciliation/status").json()
    assert status["reconciled_payments"] == 1
    assert status["open_exceptions"] == 1


# --------------------------------------------------------------------------- #
# manual resolution via the generic maker-checker
# --------------------------------------------------------------------------- #
def _open_exception_for(client, cid, amount):
    """Create one payment + one no-ref-match bank line -> one open exception."""
    payment = _pay(client, cid, amount, "M-REF")
    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "MANUAL-ONLY", "amount": 777.0, "value_date": TODAY},
    )
    client.post("/reconciliation/run")
    exc = client.get("/reconciliation/exceptions").json()[0]
    return payment, exc


def test_manual_match_requires_a_different_approver(client, client_as):
    ctx = active_contract(client, national_id="REC-6")
    cid = ctx["contract_id"]
    payment, exc = _open_exception_for(client, cid, _first_total(ctx))

    fo = client_as("finance_officer")
    req = fo.post(
        f"/reconciliation/exceptions/{exc['id']}/request-match",
        json={"payment_id": payment["id"], "reason": "verified by ops"},
    )
    assert req.status_code == 201
    approval_id = req.json()["id"]

    # same requester cannot approve
    assert fo.post(f"/approvals/{approval_id}/approve").status_code == 409

    # exception still open, payment still not reconciled
    assert client.get("/reconciliation/exceptions").json()[0]["status"] == "open"


def test_manual_match_approved_by_second_user_reconciles_and_closes(
    client, client_as, db
):
    ctx = active_contract(client, national_id="REC-7")
    cid = ctx["contract_id"]
    payment, exc = _open_exception_for(client, cid, _first_total(ctx))

    req = client_as("finance_officer").post(
        f"/reconciliation/exceptions/{exc['id']}/request-match",
        json={"payment_id": payment["id"], "reason": "ops confirmed"},
    )
    approval_id = req.json()["id"]

    ok = client_as("credit_manager").post(f"/approvals/{approval_id}/approve")
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"

    exc_after = client.get("/reconciliation/exceptions?status=resolved").json()
    assert len(exc_after) == 1
    assert exc_after[0]["id"] == exc["id"]
    assert exc_after[0]["resolved_by"] is not None

    assert _recon_status(db, payment["id"]) == PaymentReconciliationStatus.reconciled


def test_pending_request_blocks_a_second_one(client, client_as):
    ctx = active_contract(client, national_id="REC-8")
    cid = ctx["contract_id"]
    payment, exc = _open_exception_for(client, cid, _first_total(ctx))

    fo = client_as("finance_officer")
    body = {"payment_id": payment["id"], "reason": "x"}
    assert fo.post(
        f"/reconciliation/exceptions/{exc['id']}/request-match", json=body
    ).status_code == 201
    assert fo.post(
        f"/reconciliation/exceptions/{exc['id']}/request-match", json=body
    ).status_code == 409


# --------------------------------------------------------------------------- #
# reporting surfaces
# --------------------------------------------------------------------------- #
def test_status_counts_a_mixed_scenario(client):
    ctx = active_contract(client, national_id="REC-9")
    cid = ctx["contract_id"]
    a0 = ctx["schedule"][0]["total"]
    a1 = ctx["schedule"][1]["total"]
    _pay(client, cid, a0, "S-MATCH")
    _pay(client, cid, a1, "S-OTHER")

    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "S-MATCH", "amount": a0, "value_date": TODAY},
    )
    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "S-GHOST", "amount": 0.01, "value_date": TODAY},
    )
    client.post("/reconciliation/run")

    status = client.get("/reconciliation/status").json()
    assert status["reconciled_payments"] == 1
    assert status["unreconciled_payments"] == 1
    assert status["open_exceptions"] == 1
    assert status["unmatched_bank_lines"] == 1


def test_receivable_gains_reconciliation_summary(client):
    ctx = active_contract(client, national_id="REC-10")
    cid = ctx["contract_id"]
    amount = _first_total(ctx)
    _pay(client, cid, amount, "RS-1")

    rec = client.get(f"/contracts/{cid}/receivable").json()
    assert rec["reconciliation_summary"] == {
        "unreconciled": 1,
        "reconciled": 0,
        "exception": 0,
    }
    # existing figures unchanged
    assert "outstanding_receivable" in rec

    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "RS-1", "amount": amount, "value_date": TODAY},
    )
    client.post("/reconciliation/run")
    rec2 = client.get(f"/contracts/{cid}/receivable").json()
    assert rec2["reconciliation_summary"]["reconciled"] == 1


def test_gateway_reference_is_matched_when_set(client, db):
    ctx = active_contract(client, national_id="REC-11")
    cid = ctx["contract_id"]
    amount = _first_total(ctx)
    payment = _pay(client, cid, amount, "MERCHANT-KEY")

    row = db.get(Payment, payment["id"])
    row.gateway_reference = "GATEWAY-TXN-77"
    db.commit()

    client.post(
        "/reconciliation/bank-lines",
        json={
            "bank_reference": "GATEWAY-TXN-77",
            "amount": amount,
            "value_date": TODAY,
        },
    )
    run = client.post("/reconciliation/run").json()
    assert run["matched"] == 1
    assert (
        db.get(Payment, payment["id"]).reconciliation_status
        == PaymentReconciliationStatus.reconciled
    )


def test_date_tolerance_window_is_configurable(client, set_config):
    ctx = active_contract(client, national_id="REC-12")
    cid = ctx["contract_id"]
    amount = _first_total(ctx)
    payment = _pay(client, cid, amount, "MERCH-ONLY")

    # one day before the payment's actual (UTC) received date
    pay_date = date.fromisoformat(payment["received_at"][:10])
    yesterday = (pay_date - timedelta(days=1)).isoformat()
    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "X", "amount": amount, "value_date": yesterday},
    )

    # default tolerance 0 -> no fallback match, one exception
    assert client.post("/reconciliation/run").json()["exceptions_created"] == 1

    set_config(cfg.KEY_RECON_DATE_TOLERANCE_DAYS, 3)
    # widen and re-run against a fresh line for the same payment
    client.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "Y", "amount": amount, "value_date": yesterday},
    )
    assert client.post("/reconciliation/run").json()["matched"] == 1


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #
def test_rbac_on_reconciliation_endpoints(client_as):
    sales = client_as("sales_employee")
    assert sales.post(
        "/reconciliation/bank-lines",
        json={"bank_reference": "R", "amount": 1.0, "value_date": TODAY},
    ).status_code == 403
    assert sales.post("/reconciliation/run").status_code == 403
    assert sales.get("/reconciliation/exceptions").status_code == 403
    assert sales.get("/reconciliation/status").status_code == 403
