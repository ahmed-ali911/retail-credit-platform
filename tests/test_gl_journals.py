"""Chart of Accounts — Checkpoint 2: journal generation and posting
behaviour. Checkpoint 1's domain model (accounts, versioned mappings,
maker-checker) is covered in test_chart_of_accounts.py; this file proves
that a real `AccountingEvent`, emitted by an existing business flow, turns
into a correct, balanced `GLJournal` — or is left explainably UNMAPPED/
FAILED, never silently wrong.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.accounting import AccountingEvent, AccountingEventType, AccountingStatus
from app.models.gl import EventAccountMapping, GLJournal, JournalStatus, PostingSide
from app.services import coa as coa_service
from tests.helpers import active_contract, first_due_date

APPROX = dict(abs=0.005)


def _assess_overdue(client, as_of):
    r = client.post("/jobs/assess-overdue", json={"as_of": as_of.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _run_ecl(client, as_of=None, post=False):
    body = {"post": post}
    if as_of is not None:
        body["as_of"] = as_of.isoformat()
    r = client.post("/ecl/run", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _make_delinquent(client, national_id: str, days_past_due: int):
    ctx = active_contract(client, national_id=national_id)
    cid = ctx["contract_id"]
    as_of = first_due_date(client, cid) + timedelta(days=days_past_due)
    _assess_overdue(client, as_of)
    _run_ecl(client, as_of)
    return ctx, cid, as_of


def _event_by_ref(db, event_reference: str) -> AccountingEvent:
    return db.execute(
        select(AccountingEvent).where(AccountingEvent.event_reference == event_reference)
    ).scalar_one()


def _journal_for(db, event: AccountingEvent) -> GLJournal | None:
    return db.execute(
        select(GLJournal).where(GLJournal.accounting_event_id == event.id)
    ).scalar_one_or_none()


def _open_case_id(client, cid) -> int:
    cases = client.get("/collections/cases", params={"contract_id": cid, "status": "open"}).json()
    assert cases, "expected an open collection case"
    return cases[0]["id"]


def _log_activity(client, case_id):
    r = client.post(f"/collections/cases/{case_id}/activities", json={
        "activity_type": "call", "notes": "attempted contact",
    })
    assert r.status_code == 201, r.text


def _request_and_approve_write_off(client, client_as, cid) -> int:
    r = client.post(f"/write-offs/contracts/{cid}/requests", json={
        "write_off_type": "FULL",
        "reason_code": "COLLECTIONS_EXHAUSTED",
        "justification": "Exhausted all reasonable collection efforts.",
    })
    assert r.status_code == 201, r.text
    wo_id = int(r.json()["entity_id"])
    ok = client_as("credit_manager").post(f"/approvals/{r.json()['id']}/approve")
    assert ok.status_code == 200, ok.text
    return wo_id


# --------------------------------------------------------------------------- #
# Simple mapped event -> balanced journal
# --------------------------------------------------------------------------- #
def test_simple_mapped_event_creates_a_balanced_journal(client, db):
    ctx = active_contract(client, national_id="GLJ-SIMPLE")
    cid = ctx["contract_id"]
    amount = ctx["schedule"][0]["total"]

    r = client.post(f"/contracts/{cid}/payments", json={"amount": amount, "external_reference": "GLJ-SIMPLE-PAY"})
    assert r.status_code == 200, r.text
    payment_id = r.json()["payment"]["id"]

    event = _event_by_ref(db, f"payment-received-{payment_id}")
    journal = _journal_for(db, event)
    assert journal is not None
    assert journal.journal_status == JournalStatus.ready
    assert journal.is_balanced is True
    assert journal.total_debit == journal.total_credit == Decimal(str(round(amount, 2)))

    lines = list(journal.lines)
    assert len(lines) == 2
    sides = {l.posting_side for l in lines}
    assert sides == {PostingSide.debit, PostingSide.credit}
    for l in lines:
        assert l.amount > 0  # never a negative debit/credit line
        assert l.account_code_snapshot
        assert l.account_name_snapshot


# --------------------------------------------------------------------------- #
# Compound mapped event -> multiple balanced lines
# --------------------------------------------------------------------------- #
def test_compound_mapped_event_creates_multiple_balanced_lines(client, db):
    ctx = active_contract(client, national_id="GLJ-COMPOUND")  # cash 1200, DP 300, profit 81
    cid = ctx["contract_id"]

    event = _event_by_ref(db, f"contract-activated-{cid}")
    journal = _journal_for(db, event)
    assert journal is not None
    assert journal.journal_status == JournalStatus.ready

    lines = list(journal.lines)
    assert len(lines) == 4  # down payment, gross receivable / cash price, unearned profit
    debit_total = sum(l.amount for l in lines if l.posting_side == PostingSide.debit)
    credit_total = sum(l.amount for l in lines if l.posting_side == PostingSide.credit)
    assert debit_total == credit_total == journal.total_debit == journal.total_credit
    for l in lines:
        assert l.amount > 0


# --------------------------------------------------------------------------- #
# Sign handling
# --------------------------------------------------------------------------- #
def test_ecl_provision_release_reverses_sides_on_a_negative_source_amount(client, client_as, db):
    """A write-off's own ecl_provision_released event always carries a
    negative amount (amount=-provision_snapshot) — the seeded mapping's
    reverse_on_negative=True must flip Dr Impairment Expense / Cr Allowance
    into Dr Allowance / Cr Impairment Expense, never post a negative line."""
    ctx, cid, as_of = _make_delinquent(client, "GLJ-RELEASE", 200)
    case_id = _open_case_id(client, cid)
    _log_activity(client, case_id)

    wo_id = _request_and_approve_write_off(client, client_as, cid)
    ex = client.post(f"/write-offs/requests/{wo_id}/execute")
    assert ex.status_code == 200, ex.text
    execution_id = ex.json()["execution"]["id"]

    event = _event_by_ref(db, f"ecl-writeoff-release-{execution_id}")
    assert event.amount < 0  # confirmed-negative source, per the Checkpoint 0 audit
    journal = _journal_for(db, event)
    assert journal is not None
    assert journal.journal_status == JournalStatus.ready

    lines = {l.account_code_snapshot: l for l in journal.lines}
    assert lines["2300"].posting_side == PostingSide.debit    # ECL Allowance — debited to release it
    assert lines["5100"].posting_side == PostingSide.credit   # ECL/Impairment Expense — credited (reversal)
    for l in journal.lines:
        assert l.amount > 0  # abs() applied, never a negative line


def test_unsupported_negative_amount_produces_an_explainable_failed_journal(client, db):
    ctx = active_contract(client, national_id="GLJ-UNSUPPORTED-NEG")
    cid = ctx["contract_id"]
    event = _event_by_ref(db, f"contract-activated-{cid}")
    mapping = db.execute(
        select(EventAccountMapping).where(
            EventAccountMapping.account_event_type == AccountingEventType.contract_activated,
            EventAccountMapping.is_active.is_(True),
        )
    ).scalar_one()
    line = mapping.lines[0]
    assert line.reverse_on_negative is False  # confirmed by the seed — never expected to go negative

    # simulate a resolver that legitimately returns a negative value for a
    # line the approved mapping never authorised to reverse
    from app.services import gl_journal as gl_journal_service
    original = gl_journal_service._RESOLVERS[line.amount_source]
    gl_journal_service._RESOLVERS[line.amount_source] = lambda db_, ev: Decimal("-5.00")
    try:
        # delete the journal this event already generated at emit() time, and regenerate under the patched resolver
        existing = db.execute(select(GLJournal).where(GLJournal.accounting_event_id == event.id)).scalar_one()
        db.delete(existing)
        db.flush()
        journal = gl_journal_service.generate_journal(db, event)
    finally:
        gl_journal_service._RESOLVERS[line.amount_source] = original

    assert journal.journal_status == JournalStatus.failed
    assert "negative" in journal.error_message.lower()
    assert list(journal.lines) == []


# --------------------------------------------------------------------------- #
# Unmapped / summary-only / reserved
# --------------------------------------------------------------------------- #
def test_unmapped_event_is_preserved_and_visible_as_unmapped(client, db):
    """No mapping is effective for an event dated before the seed's own
    effective_from (always "today" — see coa_service.seed_demo_data) —
    the simplest genuinely-unmapped scenario, exercised directly through
    accounting.emit() (a real business flow would reach the same state via
    a mapping that was deactivated and never replaced)."""
    from datetime import datetime, timedelta, timezone

    from app.models.contract import InstallmentContract
    from app.services import accounting as accounting_service

    ctx = active_contract(client, national_id="GLJ-UNMAPPED")
    cid = ctx["contract_id"]
    contract = db.get(InstallmentContract, cid)

    long_ago = datetime.now(timezone.utc) - timedelta(days=3650)
    event = accounting_service.emit(
        db, event_type=AccountingEventType.late_fee_waived,
        event_reference="late-fee-waived-UNMAPPED-TEST",
        contract=contract, amount=Decimal("10.00"), event_date=long_ago,
    )
    journal = _journal_for(db, event)
    assert journal is not None
    assert journal.journal_status == JournalStatus.unmapped
    assert journal.mapping_version_id is None
    assert "no eventaccountmapping" in journal.error_message.lower()
    # the AccountingEvent itself is untouched — preserved, not dropped
    assert event.accounting_status == AccountingStatus.pending


def test_summary_only_event_does_not_create_a_gl_journal(client, db):
    ctx, cid, as_of = _make_delinquent(client, "GLJ-SUMMARY", 30)
    result = _run_ecl(client, as_of + timedelta(days=1), post=True)
    movement_ref = f"ecl-provision-movement-run-{result['run_id']}"
    event = _event_by_ref(db, movement_ref)
    assert _journal_for(db, event) is None


def test_reserved_event_type_has_no_active_mapping_lines_and_would_never_post(db):
    mapping = coa_service.get_active_mapping(db, AccountingEventType.recovery_adjustment)
    assert mapping.classification.value == "RESERVED"
    assert mapping.lines == []


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def test_duplicate_event_emission_never_creates_duplicate_journals(client, db):
    ctx = active_contract(client, national_id="GLJ-DUP-EMIT")
    cid = ctx["contract_id"]

    # re-confirming delivery is rejected by the domain guard long before a
    # second emit() could ever run — assert the emit()-level idempotency
    # directly instead, which is what actually protects this invariant.
    from app.models.contract import InstallmentContract
    from app.services import accounting as accounting_service

    contract = db.get(InstallmentContract, cid)
    before = db.execute(select(GLJournal)).scalars().all()
    accounting_service.emit(
        db, event_type=AccountingEventType.contract_activated,
        event_reference=f"contract-activated-{cid}", contract=contract,
        amount=contract.sales_order.sale_price, event_date=contract.activated_at,
    )
    after = db.execute(select(GLJournal)).scalars().all()
    assert len(before) == len(after)


def test_duplicate_posting_does_not_create_duplicate_erp_submissions(client, db):
    active_contract(client, national_id="GLJ-DUP-POST")
    first = client.post("/jobs/post-accounting-events").json()
    assert first["posted"] > 0

    refs_after_first = {
        j.external_gl_reference for j in db.execute(
            select(GLJournal).where(GLJournal.journal_status == JournalStatus.posted)
        ).scalars()
    }

    second = client.post("/jobs/post-accounting-events").json()
    assert second["posted"] == 0
    assert second["events_considered"] == 0

    refs_after_second = {
        j.external_gl_reference for j in db.execute(
            select(GLJournal).where(GLJournal.journal_status == JournalStatus.posted)
        ).scalars()
    }
    assert refs_after_first == refs_after_second


# --------------------------------------------------------------------------- #
# ECL double-posting protection
# --------------------------------------------------------------------------- #
def test_contract_level_ecl_events_post_and_portfolio_rollup_never_duplicates_them(client, db):
    # Deliberately NOT _make_delinquent() (which runs+posts an unposted ECL
    # run internally, leaving nothing left to move by the time this test's
    # own run executes) — backdate and post the FIRST real portfolio run
    # directly, so it carries the whole origination-to-delinquent movement.
    ctx = active_contract(client, national_id="GLJ-ECL-1")
    cid = ctx["contract_id"]
    as_of = first_due_date(client, cid) + timedelta(days=100)
    _assess_overdue(client, as_of)
    result = _run_ecl(client, as_of, post=True)
    run_id = result["run_id"]

    # scope to THIS run's own contract-level events only (event_reference is
    # deterministic per run+contract — see ecl.py::post_run) — the contract
    # also carries events from its origination assessment and from
    # _make_delinquent's own earlier run, which must NOT be counted here.
    contract_events = db.execute(
        select(AccountingEvent).where(
            AccountingEvent.event_reference.in_(
                [f"ecl-{run_id}-{cid}", f"ecl-ovr-{run_id}-{cid}"]
            ),
        )
    ).scalars().all()
    assert contract_events, "expected at least one contract-level ECL event for this run"

    contract_level_total = Decimal("0")
    for ev in contract_events:
        journal = _journal_for(db, ev)
        assert journal is not None
        assert journal.journal_status == JournalStatus.ready
        # signed contribution to the provision: debit-normal expense side
        # increases the provision, credit-normal side (post-reversal) decreases it
        debit_lines = [l for l in journal.lines if l.posting_side == PostingSide.debit and l.account_code_snapshot == "2300"]
        credit_lines = [l for l in journal.lines if l.posting_side == PostingSide.credit and l.account_code_snapshot == "2300"]
        contract_level_total += sum((l.amount for l in credit_lines), Decimal("0"))
        contract_level_total -= sum((l.amount for l in debit_lines), Decimal("0"))

    movement_event = _event_by_ref(db, f"ecl-provision-movement-run-{result['run_id']}")
    assert _journal_for(db, movement_event) is None  # SUMMARY_ONLY — never journalised

    assert contract_level_total == Decimal(str(round(result["total_provision_movement"], 2)))


# --------------------------------------------------------------------------- #
# Historical integrity
# --------------------------------------------------------------------------- #
def test_posted_journal_keeps_its_mapping_version_after_a_later_mapping_change(client, client_as, db, auth):
    ctx = active_contract(client, national_id="GLJ-HIST-1")
    cid = ctx["contract_id"]
    event = _event_by_ref(db, f"down-payment-received-{cid}")
    journal = _journal_for(db, event)
    original_mapping_id = journal.mapping_version_id
    original_line_snapshot = [(l.account_code_snapshot, l.amount) for l in journal.lines]

    # propose and approve a new v2 mapping for the SAME event type
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")
    deposit = coa_service.get_account_by_code(db, "2000")
    req = coa_service.propose_mapping_change(
        db, actor_id=maker, event_type=AccountingEventType.down_payment_received,
        classification="POSTABLE",
        lines=[
            {"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"},
            {"posting_side": "CREDIT", "account_id": deposit.id, "amount_source": "absolute_event_amount"},
        ],
    )
    db.commit()
    ok = client_as("admin").post(f"/approvals/{req.id}/approve")
    assert ok.status_code == 200, ok.text

    db.refresh(journal)
    assert journal.mapping_version_id == original_mapping_id  # unchanged
    assert [(l.account_code_snapshot, l.amount) for l in journal.lines] == original_line_snapshot


def test_account_snapshot_on_a_journal_line_survives_a_later_account_rename(client, db, auth, client_as):
    ctx = active_contract(client, national_id="GLJ-HIST-2")
    cid = ctx["contract_id"]
    event = _event_by_ref(db, f"down-payment-received-{cid}")
    journal = _journal_for(db, event)
    line = journal.lines[0]
    original_name_snapshot = line.account_name_snapshot
    account = coa_service.get_account(db, line.account_id)

    maker = auth["users"]["finance_officer"].id
    req = coa_service.propose_account_update(
        db, actor_id=maker, account_id=account.id, payload={"account_name": "Renamed For Test"}
    )
    db.commit()
    ok = client_as("admin").post(f"/approvals/{req.id}/approve")
    assert ok.status_code == 200, ok.text

    db.refresh(account)
    assert account.account_name == "Renamed For Test"
    db.refresh(line)
    assert line.account_name_snapshot == original_name_snapshot  # untouched
