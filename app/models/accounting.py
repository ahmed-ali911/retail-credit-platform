"""Accounting-event boundary (fills Gap Matrix G-07 / assessment §22).

This is **not** a general ledger. Every financial event this platform already
knows about is turned into a structured, postable `AccountingEvent` — the same
mock-adapter boundary already used for the payment gateway and the bank feed.

Confirmed principle: events are generated automatically and additively from
things that already happen; they never change existing business behaviour. The
downstream *posting* of an event to a real ERP/GL is recoverable and retryable
and is never a blocker for the business action that produced it.

Still open (BUSINESS DECISION REQUIRED): the chart-of-accounts / debit-credit
mapping per `event_type`. This model deliberately carries only a single signed
`amount` — the double-entry breakdown is whatever Finance eventually confirms,
applied by the real `GlProvider` adapter, not here.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class AccountingEventType(str, enum.Enum):
    contract_activated = "contract_activated"
    down_payment_received = "down_payment_received"
    payment_received = "payment_received"
    profit_recognized = "profit_recognized"
    late_fee_charged = "late_fee_charged"
    late_fee_waived = "late_fee_waived"
    early_settlement = "early_settlement"
    cancellation = "cancellation"
    return_ = "return"          # value "return"; matches ClosureReason.return_
    contract_closed = "contract_closed"  # normal full-repayment closure (bug fix)


class AccountingStatus(str, enum.Enum):
    pending = "pending"     # created, not yet handed to the ERP adapter
    posted = "posted"       # the ERP adapter accepted it (external_gl_reference set)
    failed = "failed"       # the ERP adapter rejected it; retry_count incremented


class AccountingEvent(Base):
    """One postable financial event.

    ``event_reference`` is the idempotency key — a deterministic string derived
    from the row that caused the event (e.g. ``payment-received-42``). The unique
    constraint is what makes every hook safe to fire twice.

    ``amount`` is a single signed figure, same convention as
    ``ContractClosure.financial_adjustment`` where a sign is meaningful
    (positive = owed to / received from the customer's side as recorded). The
    real debit/credit split is the unconfirmed chart-of-accounts mapping and is
    applied by the ERP adapter, not stored here.
    """

    __tablename__ = "accounting_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[AccountingEventType] = mapped_column(
        Enum(AccountingEventType, native_enum=False, length=30),
        nullable=False,
        index=True,
    )
    event_reference: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="KWD", server_default="KWD"
    )
    event_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    accounting_status: Mapped[AccountingStatus] = mapped_column(
        Enum(AccountingStatus, native_enum=False, length=20),
        default=AccountingStatus.pending,
        server_default=AccountingStatus.pending.value,
        nullable=False,
        index=True,
    )
    external_gl_reference: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
