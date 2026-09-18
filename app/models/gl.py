"""Chart of Accounts + Versioned Posting Rules + Double-Entry Journal.

Converts the existing flat `AccountingEvent` stream (one signed `amount` per
event — see `app/models/accounting.py`, Gap G-07 / BDR-31 / BDR-PG-07) into
explainable, balanced double-entry journals, without changing anything about
how or when an `AccountingEvent` itself gets created.

**Every seeded account and posting rule is a DEMO / ILLUSTRATIVE PLACEHOLDER —
FINANCE DECISION REQUIRED.** Nothing here is this company's real chart of
accounts, GL code convention, or approved debit/credit treatment — see
`app/services/coa.py`'s seed data for the exact, explicit wording, and
`docs/chart-of-accounts/BRD.md` §6/§17 for the full list of open decisions.

Five tables, mirroring this codebase's two most relevant precedents:

  * `ChartOfAccount` — the account master. Controlled the same way every
    other reference entity in this platform is: maker-checker-gated create/
    edit/deactivate (`services/approvals.py`), never a hard delete.
  * `EventAccountMapping` / `EventAccountMappingLine` — a versioned posting
    rule per `AccountingEventType` (reusing the EXISTING enum — no parallel
    event taxonomy). Versioning mirrors `ecl_config.py::activate_version()`
    exactly: a change creates a new version and deactivates the prior one
    prospectively; a version, once created, is never edited in place.
  * `GLJournal` / `GLJournalLine` — the double-entry result of applying one
    `EventAccountMapping` version to one `AccountingEvent`. **Defined now,
    with the rest of the domain model, so the migration lands once** — the
    same reasoning `models/write_off.py` used for `WriteOffExecution` and
    `Recovery`: journal GENERATION is a later checkpoint (amount-source
    resolution, balance validation, `emit()` integration); this checkpoint
    only defines the shape and the control framework around it.

Confirmed design decisions from the Checkpoint 0 audit (see the chat
transcript / BRD.md §4 for the full reasoning):

  * One `GLJournal` per `AccountingEvent` (`accounting_event_id` unique).
  * A journal's own debit/credit total is never computed by summing signed
    `AccountingEvent.amount`s — every `GLJournalLine.amount` is a POSITIVE
    magnitude; direction lives entirely in `posting_side`.
  * `EventAccountMapping` classification (`POSTABLE` / `SUMMARY_ONLY` /
    `RESERVED`) is itself a maker-checker-approved, versioned fact — not a
    hardcoded Python constant — so *why* `ecl_provision_movement` never
    posts a journal (it is the portfolio-level roll-up of the SAME movements
    the per-contract `ecl_provision_created`/`_increased`/`_released`/
    `_override_adjustment` events already post individually — posting both
    would double-count the provision) is itself visible and auditable in
    this table, not buried in code.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.accounting import AccountingEventType
from app.models.base import Base, by_value, utcnow


class AccountType(str, enum.Enum):
    asset = "ASSET"
    liability = "LIABILITY"
    equity = "EQUITY"
    income = "INCOME"
    expense = "EXPENSE"


class NormalBalance(str, enum.Enum):
    debit = "DEBIT"
    credit = "CREDIT"


class PostingSide(str, enum.Enum):
    debit = "DEBIT"
    credit = "CREDIT"


class EventClassification(str, enum.Enum):
    """The system's own, versioned, auditable answer to "does this event
    type ever produce a journal?" — see the module docstring's note on
    `ecl_provision_movement`."""

    postable = "POSTABLE"
    summary_only = "SUMMARY_ONLY"
    reserved = "RESERVED"


class AmountSource(str, enum.Enum):
    """Controlled vocabulary for `EventAccountMappingLine.amount_source` —
    never a free-text formula or arbitrary code (per the brief's explicit
    "no user-entered formulas" rule). Confirmed-resolvable set from the
    Checkpoint 0 code audit; the actual resolver functions are Checkpoint 2
    work (journal generation) — this checkpoint only fixes the vocabulary.

    Deliberately NOT included, with reasons (see BRD.md §4 "Missing source
    values"):
      * ``inventory_cost`` — no cost/COGS field exists anywhere on `Product`
        or `SalesOrder` in this platform. Seeding a line that needs it would
        mean fabricating a number; the brief explicitly forbids that.
      * a literal "cash selling price" source distinct from ``cash_price`` —
        ``cash_price`` (derived: `sale_price - total_profit`) already *is*
        the cash selling price; a second name for the same value would be
        redundant, not a second fact.
      * ``refund_amount`` as distinct from ``event_amount`` — every event
        that represents a refund (`cancellation`, `refund_completed`) already
        carries the exact refund figure as its own `AccountingEvent.amount`;
        no separate lookup is needed.
    """

    # the event's own single signed figure, already exactly right
    event_amount = "event_amount"
    absolute_event_amount = "absolute_event_amount"

    # contract_activated — derived from SalesOrder/Contract, never fabricated
    cash_price = "cash_price"
    down_payment = "down_payment"
    financed_principal = "financed_principal"
    total_contractual_profit = "total_contractual_profit"
    gross_installment_receivable = "gross_installment_receivable"

    # payment_received's component split (reserved for a future, finer-
    # grained mapping version — the seeded v1 mapping uses event_amount)
    payment_principal = "payment_principal"
    payment_profit = "payment_profit"
    payment_late_fee = "payment_late_fee"

    # early_settlement — resolved from LedgerEntry rows keyed to the
    # ContractClosure (reference_type="contract_closure"), already dual-
    # written by settle_contract()
    closure_ledger_principal = "closure_ledger_principal"
    closure_ledger_late_fee = "closure_ledger_late_fee"
    closure_ledger_profit_recognized = "closure_ledger_profit_recognized"
    closure_ledger_profit_rebated = "closure_ledger_profit_rebated"
    closure_ledger_payoff_total = "closure_ledger_payoff_total"

    # return — resolved from the dedicated return_* LedgerEntry rows added
    # alongside this feature (see services/closure.py::return_contract)
    closure_ledger_return_principal = "closure_ledger_return_principal"
    closure_ledger_return_late_fee = "closure_ledger_return_late_fee"
    closure_ledger_return_profit_retained = "closure_ledger_return_profit_retained"
    closure_ledger_return_profit_waived = "closure_ledger_return_profit_waived"

    # write-off — resolved directly from WriteOffExecution's own columns
    written_off_principal = "written_off_principal"
    written_off_profit = "written_off_profit"
    written_off_late_fee = "written_off_late_fee"
    provision_used = "provision_used"
    write_off_expense_excess = "write_off_expense_excess"


class ChartOfAccount(Base):
    """The account master. Every current row is a DEMO PLACEHOLDER — see
    `services/coa.py`'s seed function for the exact wording each row's own
    `description` carries.

    Controls (enforced in `services/coa.py`, not here):
      * `account_code` unique (DB constraint below).
      * `account_code` / `account_type` are immutable once the account has
        been referenced by any `EventAccountMappingLine` — before that,
        a proposed edit may still change them.
      * an account referenced by any mapping line can only be deactivated,
        never deleted — there is no delete endpoint anywhere for this table.
      * create / material edit / deactivate all go through the existing
        maker-checker framework (`ACTION_COA_ACCOUNT_*` in
        `models/approval.py`) — the row itself is only ever materialised at
        approval time (mirrors `ecl_config.py::activate_version()`'s own
        "nothing exists until approved" shape), so there is no "pending
        account" state to model here.
    """

    __tablename__ = "chart_of_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    account_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, native_enum=False, length=15, values_callable=by_value), nullable=False
    )
    normal_balance: Mapped[NormalBalance] = mapped_column(
        Enum(NormalBalance, native_enum=False, length=10, values_callable=by_value), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Every row seeded by this checkpoint is True. A real account, if this
    # platform is ever connected to Finance's approved chart, would be
    # proposed with is_demo=False through the exact same maker-checker path.
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class EventAccountMapping(Base):
    """One versioned posting rule for one `AccountingEventType`. Versioning
    is scoped PER event type (23 independent version lineages, not one
    global version) — otherwise identical in shape to
    `ecl_config.py::ECLConfiguration`'s own "clone, bump, deactivate the old
    one" mechanic: `is_active` is the only lifecycle flag (no separate
    pending/approved status column) because a row is only ever created at
    the moment its proposal is approved — a rejected proposal never
    materialises one (see `services/coa.py::propose_mapping_*`).

    Only one `is_active=True` row may exist per `account_event_type` at a
    time (enforced in the service layer; see the unique constraint below for
    the weaker DB-level guarantee that also stops two rows from claiming the
    exact same version number for one event type).
    """

    __tablename__ = "event_account_mappings"
    __table_args__ = (
        UniqueConstraint("account_event_type", "version", name="uq_mapping_event_type_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_event_type: Mapped[AccountingEventType] = mapped_column(
        Enum(AccountingEventType, native_enum=False, length=40), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[EventClassification] = mapped_column(
        Enum(EventClassification, native_enum=False, length=15, values_callable=by_value), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approval_request_id: Mapped[int | None] = mapped_column(ForeignKey("approval_requests.id"), nullable=True)

    lines: Mapped[list["EventAccountMappingLine"]] = relationship(
        back_populates="mapping", order_by="EventAccountMappingLine.line_sequence",
        cascade="all, delete-orphan",
    )


class EventAccountMappingLine(Base):
    """One debit or credit line of a mapping version. A `POSTABLE` mapping
    needs at least one `DEBIT` and one `CREDIT` line (enforced in the
    service layer at propose-time, and again at journal-generation time in
    Checkpoint 2); a `SUMMARY_ONLY` or `RESERVED` mapping has none."""

    __tablename__ = "event_account_mapping_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mapping_id: Mapped[int] = mapped_column(
        ForeignKey("event_account_mappings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    posting_side: Mapped[PostingSide] = mapped_column(
        Enum(PostingSide, native_enum=False, length=10, values_callable=by_value), nullable=False
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("chart_of_accounts.id"), nullable=False)
    amount_source: Mapped[AmountSource] = mapped_column(
        Enum(AmountSource, native_enum=False, length=45), nullable=False
    )
    multiplier: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("1"))
    # Only meaningful for a 2-line mapping whose source can be signed
    # (ecl_provision_released/_override_adjustment, settlement_difference):
    # a negative resolved amount uses abs(amount) and swaps this line's own
    # posting_side with its sibling's, rather than ever writing a negative
    # debit/credit. See BRD.md §5 "Sign & reversal handling".
    reverse_on_negative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    mapping: Mapped[EventAccountMapping] = relationship(back_populates="lines")
    account: Mapped[ChartOfAccount] = relationship()


class JournalStatus(str, enum.Enum):
    unmapped = "UNMAPPED"
    ready = "READY"
    posted = "POSTED"
    failed = "FAILED"


class GLJournal(Base):
    """The double-entry result of applying one `EventAccountMapping` version
    to one `AccountingEvent`. **Not populated by this checkpoint** — journal
    generation (amount-source resolution, balance validation, `emit()`
    integration) is Checkpoint 2. Defined now so the migration lands once,
    the same reasoning `models/write_off.py` used for `WriteOffExecution`.
    """

    __tablename__ = "gl_journals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    accounting_event_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_events.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    mapping_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_account_mappings.id"), nullable=True
    )
    journal_status: Mapped[JournalStatus] = mapped_column(
        Enum(JournalStatus, native_enum=False, length=15, values_callable=by_value),
        nullable=False, default=JournalStatus.unmapped, index=True,
    )
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    posting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "YYYY-MM" — a display/reporting grouping only in this checkpoint; no
    # period-close control exists anywhere in this platform yet (documented
    # future gap, see BRD.md §6/§18).
    accounting_period: Mapped[str | None] = mapped_column(String(7), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    total_debit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    is_balanced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    external_gl_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["GLJournalLine"]] = relationship(
        back_populates="journal", order_by="GLJournalLine.line_sequence",
        cascade="all, delete-orphan",
    )


class GLJournalLine(Base):
    """One debit or credit line of a posted/ready journal. `amount` is
    ALWAYS a positive magnitude — direction lives entirely in
    `posting_side` (non-negotiable design rule #8). `account_code_snapshot`/
    `account_name_snapshot` are copied at journal-creation time so a
    historical journal stays fully readable even after the account is later
    renamed or deactivated (non-negotiable design rule #7)."""

    __tablename__ = "gl_journal_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_id: Mapped[int] = mapped_column(
        ForeignKey("gl_journals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    posting_side: Mapped[PostingSide] = mapped_column(
        Enum(PostingSide, native_enum=False, length=10, values_callable=by_value), nullable=False
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("chart_of_accounts.id"), nullable=False)
    account_code_snapshot: Mapped[str] = mapped_column(String(20), nullable=False)
    account_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    amount_source: Mapped[AmountSource] = mapped_column(
        Enum(AmountSource, native_enum=False, length=45), nullable=False
    )
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("installment_contracts.id"), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    journal: Mapped[GLJournal] = relationship(back_populates="lines")
    account: Mapped[ChartOfAccount] = relationship()
