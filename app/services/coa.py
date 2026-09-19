"""Chart of Accounts + versioned posting rules — proposal layer and demo seed.

Every account/mapping row this module can create is materialised only at
approval time (`services/approvals.py`'s `_execute` branches for the
`ACTION_COA_*` action types), mirroring `ecl_config.py::activate_version()`'s
own shape: a rejected proposal leaves no row behind, and this module never
mutates an approved row in place — a mapping "change" always means "propose
the next version."

Journal generation (turning a `POSTABLE` mapping + an `AccountingEvent` into
a `GLJournal`) is NOT this checkpoint — see `app/models/gl.py`'s module
docstring. This file only builds and controls the domain model: accounts,
mappings, mapping lines, and the demo seed data every one of them is
stamped with.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import AccountingEventType
from app.models.approval import (
    ACTION_COA_ACCOUNT_CREATE,
    ACTION_COA_ACCOUNT_DEACTIVATE,
    ACTION_COA_ACCOUNT_UPDATE,
    ACTION_COA_MAPPING_CREATE,
    ACTION_COA_MAPPING_DEACTIVATE,
    ACTION_COA_MAPPING_UPDATE,
    ApprovalRequest,
    ApprovalStatus,
)
from app.models.gl import (
    AccountType,
    AmountSource,
    ChartOfAccount,
    EventAccountMapping,
    EventAccountMappingLine,
    EventClassification,
    NormalBalance,
    PostingSide,
)
from app.services import approvals as approval_service
from app.services.errors import DomainError

_DEMO_NOTE = (
    "DEMO / ILLUSTRATIVE PLACEHOLDER — FINANCE DECISION REQUIRED. Not this "
    "company's real chart of accounts, GL code convention, or approved "
    "debit/credit treatment. See docs/chart-of-accounts/BRD.md §6/§17."
)

_MAPPING_ENTITY_TYPE = "event_account_mapping"
_ACCOUNT_ENTITY_TYPE = "chart_of_account"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Accounts — reads
# --------------------------------------------------------------------------- #
def get_account(db: Session, account_id: int) -> ChartOfAccount:
    account = db.get(ChartOfAccount, account_id)
    if account is None:
        raise DomainError("Account not found", status_code=404)
    return account


def get_account_by_code(db: Session, account_code: str) -> ChartOfAccount | None:
    return db.execute(
        select(ChartOfAccount).where(ChartOfAccount.account_code == account_code)
    ).scalar_one_or_none()


def list_accounts(db: Session, *, is_active: bool | None = None) -> list[ChartOfAccount]:
    stmt = select(ChartOfAccount).order_by(ChartOfAccount.account_code)
    if is_active is not None:
        stmt = stmt.where(ChartOfAccount.is_active == is_active)
    return list(db.execute(stmt).scalars().all())


def account_used_in_any_mapping_line(db: Session, account_id: int) -> bool:
    """Ever referenced, by any mapping version (active or superseded) — the
    condition that makes `account_code`/`account_type` immutable and a hard
    delete impossible. Once GLJournalLine is populated (Checkpoint 2), that
    table must be checked here too — not yet possible, it is always empty in
    this checkpoint."""
    return (
        db.execute(
            select(EventAccountMappingLine.id).where(
                EventAccountMappingLine.account_id == account_id
            ).limit(1)
        ).first()
        is not None
    )


def account_used_in_active_mapping(db: Session, account_id: int) -> bool:
    """Referenced by a line on the CURRENTLY ACTIVE mapping of some event
    type — the narrower condition that blocks deactivation."""
    return (
        db.execute(
            select(EventAccountMappingLine.id)
            .join(EventAccountMapping, EventAccountMappingLine.mapping_id == EventAccountMapping.id)
            .where(
                EventAccountMappingLine.account_id == account_id,
                EventAccountMappingLine.is_active.is_(True),
                EventAccountMapping.is_active.is_(True),
            )
            .limit(1)
        ).first()
        is not None
    )


# --------------------------------------------------------------------------- #
# Accounts — propose (maker-checker; materialised only on approval)
# --------------------------------------------------------------------------- #
def _pending_account_proposal(db: Session, action_type: str, entity_id: object) -> ApprovalRequest | None:
    return approval_service.pending_request_for(db, action_type, entity_id)


def propose_account_create(db: Session, *, actor_id: int, payload: dict) -> ApprovalRequest:
    account_code = (payload.get("account_code") or "").strip()
    account_name = (payload.get("account_name") or "").strip()
    if not account_code or not account_name:
        raise DomainError("account_code and account_name are required", status_code=422)
    try:
        account_type = AccountType(payload["account_type"])
        normal_balance = NormalBalance(payload["normal_balance"])
    except (KeyError, ValueError):
        raise DomainError(
            f"account_type must be one of {[t.value for t in AccountType]} and "
            f"normal_balance one of {[b.value for b in NormalBalance]}",
            status_code=422,
        )
    if get_account_by_code(db, account_code) is not None:
        raise DomainError(f"Account code {account_code!r} already exists", status_code=409)
    if _pending_account_proposal(db, ACTION_COA_ACCOUNT_CREATE, account_code) is not None:
        raise DomainError(
            f"An account-creation proposal for code {account_code!r} is already pending",
            status_code=409,
        )

    return approval_service.create_request(
        db,
        action_type=ACTION_COA_ACCOUNT_CREATE,
        entity_type=_ACCOUNT_ENTITY_TYPE,
        entity_id=account_code,
        requested_by=actor_id,
        payload={
            "account_code": account_code,
            "account_name": account_name,
            "account_type": account_type.value,
            "normal_balance": normal_balance.value,
            "is_demo": bool(payload.get("is_demo", True)),
            "description": (payload.get("description") or "").strip() or _DEMO_NOTE,
        },
    )


def propose_account_update(db: Session, *, actor_id: int, account_id: int, payload: dict) -> ApprovalRequest:
    account = get_account(db, account_id)
    changes: dict = {}

    if "account_name" in payload and payload["account_name"]:
        changes["account_name"] = payload["account_name"].strip()
    if "description" in payload and payload["description"]:
        changes["description"] = payload["description"].strip()
    if "account_code" in payload and payload["account_code"] and payload["account_code"] != account.account_code:
        if account_used_in_any_mapping_line(db, account.id):
            raise DomainError(
                f"Account {account.account_code} is already referenced by a mapping line — "
                "account_code is immutable once used",
                status_code=409,
            )
        existing = get_account_by_code(db, payload["account_code"])
        if existing is not None and existing.id != account.id:
            raise DomainError(f"Account code {payload['account_code']!r} already exists", status_code=409)
        changes["account_code"] = payload["account_code"].strip()
    if "account_type" in payload and payload["account_type"] and payload["account_type"] != account.account_type.value:
        if account_used_in_any_mapping_line(db, account.id):
            raise DomainError(
                f"Account {account.account_code} is already referenced by a mapping line — "
                "account_type is immutable once used",
                status_code=409,
            )
        try:
            changes["account_type"] = AccountType(payload["account_type"]).value
        except ValueError:
            raise DomainError(f"account_type must be one of {[t.value for t in AccountType]}", status_code=422)
    if "normal_balance" in payload and payload["normal_balance"]:
        try:
            changes["normal_balance"] = NormalBalance(payload["normal_balance"]).value
        except ValueError:
            raise DomainError(f"normal_balance must be one of {[b.value for b in NormalBalance]}", status_code=422)

    if not changes:
        raise DomainError("No changeable field was supplied", status_code=422)
    if _pending_account_proposal(db, ACTION_COA_ACCOUNT_UPDATE, account.id) is not None:
        raise DomainError(f"An update proposal for account {account.id} is already pending", status_code=409)

    return approval_service.create_request(
        db,
        action_type=ACTION_COA_ACCOUNT_UPDATE,
        entity_type=_ACCOUNT_ENTITY_TYPE,
        entity_id=account.id,
        requested_by=actor_id,
        payload={
            "old_value": {
                "account_code": account.account_code,
                "account_name": account.account_name,
                "account_type": account.account_type.value,
                "normal_balance": account.normal_balance.value,
                "description": account.description,
            },
            "changes": changes,
        },
    )


def propose_account_deactivate(db: Session, *, actor_id: int, account_id: int, reason: str | None) -> ApprovalRequest:
    account = get_account(db, account_id)
    if not account.is_active:
        raise DomainError(f"Account {account.account_code} is already inactive", status_code=409)
    if account_used_in_active_mapping(db, account.id):
        raise DomainError(
            f"Account {account.account_code} is referenced by the currently active mapping of "
            "at least one event type — deactivating it would silently break future journal "
            "generation. Propose a mapping change to stop using it first.",
            status_code=409,
        )
    if _pending_account_proposal(db, ACTION_COA_ACCOUNT_DEACTIVATE, account.id) is not None:
        raise DomainError(f"A deactivation proposal for account {account.id} is already pending", status_code=409)

    return approval_service.create_request(
        db,
        action_type=ACTION_COA_ACCOUNT_DEACTIVATE,
        entity_type=_ACCOUNT_ENTITY_TYPE,
        entity_id=account.id,
        requested_by=actor_id,
        payload={"account_code": account.account_code, "reason": reason},
    )


# --------------------------------------------------------------------------- #
# Mappings — reads
# --------------------------------------------------------------------------- #
def get_active_mapping(db: Session, event_type: AccountingEventType) -> EventAccountMapping | None:
    return db.execute(
        select(EventAccountMapping).where(
            EventAccountMapping.account_event_type == event_type,
            EventAccountMapping.is_active.is_(True),
        )
    ).scalar_one_or_none()


def list_mapping_versions(db: Session, event_type: AccountingEventType) -> list[EventAccountMapping]:
    return list(
        db.execute(
            select(EventAccountMapping)
            .where(EventAccountMapping.account_event_type == event_type)
            .order_by(EventAccountMapping.version.desc())
        ).scalars()
    )


def list_active_mappings(db: Session) -> list[EventAccountMapping]:
    return list(
        db.execute(
            select(EventAccountMapping).where(EventAccountMapping.is_active.is_(True))
        ).scalars()
    )


# --------------------------------------------------------------------------- #
# Mappings — propose (maker-checker; a new version is materialised only on
# approval — the row referenced by entity_id below is a KEY, not a FK,
# because no EventAccountMapping row exists yet for a brand-new event type)
# --------------------------------------------------------------------------- #
def _pending_mapping_proposal(db: Session, event_type: AccountingEventType) -> ApprovalRequest | None:
    """Blocks a second pending create/update/deactivate proposal for the
    SAME event type regardless of which of the three action types it is —
    `pending_request_for` alone only matches within one action_type."""
    return db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.action_type.in_(
                [ACTION_COA_MAPPING_CREATE, ACTION_COA_MAPPING_UPDATE, ACTION_COA_MAPPING_DEACTIVATE]
            ),
            ApprovalRequest.entity_type == _MAPPING_ENTITY_TYPE,
            ApprovalRequest.entity_id == event_type.value,
            ApprovalRequest.status == ApprovalStatus.pending,
        )
    ).scalar_one_or_none()


def _validate_mapping_lines(db: Session, classification: EventClassification, raw_lines: list[dict]) -> list[dict]:
    if classification != EventClassification.postable:
        # A SUMMARY_ONLY/RESERVED mapping carries no lines by construction —
        # see app/models/gl.py's EventClassification docstring.
        return []

    if not raw_lines:
        raise DomainError(
            "A POSTABLE mapping needs at least one DEBIT and one CREDIT line", status_code=422
        )

    cleaned: list[dict] = []
    sides: set[str] = set()
    for i, line in enumerate(raw_lines, start=1):
        try:
            side = PostingSide(line["posting_side"])
            amount_source = AmountSource(line["amount_source"])
        except (KeyError, ValueError) as exc:
            raise DomainError(f"Mapping line {i}: {exc}", status_code=422)
        account_id = line.get("account_id")
        account = db.get(ChartOfAccount, account_id) if account_id is not None else None
        if account is None:
            raise DomainError(f"Mapping line {i}: account {account_id} not found", status_code=422)
        if not account.is_active:
            raise DomainError(
                f"Mapping line {i}: account {account.account_code} is not active", status_code=422
            )
        sides.add(side.value)
        cleaned.append(
            {
                "line_sequence": line.get("line_sequence") if line.get("line_sequence") is not None else i,
                "posting_side": side.value,
                "account_id": account.id,
                "amount_source": amount_source.value,
                "multiplier": str(line.get("multiplier", "1")),
                "reverse_on_negative": bool(line.get("reverse_on_negative", False)),
                "description": line.get("description"),
            }
        )

    if "DEBIT" not in sides or "CREDIT" not in sides:
        raise DomainError(
            "A POSTABLE mapping needs at least one DEBIT and one CREDIT line", status_code=422
        )
    return cleaned


def propose_mapping_change(
    db: Session,
    *,
    actor_id: int,
    event_type: AccountingEventType,
    classification: str,
    lines: list[dict],
    effective_from: date | None = None,
    description: str | None = None,
    change_reason: str | None = None,
) -> ApprovalRequest:
    """Proposes the NEXT version for `event_type` — version 1 if none exists
    yet (action_type recorded as `mapping_create`), otherwise the next
    integer after the current active version (`mapping_update`). One
    function, because the only real difference is which of the two action
    types gets recorded and what the Approvals screen calls it — the
    validation and eventual activation are identical either way."""
    try:
        classification_enum = EventClassification(classification)
    except ValueError:
        raise DomainError(
            f"classification must be one of {[c.value for c in EventClassification]}", status_code=422
        )

    current = get_active_mapping(db, event_type)
    action_type = ACTION_COA_MAPPING_UPDATE if current is not None else ACTION_COA_MAPPING_CREATE
    next_version = (current.version + 1) if current is not None else 1

    cleaned_lines = _validate_mapping_lines(db, classification_enum, lines)

    if _pending_mapping_proposal(db, event_type) is not None:
        raise DomainError(
            f"A mapping proposal for {event_type.value} is already pending", status_code=409
        )

    return approval_service.create_request(
        db,
        action_type=action_type,
        entity_type=_MAPPING_ENTITY_TYPE,
        entity_id=event_type.value,
        requested_by=actor_id,
        payload={
            "account_event_type": event_type.value,
            "from_version": current.version if current is not None else None,
            "version": next_version,
            "classification": classification_enum.value,
            "effective_from": (effective_from or _utcnow().date()).isoformat(),
            "is_demo": True,
            "description": (description or "").strip() or _DEMO_NOTE,
            "change_reason": change_reason,
            "lines": cleaned_lines,
        },
    )


def propose_mapping_deactivate(
    db: Session, *, actor_id: int, event_type: AccountingEventType, reason: str | None
) -> ApprovalRequest:
    current = get_active_mapping(db, event_type)
    if current is None:
        raise DomainError(f"No active mapping exists for {event_type.value}", status_code=409)
    if _pending_mapping_proposal(db, event_type) is not None:
        raise DomainError(
            f"A mapping proposal for {event_type.value} is already pending", status_code=409
        )

    return approval_service.create_request(
        db,
        action_type=ACTION_COA_MAPPING_DEACTIVATE,
        entity_type=_MAPPING_ENTITY_TYPE,
        entity_id=event_type.value,
        requested_by=actor_id,
        payload={
            "account_event_type": event_type.value,
            "mapping_id": current.id,
            "version": current.version,
            "reason": reason,
        },
    )


# =========================================================================== #
# Demo seed data
#
# Bootstrap facts, not maker-checker decisions — inserted directly, exactly
# like ecl_config.py::get_active()'s own v1 seed (no ApprovalRequest is
# created for this; there is no "maker" for a very first bootstrap default).
# Every account and every mapping below is explicitly DEMO / ILLUSTRATIVE —
# see `_DEMO_NOTE` and each row's own `description`.
#
# The illustrative accounting basis worked out during the Checkpoint 0/1
# design pass (documented in full in docs/chart-of-accounts/BRD.md §4):
#   * "Installment Receivable" is treated as ONE blended gross account
#     carrying principal + unearned profit + late fees together — a
#     simplification; the platform's own confirmed Receivable definition
#     (`receivable.py`) tracks late fees as a genuinely separate line from
#     principal/profit, so a real Finance-approved CoA would likely split
#     this into two accounts. Flagged, not silently resolved.
#   * `contract_activated` / `early_settlement` / `write_off_executed` /
#     `partial_write_off_executed` are internally consistent with each
#     other under that same gross-receivable convention (verified to
#     balance debits=credits algebraically, not just by construction).
#   * `return`'s compound mapping deliberately does NOT reverse Sales
#     Revenue, Inventory, or COGS (no inventory-cost data exists anywhere
#     in this platform — see AmountSource's docstring) — the
#     principal/late-fee portion of a returned receivable is routed through
#     the "Settlement Difference / Suspense" account instead of a real
#     account, and is explicitly labelled as pending a Finance decision on
#     Sales Revenue reversal, rather than silently picking one.
#   * `cancellation`'s mapping does NOT reverse Sales Revenue/Receivable
#     either — a cancellation only ever happens pre-delivery, and this
#     platform's `contract_activated`/`down_payment_received` events only
#     fire AT delivery (see BRD.md §4 finding D.2) — so nothing has been
#     booked yet at cancellation time to reverse.
#   * `contract_closed` (normal full-repayment closure) and
#     `ecl_provision_movement` (portfolio roll-up — see the ECL
#     double-posting note in app/models/gl.py) are SUMMARY_ONLY: no journal
#     is ever generated for them.
#   * `recovery_adjustment` is RESERVED: nothing in this platform writes
#     this event yet.
# =========================================================================== #
AET = AccountingEventType
_D, _C = PostingSide.debit.value, PostingSide.credit.value

_SEED_ACCOUNTS: list[tuple[str, str, str, str, str]] = [
    # code, name, account_type, normal_balance, short description
    ("1000", "Bank / Cash", "ASSET", "DEBIT", "Cash/bank settlement account."),
    ("1010", "Payment Gateway Clearing", "ASSET", "DEBIT", "Funds in transit from the mock payment gateway before settlement."),
    ("1100", "Installment Receivable", "ASSET", "DEBIT", "Gross customer receivable — principal + unearned profit + late fees, blended (see module note on the platform's own separate late-fee receivable convention)."),
    ("1200", "Inventory", "ASSET", "DEBIT", "Product inventory at cost. UNUSED by every seeded mapping below — no cost/COGS field exists anywhere in this platform (Product has cash_price only)."),
    ("2000", "Customer Deposit Liability", "LIABILITY", "CREDIT", "Down payments held before being recognised as revenue at delivery."),
    ("2100", "Unearned Finance Income", "LIABILITY", "CREDIT", "Contractual profit not yet recognised."),
    ("2200", "Refund / Customer Payable", "LIABILITY", "CREDIT", "Amounts owed back to a customer (cancellation/return/reversal)."),
    ("2300", "ECL Allowance / Loss Allowance", "ASSET", "CREDIT", "Contra-asset — accumulated expected-credit-loss provision against Installment Receivable."),
    ("2400", "Settlement Difference / Suspense", "ASSET", "DEBIT", "Gateway-vs-internal settlement variances, and the unresolved portion of a return's receivable write-down pending a Sales Revenue reversal policy."),
    ("2410", "Rounding Difference / Suspense", "ASSET", "DEBIT", "Reserved — no seeded mapping currently posts to it; no rounding-difference source exists yet."),
    ("4000", "Sales Revenue", "INCOME", "CREDIT", "Cash selling price recognised at delivery."),
    ("4100", "Finance Profit Income", "INCOME", "CREDIT", "Contractual profit recognised as it is earned/collected."),
    ("4200", "Late Fee Income", "INCOME", "CREDIT", "Late fees assessed on overdue installments."),
    ("4300", "Recovery Income", "INCOME", "CREDIT", "Cash recovered against an already-written-off receivable."),
    ("5000", "Cost of Goods Sold", "EXPENSE", "DEBIT", "UNUSED by every seeded mapping below — same missing-cost-data reason as Inventory (1200)."),
    ("5100", "ECL / Impairment Expense", "EXPENSE", "DEBIT", "Expected-credit-loss charge/release for the period."),
    ("5200", "Write-off Expense", "EXPENSE", "DEBIT", "Portion of a write-off not covered by existing ECL allowance."),
    ("5300", "Gateway Fee Expense", "EXPENSE", "DEBIT", "The mock payment gateway's own transaction fee."),
]

_SIMPLE = "postable_simple"  # internal marker, not persisted


def _line(side: str, code: str, source: AmountSource, *, reverse: bool = False, desc: str | None = None) -> dict:
    return {
        "posting_side": side,
        "account_code": code,
        "amount_source": source.value,
        "reverse_on_negative": reverse,
        "description": desc,
    }


_SEED_MAPPINGS: list[tuple[AccountingEventType, str, str, list[dict]]] = [
    # event_type, classification, description, lines (account_code resolved to id at seed time)
    (
        AET.contract_activated, "POSTABLE",
        "Delivery confirmation: clears the down payment already collected, "
        "books the gross future receivable, recognises the cash sale, and "
        "sets up unearned finance income. Excludes COGS/Inventory (no cost "
        "data — see module note).",
        [
            _line(_D, "2000", AmountSource.down_payment),
            _line(_D, "1100", AmountSource.gross_installment_receivable),
            _line(_C, "4000", AmountSource.cash_price),
            _line(_C, "2100", AmountSource.total_contractual_profit),
        ],
    ),
    (
        AET.down_payment_received, "POSTABLE",
        "Cash received for the down payment (fires alongside contract_activated "
        "in this platform's current lifecycle — see BRD.md §4 finding D.2).",
        [_line(_D, "1000", AmountSource.event_amount), _line(_C, "2000", AmountSource.event_amount)],
    ),
    (
        AET.payment_received, "POSTABLE",
        "Cash received against the gross receivable (principal+profit+late-fee blended).",
        [_line(_D, "1000", AmountSource.event_amount), _line(_C, "1100", AmountSource.event_amount)],
    ),
    (
        AET.profit_recognized, "POSTABLE",
        "Reclassifies the profit portion of a payment from unearned to earned.",
        [_line(_D, "2100", AmountSource.event_amount), _line(_C, "4100", AmountSource.event_amount)],
    ),
    (
        AET.late_fee_charged, "POSTABLE",
        "A new late fee charge — adds to the receivable and recognises fee income.",
        [_line(_D, "1100", AmountSource.event_amount), _line(_C, "4200", AmountSource.event_amount)],
    ),
    (
        AET.late_fee_waived, "POSTABLE",
        "A waived late fee — reverses the fee income and the receivable.",
        [_line(_D, "4200", AmountSource.event_amount), _line(_C, "1100", AmountSource.event_amount)],
    ),
    (
        AET.early_settlement, "POSTABLE",
        "Full payoff collected in cash; the still-charged profit is recognised, "
        "the rebated profit writes down the receivable. Resolved from the "
        "LedgerEntry rows settle_contract() dual-writes (reference_type="
        "contract_closure), not from the event's own amount (which is 0.00 "
        "for a plain settlement — see BRD.md §4 finding D.3).",
        [
            _line(_D, "1000", AmountSource.closure_ledger_payoff_total),
            _line(_C, "1100", AmountSource.closure_ledger_payoff_total),
            _line(_D, "2100", AmountSource.closure_ledger_profit_recognized),
            _line(_C, "4100", AmountSource.closure_ledger_profit_recognized),
            _line(_D, "2100", AmountSource.closure_ledger_profit_rebated),
            _line(_C, "1100", AmountSource.closure_ledger_profit_rebated),
        ],
    ),
    (
        AET.cancellation, "POSTABLE",
        "Pre-delivery cancellation: clears the down-payment liability into a "
        "refund payable. Does NOT reverse Sales Revenue/Receivable — nothing "
        "has been booked yet pre-delivery (see BRD.md §4 finding D.2).",
        [_line(_D, "2000", AmountSource.event_amount), _line(_C, "2200", AmountSource.event_amount)],
    ),
    (
        AET.return_, "POSTABLE",
        "Post-delivery return: recognises retained profit, writes down waived "
        "profit and the remaining principal/late-fee receivable. The "
        "principal/late-fee write-down is routed through Settlement "
        "Difference/Suspense, not Sales Revenue — a fuller reversal is a "
        "FINANCE DECISION REQUIRED (no inventory-cost data either — see "
        "BRD.md §4 finding D.4). Resolved from the dedicated return_* "
        "LedgerEntry rows return_contract() now writes.",
        [
            _line(_D, "2100", AmountSource.closure_ledger_return_profit_retained),
            _line(_C, "4100", AmountSource.closure_ledger_return_profit_retained),
            _line(_D, "2100", AmountSource.closure_ledger_return_profit_waived),
            _line(_C, "1100", AmountSource.closure_ledger_return_profit_waived),
            _line(_D, "2400", AmountSource.closure_ledger_return_principal),
            _line(_C, "1100", AmountSource.closure_ledger_return_principal),
            _line(_D, "2400", AmountSource.closure_ledger_return_late_fee),
            _line(_C, "1100", AmountSource.closure_ledger_return_late_fee),
        ],
    ),
    (
        AET.contract_closed, "SUMMARY_ONLY",
        "Normal full-repayment closure — financial_adjustment is always 0.00 "
        "by construction; every real money movement already posted through "
        "payment_received/profit_recognized. No journal is generated.",
        [],
    ),
    (
        AET.ecl_provision_movement, "SUMMARY_ONLY",
        "Portfolio-level roll-up (one per posted ECL run) — the SAME movement "
        "the per-contract ecl_provision_created/_increased/_released/"
        "_override_adjustment events already post individually. Posting both "
        "would double-count the provision (confirmed in the Checkpoint 0 "
        "audit). Preserved for the portfolio report; never journalised.",
        [],
    ),
    (
        AET.ecl_provision_created, "POSTABLE",
        "First provision for a contract.",
        [_line(_D, "5100", AmountSource.event_amount, reverse=True), _line(_C, "2300", AmountSource.event_amount, reverse=True)],
    ),
    (
        AET.ecl_provision_increased, "POSTABLE",
        "Provision increased at a later ECL run.",
        [_line(_D, "5100", AmountSource.event_amount, reverse=True), _line(_C, "2300", AmountSource.event_amount, reverse=True)],
    ),
    (
        AET.ecl_provision_released, "POSTABLE",
        "Provision released (amount is always negative at source — "
        "reverse_on_negative swaps this pair to Dr Allowance / Cr Expense).",
        [_line(_D, "5100", AmountSource.event_amount, reverse=True), _line(_C, "2300", AmountSource.event_amount, reverse=True)],
    ),
    (
        AET.ecl_provision_override_adjustment, "POSTABLE",
        "Manual override delta on top of the model-driven movement — signed "
        "either way; reverse_on_negative applies.",
        [_line(_D, "5100", AmountSource.event_amount, reverse=True), _line(_C, "2300", AmountSource.event_amount, reverse=True)],
    ),
    (
        AET.payment_reversed, "POSTABLE",
        "A settled gateway payment taken back — reinstates the receivable.",
        [_line(_D, "1100", AmountSource.event_amount), _line(_C, "1000", AmountSource.event_amount)],
    ),
    (
        AET.refund_completed, "POSTABLE",
        "A settled gateway payment refunded in full — reinstates the receivable.",
        [_line(_D, "1100", AmountSource.event_amount), _line(_C, "1000", AmountSource.event_amount)],
    ),
    (
        AET.gateway_fee_recognized, "POSTABLE",
        "The mock gateway's own transaction fee, when known.",
        [_line(_D, "5300", AmountSource.event_amount), _line(_C, "1010", AmountSource.event_amount)],
    ),
    (
        AET.settlement_difference, "POSTABLE",
        "Gateway-vs-internal settlement variance — signed either way; "
        "reverse_on_negative applies.",
        [_line(_D, "1010", AmountSource.event_amount, reverse=True), _line(_C, "2400", AmountSource.event_amount, reverse=True)],
    ),
    (
        AET.write_off_executed, "POSTABLE",
        "Full write-off: uses available ECL allowance first, expenses any "
        "excess, and clears the receivable by component (principal/profit/"
        "late fee kept separate, not one combined pair — per the brief's own "
        "instruction). Resolved from WriteOffExecution's own stored columns.",
        [
            _line(_D, "2300", AmountSource.provision_used),
            _line(_D, "5200", AmountSource.write_off_expense_excess),
            _line(_C, "1100", AmountSource.written_off_principal),
            _line(_C, "1100", AmountSource.written_off_profit),
            _line(_C, "1100", AmountSource.written_off_late_fee),
        ],
    ),
    (
        AET.partial_write_off_executed, "POSTABLE",
        "Partial write-off — identical treatment to a full write-off, scoped "
        "to the requested components only; the contract stays active.",
        [
            _line(_D, "2300", AmountSource.provision_used),
            _line(_D, "5200", AmountSource.write_off_expense_excess),
            _line(_C, "1100", AmountSource.written_off_principal),
            _line(_C, "1100", AmountSource.written_off_profit),
            _line(_C, "1100", AmountSource.written_off_late_fee),
        ],
    ),
    (
        AET.recovery_received, "POSTABLE",
        "Cash recovered against an already-written-off execution.",
        [_line(_D, "1000", AmountSource.event_amount), _line(_C, "4300", AmountSource.event_amount)],
    ),
    (
        AET.recovery_adjustment, "RESERVED",
        "Reserved for a future recovery reversal/correction — nothing in "
        "this platform writes this event yet.",
        [],
    ),
]


def seed_demo_data(db: Session) -> dict:
    """Idempotent bootstrap: seeds the 18 demo accounts and one active,
    version-1 mapping per `AccountingEventType` (23, matching the confirmed
    enum exactly) if — and only if — the Chart of Accounts is currently
    empty. Never re-seeds over existing data (mirrors
    `ConfigService.seed_from_yaml`'s own "only add what's missing" spirit,
    simplified to a whole-table guard since every row here is interdependent
    — an account and the mapping lines that reference it must appear
    together or not at all)."""
    if db.execute(select(ChartOfAccount.id).limit(1)).first() is not None:
        return {"accounts_created": 0, "mappings_created": 0}

    accounts_by_code: dict[str, ChartOfAccount] = {}
    for code, name, acct_type, normal_balance, desc in _SEED_ACCOUNTS:
        account = ChartOfAccount(
            account_code=code,
            account_name=name,
            account_type=AccountType(acct_type),
            normal_balance=NormalBalance(normal_balance),
            is_active=True,
            is_demo=True,
            description=f"{desc} {_DEMO_NOTE}",
            approved_at=_utcnow(),
        )
        db.add(account)
        accounts_by_code[code] = account
    db.flush()

    mappings_created = 0
    for event_type, classification, description, raw_lines in _SEED_MAPPINGS:
        mapping = EventAccountMapping(
            account_event_type=event_type,
            version=1,
            classification=EventClassification(classification),
            effective_from=_utcnow().date(),
            effective_to=None,
            is_active=True,
            is_demo=True,
            description=f"{description} {_DEMO_NOTE}",
            change_reason="Initial demo seed.",
            approved_at=_utcnow(),
        )
        db.add(mapping)
        db.flush()
        for i, line in enumerate(raw_lines, start=1):
            db.add(
                EventAccountMappingLine(
                    mapping_id=mapping.id,
                    line_sequence=i,
                    posting_side=PostingSide(line["posting_side"]),
                    account_id=accounts_by_code[line["account_code"]].id,
                    amount_source=AmountSource(line["amount_source"]),
                    reverse_on_negative=line["reverse_on_negative"],
                    description=line["description"],
                )
            )
        mappings_created += 1
    db.commit()

    return {"accounts_created": len(accounts_by_code), "mappings_created": mappings_created}
