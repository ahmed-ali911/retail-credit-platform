"""P0-1 — immutable financial ledger, dual-write phase.

The proof that the dual-write is complete and correct: for the payment and
settlement scenarios already covered elsewhere, summing the relevant
`LedgerEntry` rows reproduces exactly the figures the existing (unchanged)
balance calculations report. If an assertion here fails, the ledger write has a
bug — fix the write, not the test.
"""
from decimal import Decimal

from app.models.contract import InstallmentContract
from app.models.ledger import LedgerEntry, LedgerEntryType, LedgerRelatedAction
from tests.helpers import active_contract

D = Decimal


def _ledger_sum(db, contract_id, *entry_types):
    wanted = set(entry_types)
    rows = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.contract_id == contract_id)
        .all()
    )
    return sum((r.amount for r in rows if r.entry_type in wanted), D("0"))


def _existing_paid_totals(db, contract_id):
    """The figures the current (unchanged) code already tracks in place."""
    contract = db.get(InstallmentContract, contract_id)
    principal_paid = sum(
        (D(str(i.principal_paid)) for i in contract.installments), D("0")
    )
    profit_paid = sum(
        (D(str(i.profit_paid)) for i in contract.installments), D("0")
    )
    late_fee_paid = sum(
        (D(str(c.amount_paid)) for c in contract.late_fee_charges), D("0")
    )
    return principal_paid, profit_paid, late_fee_paid


def _assert_reconciled(db, contract_id):
    principal_paid, profit_paid, late_fee_paid = _existing_paid_totals(db, contract_id)

    assert _ledger_sum(db, contract_id, LedgerEntryType.principal_paid) == principal_paid
    # profit "paid" on the installments == profit recognised in cash + profit rebated
    assert (
        _ledger_sum(
            db,
            contract_id,
            LedgerEntryType.profit_recognized,
            LedgerEntryType.profit_rebated,
        )
        == profit_paid
    )
    assert _ledger_sum(db, contract_id, LedgerEntryType.late_fee_paid) == late_fee_paid


# --------------------------------------------------------------------------- #
# Scenario 1 — full repayment
# --------------------------------------------------------------------------- #
def test_ledger_reconciles_full_repayment(client, db):
    ctx = active_contract(client, national_id="LG-FULL")
    cid = ctx["contract_id"]
    total = round(sum(line["total"] for line in ctx["schedule"]), 2)

    r = client.post(f"/contracts/{cid}/payments",
                    json={"amount": total, "external_reference": "LG-FULL-PAY"})
    assert r.status_code == 200, r.text

    _assert_reconciled(db, cid)
    assert client.get(f"/contracts/{cid}/receivable").json()["outstanding_receivable"] == 0.0
    # nothing rebated on a straight repayment
    assert _ledger_sum(db, cid, LedgerEntryType.profit_rebated) == D("0")


# --------------------------------------------------------------------------- #
# Scenario 2 — delinquency, late fee, then repayment
# --------------------------------------------------------------------------- #
def test_ledger_reconciles_delinquency_then_repayment(client, db):
    ctx = active_contract(client, national_id="LG-DELINQ")
    cid = ctx["contract_id"]

    client.post("/jobs/assess-overdue", json={"as_of": "2026-10-15"})  # late fee on inst 1
    charge = client.get(f"/contracts/{cid}").json()["late_fee_charges"][0]

    pay = round(ctx["schedule"][0]["total"] + charge["amount"], 2)
    r = client.post(f"/contracts/{cid}/payments",
                    json={"amount": pay, "external_reference": "LG-DELINQ-PAY"})
    assert r.status_code == 200, r.text

    _assert_reconciled(db, cid)
    assert _ledger_sum(db, cid, LedgerEntryType.late_fee_paid) == D(str(charge["amount"]))


# --------------------------------------------------------------------------- #
# Scenario 3 — early settlement (the profit-rebate case from the assessment)
# --------------------------------------------------------------------------- #
def test_ledger_reconciles_early_settlement_with_rebate(client, db):
    ctx = active_contract(client, national_id="LG-SETTLE")
    cid = ctx["contract_id"]

    # a partial payment first so the settlement has a real remaining balance
    client.post(f"/contracts/{cid}/payments",
                json={"amount": ctx["schedule"][0]["total"], "external_reference": "LG-SETTLE-P1"})

    quote = client.get(f"/contracts/{cid}/settlement-quote").json()
    assert quote["profit_rebate_amount"] > 0

    s = client.post(f"/contracts/{cid}/settle",
                    json={"amount": quote["final_payoff_amount"],
                          "external_reference": "LG-SETTLE-SET"})
    assert s.status_code == 200, s.text

    _assert_reconciled(db, cid)

    # the rebated profit is explicitly and separately reconstructable (S-4)
    assert _ledger_sum(db, cid, LedgerEntryType.profit_rebated) == D(
        str(quote["profit_rebate_amount"])
    )
    # recognised + rebated == the full scheduled profit for the contract
    contract = db.get(InstallmentContract, cid)
    scheduled_profit = sum(
        (D(str(i.profit_component)) for i in contract.installments), D("0")
    )
    assert (
        _ledger_sum(db, cid, LedgerEntryType.profit_recognized, LedgerEntryType.profit_rebated)
        == scheduled_profit
    )


# --------------------------------------------------------------------------- #
# The read paths are explicitly NOT cut over yet
# --------------------------------------------------------------------------- #
def test_receivable_still_computed_the_old_way_not_from_ledger(client, db):
    ctx = active_contract(client, national_id="LG-READ")
    cid = ctx["contract_id"]
    before = client.get(f"/contracts/{cid}/receivable").json()

    # write a bogus ledger entry directly; the receivable view must ignore it
    db.add(
        LedgerEntry(
            contract_id=cid,
            entry_type=LedgerEntryType.principal_paid,
            amount=D("99999.99"),
            related_action=LedgerRelatedAction.payment,
            reference_type="payment",
            reference_id=0,
        )
    )
    db.commit()

    after = client.get(f"/contracts/{cid}/receivable").json()
    assert after == before  # unchanged — reads do not consult the ledger
