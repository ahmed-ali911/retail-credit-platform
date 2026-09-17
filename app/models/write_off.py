"""Write-off & Recovery — domain model.

Write-off is a SEPARATE, controlled business decision — never a side effect
of default/Stage 3/cancellation/settlement (see the design note at the top
of ``services/write_off.py``). This module deliberately reuses everything it
can rather than building parallel machinery:

  * the generic maker-checker (``ApprovalRequest`` / ``services/approvals.py``,
    a new ``ACTION_WRITE_OFF_REQUEST`` action type — see ``models/approval.py``);
  * the existing accounting-event boundary (``models/accounting.py``);
  * the existing audit trail (``services/audit.py::record_event``);
  * the existing ledger dual-write pattern (``models/ledger.py``);
  * the existing Contract/Installment/LateFeeCharge tables, extended
    additively (``principal_written_off`` / ``profit_written_off`` /
    ``amount_written_off`` columns, new terminal status values) rather than
    a parallel balance model.

Three tables, mirroring the Mock Payment Gateway's own Intent -> Transaction
-> (append-only child) shape:

  * ``WriteOffRequest``  — the maker-checker-gated record. Everything about
    the *request* (why, how much, the system's own eligibility verdict, an
    immutable snapshot of the account at request time) lives here, and
    stays exactly as it was even if the account's real balances move before
    a decision is made.
  * ``WriteOffExecution`` — created ONLY once an approved request is
    EXECUTED (a later checkpoint — this table is defined now, as part of
    the domain model, but nothing writes to it yet). One-to-one with an
    approved ``WriteOffRequest``. The immutable financial record: what was
    actually written off, by component, and what ECL/provision looked like
    at that exact moment.
  * ``Recovery`` — one immutable, append-only row per recovery payment
    against a ``WriteOffExecution`` (a later checkpoint — defined now for
    the same reason). Never edits ``WriteOffExecution``'s own amounts; the
    original write-off, the sum of recoveries, and the remaining balance
    are always three separately reconstructable numbers (see §14 of the
    brief this module implements).
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
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

from app.models.base import Base, by_value, utcnow


class WriteOffType(str, enum.Enum):
    full = "FULL"
    partial = "PARTIAL"


class WriteOffRequestStatus(str, enum.Enum):
    pending = "PENDING"
    approved = "APPROVED"      # checker-approved; not yet financially executed
    rejected = "REJECTED"
    executed = "EXECUTED"      # financially applied (future checkpoint) — see WriteOffExecution
    cancelled = "CANCELLED"    # withdrawn before a decision, or superseded — mirrors ECLOverride's
                                # own CANCELLED semantics ("withdrawn / superseded")


class WriteOffEligibilityStatus(str, enum.Enum):
    """The system's own computed verdict — see services/write_off.py's
    ``evaluate_eligibility``. Distinct from ``WriteOffRequestStatus``: this is
    what the ENGINE concluded, not what a human decided to do about it."""

    eligible = "ELIGIBLE"
    not_eligible = "NOT_ELIGIBLE"
    # At least one gating indicator could not be evaluated (e.g. no ECL
    # assessment exists yet for this contract) — genuinely unknown, not a
    # "no". Also the status used for anything gated purely on data this
    # platform doesn't capture (legal / dispute / restructuring status).
    unavailable = "UNAVAILABLE"


class WriteOffReasonCode(str, enum.Enum):
    """Mirrors ECLOverrideReasonCode's exact shape (SCREAMING_CASE values,
    lowercase Pythonic names, an OTHER catch-all) for consistency with the
    most recent, most similar maker-checker feature in this codebase."""

    collections_exhausted = "COLLECTIONS_EXHAUSTED"
    customer_financial_difficulty = "CUSTOMER_FINANCIAL_DIFFICULTY"
    unlikely_to_recover = "UNLIKELY_TO_RECOVER"
    legal_outcome = "LEGAL_OUTCOME"
    deceased_customer = "DECEASED_CUSTOMER"
    insolvency = "INSOLVENCY"
    management_decision = "MANAGEMENT_DECISION"
    other = "OTHER"


class WriteOffRequest(Base):
    """The maker-checker-gated write-off request.

    ``eligibility_status`` / ``eligibility_snapshot`` are the SYSTEM's
    verdict, computed once at request time and never recomputed in place —
    the account's real DPD/stage/collections state can keep moving; this
    row stays a faithful record of what was true when the request was made.

    Per the confirmed design: a NOT_ELIGIBLE or UNAVAILABLE verdict does not
    outright block a request — it forces the ``is_exception`` path, which
    requires ``exception_justification`` and is still subject to the exact
    same maker-checker approval as a normal request (services/write_off.py
    enforces this; the column is nullable here only because a normal,
    ELIGIBLE-verdict request never populates it).
    """

    __tablename__ = "write_off_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)

    write_off_type: Mapped[WriteOffType] = mapped_column(
        Enum(WriteOffType, native_enum=False, length=10, values_callable=by_value), nullable=False
    )
    status: Mapped[WriteOffRequestStatus] = mapped_column(
        Enum(WriteOffRequestStatus, native_enum=False, length=15, values_callable=by_value),
        default=WriteOffRequestStatus.pending,
        nullable=False,
        index=True,
    )

    reason_code: Mapped[WriteOffReasonCode] = mapped_column(
        Enum(WriteOffReasonCode, native_enum=False, length=35, values_callable=by_value), nullable=False
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- eligibility (system-computed, immutable once stamped) -----------
    eligibility_status: Mapped[WriteOffEligibilityStatus] = mapped_column(
        Enum(WriteOffEligibilityStatus, native_enum=False, length=15, values_callable=by_value),
        nullable=False,
    )
    # [{id, name, category, result: "satisfied"|"not_satisfied"|"unavailable", detail}]
    eligibility_snapshot: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_exception: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Mandatory (enforced in services/write_off.py) whenever is_exception is
    # True — the human rationale for proceeding despite a NOT_ELIGIBLE or
    # UNAVAILABLE system verdict. Preserved historically alongside, never
    # instead of, the system's own verdict above.
    exception_justification: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- account snapshot at request time (never recomputed in place) ----
    snapshot_principal_outstanding: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    snapshot_profit_outstanding: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    snapshot_late_fee_outstanding: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Modelled for completeness (§4 of the brief) — this platform has no
    # "other charges" balance anywhere, so this is always 0.00, never
    # fabricated; see services/write_off.py.
    snapshot_other_charges_outstanding: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    snapshot_total_outstanding: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    snapshot_dpd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_ecl_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_ecl_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    snapshot_provision_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    snapshot_collections_case_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    snapshot_collections_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_cases.id"), nullable=True
    )

    # --- requested amounts by component -----------------------------------
    # For a FULL request these are set equal to the snapshot outstanding
    # amounts (computed, not typed in) — see services/write_off.py.
    requested_principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    requested_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    requested_late_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    requested_other_charges: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    approval_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=True
    )
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
    execution: Mapped["WriteOffExecution | None"] = relationship(
        back_populates="request", uselist=False
    )

    @property
    def requested_total(self) -> Decimal:
        return (
            (self.requested_principal or 0)
            + (self.requested_profit or 0)
            + (self.requested_late_fee or 0)
            + (self.requested_other_charges or 0)
        )


class WriteOffExecution(Base):
    """The immutable financial record of an EXECUTED write-off.

    Not populated by this checkpoint — ``services/write_off.py`` only
    creates ``WriteOffRequest`` rows and drives them to PENDING -> APPROVED
    / REJECTED. The execution step (this table's writer) is a later
    checkpoint by explicit instruction; the table is defined now so the
    migration lands once, with the rest of the domain model.
    """

    __tablename__ = "write_off_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    write_off_request_id: Mapped[int] = mapped_column(
        ForeignKey("write_off_requests.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    write_off_type: Mapped[WriteOffType] = mapped_column(
        Enum(WriteOffType, native_enum=False, length=10, values_callable=by_value), nullable=False
    )

    executed_principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    executed_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    executed_late_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    executed_other_charges: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    # Remaining ordinarily-collectible balance immediately after execution —
    # 0 for a FULL write-off; the un-written-off remainder for a PARTIAL one.
    remaining_principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    remaining_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    remaining_late_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    # ECL/provision re-snapshotted AT EXECUTION time (may differ from the
    # request-time snapshot if time passed during approval).
    ecl_stage_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ecl_amount_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    provision_amount_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)

    contract_closure_id: Mapped[int | None] = mapped_column(
        ForeignKey("contract_closures.id"), nullable=True
    )
    collection_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_cases.id"), nullable=True
    )
    accounting_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_events.id"), nullable=True
    )

    executed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    request: Mapped[WriteOffRequest] = relationship(back_populates="execution")
    recoveries: Mapped[list["Recovery"]] = relationship(
        back_populates="write_off_execution", order_by="Recovery.id"
    )

    @property
    def total_written_off(self) -> Decimal:
        return (
            (self.executed_principal or 0)
            + (self.executed_profit or 0)
            + (self.executed_late_fee or 0)
            + (self.executed_other_charges or 0)
        )


class Recovery(Base):
    """One immutable, append-only recovery payment against a
    ``WriteOffExecution``. Never mutates ``WriteOffExecution``'s own
    amounts — "original write-off", "total recovered" (the sum of these
    rows) and "remaining" are always three independently reconstructable
    figures (§14 of the brief).

    Not populated by this checkpoint — see the ``WriteOffExecution``
    docstring; defined now for the same reason.

    Per the confirmed design: ``external_reference`` is MANDATORY — a
    recovery must always be traceable to a real payment/cash source, never
    an unexplained arbitrary amount. ``payment_id`` is an OPTIONAL link for
    the (expected to be rare) case where the recovery genuinely came through
    this app's own Payment record; most recoveries, since the underlying
    installments are already written off and hence invisible to the normal
    payment/allocation engine, will only ever have the external reference.
    """

    __tablename__ = "write_off_recoveries"
    __table_args__ = (
        UniqueConstraint(
            "write_off_execution_id", "external_reference", name="uq_recovery_execution_reference"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    write_off_execution_id: Mapped[int] = mapped_column(
        ForeignKey("write_off_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    external_reference: Mapped[str] = mapped_column(String(120), nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    recovery_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Allocation across written-off components — policy is FINANCE/BUSINESS
    # DECISION REQUIRED (see services/write_off.py once recovery lands);
    # these columns exist now so the allocator has somewhere to write.
    allocated_principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    allocated_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    allocated_late_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    allocated_other_charges: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    accounting_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_events.id"), nullable=True
    )
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    write_off_execution: Mapped[WriteOffExecution] = relationship(back_populates="recoveries")
