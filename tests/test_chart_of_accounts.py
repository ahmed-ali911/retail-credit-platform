"""Chart of Accounts + Versioned Posting Rules — Checkpoint 1 (domain model
and control framework). No journal generation exists yet (Checkpoint 2) —
these tests cover the account/mapping domain model and its maker-checker
control framework only.
"""
from datetime import date

import pytest
from sqlalchemy import select

from app.models.accounting import AccountingEventType
from app.models.approval import ApprovalStatus
from app.models.gl import (
    ChartOfAccount,
    EventAccountMapping,
    EventAccountMappingLine,
    EventClassification,
    PostingSide,
)
from app.services import coa as coa_service
from app.services.errors import DomainError


# --------------------------------------------------------------------------- #
# Seed data
# --------------------------------------------------------------------------- #
def test_seed_creates_eighteen_demo_accounts_all_clearly_marked(db):
    accounts = coa_service.list_accounts(db)
    assert len(accounts) == 18
    for a in accounts:
        assert a.is_demo is True
        assert "DEMO / ILLUSTRATIVE PLACEHOLDER" in a.description
        assert "FINANCE DECISION REQUIRED" in a.description


def test_seed_creates_one_active_mapping_per_confirmed_event_type(db):
    for event_type in AccountingEventType:
        mapping = coa_service.get_active_mapping(db, event_type)
        assert mapping is not None, f"no seeded mapping for {event_type.value}"
        assert mapping.version == 1
        assert mapping.is_demo is True
        assert "FINANCE DECISION REQUIRED" in mapping.description


def test_seed_classifies_ecl_provision_movement_and_contract_closed_as_summary_only(db):
    movement = coa_service.get_active_mapping(db, AccountingEventType.ecl_provision_movement)
    closed = coa_service.get_active_mapping(db, AccountingEventType.contract_closed)
    assert movement.classification == EventClassification.summary_only
    assert closed.classification == EventClassification.summary_only
    assert movement.lines == []
    assert closed.lines == []


def test_seed_classifies_recovery_adjustment_as_reserved(db):
    mapping = coa_service.get_active_mapping(db, AccountingEventType.recovery_adjustment)
    assert mapping.classification == EventClassification.reserved
    assert mapping.lines == []


def test_every_postable_seeded_mapping_has_at_least_one_debit_and_one_credit_line_and_balances_by_construction(db):
    for event_type in AccountingEventType:
        mapping = coa_service.get_active_mapping(db, event_type)
        if mapping.classification != EventClassification.postable:
            continue
        sides = {line.posting_side for line in mapping.lines}
        assert PostingSide.debit in sides, f"{event_type.value} has no debit line"
        assert PostingSide.credit in sides, f"{event_type.value} has no credit line"
        for line in mapping.lines:
            assert line.account.is_active


def test_seed_is_idempotent_and_never_reseeds_over_existing_data(db):
    before = coa_service.list_accounts(db)
    result = coa_service.seed_demo_data(db)
    after = coa_service.list_accounts(db)
    assert result == {"accounts_created": 0, "mappings_created": 0}
    assert len(before) == len(after) == 18


# --------------------------------------------------------------------------- #
# Account controls
# --------------------------------------------------------------------------- #
def test_account_code_must_be_unique(db, auth):
    maker = auth["users"]["finance_officer"].id
    with pytest.raises(DomainError) as exc:
        coa_service.propose_account_create(
            db, actor_id=maker,
            payload={
                "account_code": "1000",  # already seeded (Bank / Cash)
                "account_name": "Duplicate", "account_type": "ASSET", "normal_balance": "DEBIT",
            },
        )
    assert exc.value.status_code == 409


def test_inactive_account_cannot_be_used_in_a_new_mapping_line(db, auth):
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")
    bank.is_active = False
    db.commit()

    with pytest.raises(DomainError) as exc:
        coa_service.propose_mapping_change(
            db, actor_id=maker, event_type=AccountingEventType.recovery_received,
            classification="POSTABLE",
            lines=[
                {"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"},
                {"posting_side": "CREDIT", "account_id": coa_service.get_account_by_code(db, "4300").id, "amount_source": "event_amount"},
            ],
        )
    assert exc.value.status_code == 422
    assert "not active" in exc.value.message


def test_account_used_by_a_mapping_line_cannot_change_code_or_type(db, auth):
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")  # referenced by several seeded lines
    assert coa_service.account_used_in_any_mapping_line(db, bank.id) is True

    with pytest.raises(DomainError) as exc:
        coa_service.propose_account_update(
            db, actor_id=maker, account_id=bank.id, payload={"account_code": "9999"}
        )
    assert exc.value.status_code == 409
    assert "immutable" in exc.value.message

    # renaming (not the code/type) is still allowed
    req = coa_service.propose_account_update(
        db, actor_id=maker, account_id=bank.id, payload={"account_name": "Bank / Cash (Renamed)"}
    )
    assert req.status == ApprovalStatus.pending


def test_account_referenced_by_the_active_mapping_cannot_be_deactivated(db, auth):
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")
    assert coa_service.account_used_in_active_mapping(db, bank.id) is True

    with pytest.raises(DomainError) as exc:
        coa_service.propose_account_deactivate(db, actor_id=maker, account_id=bank.id, reason="test")
    assert exc.value.status_code == 409
    assert "referenced" in exc.value.message


def test_unreferenced_account_can_be_proposed_for_deactivation(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    suspense = coa_service.get_account_by_code(db, "2410")  # seeded, deliberately unused
    assert coa_service.account_used_in_active_mapping(db, suspense.id) is False

    req = coa_service.propose_account_deactivate(db, actor_id=maker, account_id=suspense.id, reason="unused")
    db.commit()
    ok = client_as("admin").post(f"/approvals/{req.id}/approve")
    assert ok.status_code == 200, ok.text

    db.refresh(suspense)
    assert suspense.is_active is False


# --------------------------------------------------------------------------- #
# Maker-checker — accounts
# --------------------------------------------------------------------------- #
def test_account_creation_is_not_materialised_until_approved(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    req = coa_service.propose_account_create(
        db, actor_id=maker,
        payload={
            "account_code": "9000", "account_name": "Test Account",
            "account_type": "ASSET", "normal_balance": "DEBIT",
        },
    )
    db.commit()
    assert req.status == ApprovalStatus.pending
    assert coa_service.get_account_by_code(db, "9000") is None

    ok = client_as("admin").post(f"/approvals/{req.id}/approve")
    assert ok.status_code == 200, ok.text

    created = coa_service.get_account_by_code(db, "9000")
    assert created is not None
    assert created.is_active is True
    assert created.created_by == maker
    assert created.approved_by is not None


def test_rejected_account_proposal_never_creates_a_row(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    req = coa_service.propose_account_create(
        db, actor_id=maker,
        payload={
            "account_code": "9001", "account_name": "Never Created",
            "account_type": "EXPENSE", "normal_balance": "DEBIT",
        },
    )
    db.commit()
    rej = client_as("admin").post(f"/approvals/{req.id}/reject", json={"reason": "not needed"})
    assert rej.status_code == 200, rej.text
    assert coa_service.get_account_by_code(db, "9001") is None


def test_maker_cannot_approve_their_own_account_proposal(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    req = coa_service.propose_account_create(
        db, actor_id=maker,
        payload={
            "account_code": "9002", "account_name": "Self Approve Test",
            "account_type": "ASSET", "normal_balance": "DEBIT",
        },
    )
    db.commit()
    own = client_as("finance_officer").post(f"/approvals/{req.id}/approve")
    assert own.status_code == 409
    assert "your own request" in own.text


# --------------------------------------------------------------------------- #
# Maker-checker — mappings / versioning
# --------------------------------------------------------------------------- #
def test_mapping_change_creates_a_new_version_and_deactivates_the_old_one_without_editing_it(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    v1 = coa_service.get_active_mapping(db, AccountingEventType.recovery_received)
    v1_id, v1_lines_before = v1.id, [(l.posting_side, l.account_id) for l in v1.lines]

    bank = coa_service.get_account_by_code(db, "1000")
    recovery_income = coa_service.get_account_by_code(db, "4300")
    req = coa_service.propose_mapping_change(
        db, actor_id=maker, event_type=AccountingEventType.recovery_received,
        classification="POSTABLE",
        lines=[
            {"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"},
            {"posting_side": "CREDIT", "account_id": recovery_income.id, "amount_source": "absolute_event_amount"},
        ],
        change_reason="test: switch amount_source",
    )
    db.commit()
    ok = client_as("admin").post(f"/approvals/{req.id}/approve")
    assert ok.status_code == 200, ok.text

    v2 = coa_service.get_active_mapping(db, AccountingEventType.recovery_received)
    assert v2.id != v1_id
    assert v2.version == 2
    assert v2.is_active is True

    # the OLD version is superseded, not edited: still exists, now inactive,
    # its own lines exactly as they were
    old = db.get(EventAccountMapping, v1_id)
    assert old.is_active is False
    assert old.effective_to is not None
    assert [(l.posting_side, l.account_id) for l in old.lines] == v1_lines_before

    all_versions = coa_service.list_mapping_versions(db, AccountingEventType.recovery_received)
    assert [m.version for m in all_versions] == [2, 1]


def test_only_one_active_mapping_version_exists_per_event_type_at_a_time(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")
    fee_expense = coa_service.get_account_by_code(db, "5300")
    req = coa_service.propose_mapping_change(
        db, actor_id=maker, event_type=AccountingEventType.gateway_fee_recognized,
        classification="POSTABLE",
        lines=[
            {"posting_side": "DEBIT", "account_id": fee_expense.id, "amount_source": "event_amount"},
            {"posting_side": "CREDIT", "account_id": bank.id, "amount_source": "event_amount"},
        ],
    )
    db.commit()
    client_as("admin").post(f"/approvals/{req.id}/approve")

    active = [
        m for m in coa_service.list_mapping_versions(db, AccountingEventType.gateway_fee_recognized)
        if m.is_active
    ]
    assert len(active) == 1
    assert active[0].version == 2


def test_rejected_mapping_proposal_leaves_the_active_version_untouched(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    before = coa_service.get_active_mapping(db, AccountingEventType.payment_received)
    before_version = before.version

    bank = coa_service.get_account_by_code(db, "1000")
    receivable = coa_service.get_account_by_code(db, "1100")
    req = coa_service.propose_mapping_change(
        db, actor_id=maker, event_type=AccountingEventType.payment_received,
        classification="POSTABLE",
        lines=[
            {"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"},
            {"posting_side": "CREDIT", "account_id": receivable.id, "amount_source": "event_amount"},
        ],
    )
    db.commit()
    rej = client_as("admin").post(f"/approvals/{req.id}/reject")
    assert rej.status_code == 200, rej.text

    after = coa_service.get_active_mapping(db, AccountingEventType.payment_received)
    assert after.id == before.id
    assert after.version == before_version
    all_versions = coa_service.list_mapping_versions(db, AccountingEventType.payment_received)
    assert len(all_versions) == 1  # the rejected proposal never became a row


def test_proposing_a_postable_mapping_without_both_sides_is_rejected(db, auth):
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")
    with pytest.raises(DomainError) as exc:
        coa_service.propose_mapping_change(
            db, actor_id=maker, event_type=AccountingEventType.recovery_received,
            classification="POSTABLE",
            lines=[{"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"}],
        )
    assert exc.value.status_code == 422


def test_a_second_pending_mapping_proposal_for_the_same_event_type_is_blocked(db, auth):
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")
    income = coa_service.get_account_by_code(db, "4300")
    lines = [
        {"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"},
        {"posting_side": "CREDIT", "account_id": income.id, "amount_source": "event_amount"},
    ]
    coa_service.propose_mapping_change(
        db, actor_id=maker, event_type=AccountingEventType.recovery_received,
        classification="POSTABLE", lines=lines,
    )
    db.commit()
    with pytest.raises(DomainError) as exc:
        coa_service.propose_mapping_change(
            db, actor_id=maker, event_type=AccountingEventType.recovery_received,
            classification="POSTABLE", lines=lines,
        )
    assert exc.value.status_code == 409


def test_mapping_deactivation_requires_maker_checker_and_leaves_the_event_type_unmapped(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    req = coa_service.propose_mapping_deactivate(
        db, actor_id=maker, event_type=AccountingEventType.gateway_fee_recognized, reason="no longer needed"
    )
    db.commit()
    ok = client_as("admin").post(f"/approvals/{req.id}/approve")
    assert ok.status_code == 200, ok.text

    assert coa_service.get_active_mapping(db, AccountingEventType.gateway_fee_recognized) is None
    versions = coa_service.list_mapping_versions(db, AccountingEventType.gateway_fee_recognized)
    assert len(versions) == 1
    assert versions[0].is_active is False
    assert versions[0].effective_to is not None


def test_maker_cannot_approve_their_own_mapping_proposal(db, auth, client_as):
    maker = auth["users"]["finance_officer"].id
    bank = coa_service.get_account_by_code(db, "1000")
    income = coa_service.get_account_by_code(db, "4300")
    req = coa_service.propose_mapping_change(
        db, actor_id=maker, event_type=AccountingEventType.recovery_received,
        classification="POSTABLE",
        lines=[
            {"posting_side": "DEBIT", "account_id": bank.id, "amount_source": "event_amount"},
            {"posting_side": "CREDIT", "account_id": income.id, "amount_source": "event_amount"},
        ],
    )
    db.commit()
    own = client_as("finance_officer").post(f"/approvals/{req.id}/approve")
    assert own.status_code == 409
