"""Mock Payment Gateway — daily settlement batch reconciliation.

Reuse decision (confirmed with the user before this was written): this is a
genuinely DISTINCT process from the existing bank-reconciliation module
(``models/reconciliation.py`` — ``BankStatementLine`` / ``ReconciliationException``),
not a duplicate of it:

  * different external actor — the mock-payment-gateway service's own daily
    settlement feed, vs. the company's bank statement;
  * different granularity — a whole *batch* with gross/fee/net totals, vs.
    one bank line per transfer;
  * a richer, gateway-specific exception taxonomy (MATCHED /
    MISSING_IN_GATEWAY / MISSING_IN_INTERNAL_SYSTEM / AMOUNT_MISMATCH /
    STATUS_MISMATCH / DUPLICATE / DATE_MISMATCH / UNRESOLVED) that the bank
    module's three reasons don't express.

New models, but it deliberately REUSES the existing generic maker-checker
engine (``services/approvals.py`` — a new ``ACTION_GATEWAY_RECON_RESOLVE``
action type, not a second approval system) and the existing
``services/audit.py::record_event`` — see ``services/gateway_reconciliation.py``.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, by_value, utcnow


class ReconciliationOutcome(str, enum.Enum):
    matched = "MATCHED"
    missing_in_gateway = "MISSING_IN_GATEWAY"
    missing_in_internal_system = "MISSING_IN_INTERNAL_SYSTEM"
    amount_mismatch = "AMOUNT_MISMATCH"
    status_mismatch = "STATUS_MISMATCH"
    duplicate = "DUPLICATE"
    date_mismatch = "DATE_MISMATCH"
    unresolved = "UNRESOLVED"


class GatewayReconciliationItemStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"


class SettlementBatch(Base):
    """One daily settlement feed imported from the mock-payment-gateway's own
    ``GET /gateway/settlement-batches/generate`` (or an equivalent manual
    upload) — this app never reaches into the gateway's database directly."""

    __tablename__ = "settlement_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_reference: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    gateway_name: Mapped[str] = mapped_column(
        String(50), nullable=False, default="mock-payment-gateway"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_gateway_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    imported_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    items: Mapped[list["ReconciliationItem"]] = relationship(
        back_populates="batch", order_by="ReconciliationItem.id"
    )


class ReconciliationItem(Base):
    """One line-level comparison between the gateway's settlement feed and
    this app's own internal PaymentIntent/GatewayTransaction/Payment records.

    ``settlement_batch_id`` is nullable: a MISSING_IN_GATEWAY item is
    synthesised from OUR OWN settled records for a date, not from any row in
    the gateway's feed — there is, by definition, no batch line to point at.
    """

    __tablename__ = "gateway_reconciliation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    settlement_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("settlement_batches.id", ondelete="CASCADE"), nullable=True, index=True
    )
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    gateway_transaction_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    merchant_reference: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    gateway_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    gateway_reported_status: Mapped[str | None] = mapped_column(String(25), nullable=True)

    outcome: Mapped[ReconciliationOutcome] = mapped_column(
        Enum(ReconciliationOutcome, native_enum=False, length=30, values_callable=by_value),
        nullable=False,
    )
    status: Mapped[GatewayReconciliationItemStatus] = mapped_column(
        Enum(GatewayReconciliationItemStatus, native_enum=False, length=20),
        default=GatewayReconciliationItemStatus.open,
        nullable=False,
        index=True,
    )
    matched_payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id"), nullable=True
    )
    matched_intent_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_intents.id"), nullable=True
    )
    variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    resolution_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolution_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    batch: Mapped[SettlementBatch | None] = relationship(back_populates="items")
