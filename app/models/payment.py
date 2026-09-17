from __future__ import annotations

import enum
from decimal import Decimal
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column, utcnow

_ZERO = Decimal("0.00")


class PaymentStatus(str, enum.Enum):
    applied = "applied"        # the whole payment was allocated
    overpaid = "overpaid"      # some of the payment had nothing left to settle
    reversed = "reversed"      # Mock Payment Gateway feature — a settled gateway
                                # payment whose money was taken back (REVERSED/
                                # REFUNDED/CHARGEBACK); its allocations were undone
                                # by compensating PaymentAllocation rows, never
                                # deleted or overwritten.


class PaymentSource(str, enum.Enum):
    """Mock Payment Gateway feature — who/what caused this Payment row to be
    created. `staff` is the pre-existing behaviour (POST /contracts/{id}/payments,
    unchanged); `gateway` is a payment that only exists because a PaymentIntent
    reached the configured final-allocation status (SETTLED by default) via a
    verified gateway webhook — see services/payment_intents.py."""

    staff = "staff"
    gateway = "gateway"


class PaymentReconciliationStatus(str, enum.Enum):
    """Whether this payment has been matched against the company's bank records.

    Separate from allocation: a payment can be fully allocated to installments
    and still be `unreconciled`. Set by the P0-5 reconciliation engine, never by
    payment recording. Default `unreconciled` for all existing and new payments.
    """

    unreconciled = "unreconciled"
    reconciled = "reconciled"
    exception = "exception"   # flagged during matching, needs manual attention


class LateFeeStatus(str, enum.Enum):
    assessed = "assessed"
    waived = "waived"          # future maker-checker endpoint
    paid = "paid"
    # Write-off & Recovery feature — distinct from `waived`: a waiver is a
    # goodwill/service decision (existing ACTION_LATE_FEE_WAIVE maker-checker,
    # emits late_fee_waived); a write-off is "deemed uncollectible" (the new
    # write-off maker-checker, emits write_off_executed). Same shape as
    # `waived` (outstanding -> 0) but a different business fact and a
    # different accounting event, so it needs its own status rather than
    # reusing `waived`.
    written_off = "written_off"


class Payment(Base):
    """A payment recorded against a contract (manual / API — no gateway yet).

    ``external_reference`` is the client-supplied idempotency key, unique per
    contract: replaying it returns the original allocation instead of
    processing again.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("contract_id", "external_reference", name="uq_payment_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    # A future real gateway's own transaction id — distinct from the merchant-side
    # external_reference (which stays the idempotency key). Nullable; unused today.
    gateway_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, length=20), nullable=False
    )
    reconciliation_status: Mapped[PaymentReconciliationStatus] = mapped_column(
        Enum(PaymentReconciliationStatus, native_enum=False, length=20),
        default=PaymentReconciliationStatus.unreconciled,
        server_default=PaymentReconciliationStatus.unreconciled.value,
        nullable=False,
    )
    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
    )
    unallocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
    )
    # --- Mock Payment Gateway feature ---
    source: Mapped[PaymentSource] = mapped_column(
        Enum(PaymentSource, native_enum=False, length=10),
        default=PaymentSource.staff,
        server_default=PaymentSource.staff.value,
        nullable=False,
    )
    # Set only for source=gateway — the PaymentIntent whose SETTLED webhook
    # created this row. Nullable so every pre-existing staff payment (and the
    # staff-entered path going forward) is completely untouched.
    payment_intent_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_intents.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = created_at_column()

    contract: Mapped["InstallmentContract"] = relationship(  # noqa: F821
        back_populates="payments"
    )
    allocations: Mapped[list["PaymentAllocation"]] = relationship(
        back_populates="payment",
        order_by="PaymentAllocation.id",
        cascade="all, delete-orphan",
    )


class PaymentAllocation(Base):
    """Audit row: how one payment was split against one installment."""

    __tablename__ = "payment_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"), nullable=False
    )
    installment_id: Mapped[int] = mapped_column(
        ForeignKey("installments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    late_fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
    )
    profit_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
    )
    principal_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
    )
    # --- Mock Payment Gateway feature: reversal via compensating record ---
    # Set on the ORIGINAL row once it has been reversed, pointing at the NEW
    # row created to undo it (negative late_fee/profit/principal amounts that
    # net the original back to zero). The original row is never edited beyond
    # this one pointer, never deleted — full history stays intact. NULL on
    # every pre-existing row and on a compensating row itself (a reversal is
    # not itself reversed in this model).
    reversed_by_allocation_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_allocations.id"), nullable=True
    )

    payment: Mapped[Payment] = relationship(back_populates="allocations")

    @property
    def total(self) -> Decimal:
        return self.late_fee_amount + self.profit_amount + self.principal_amount


class LateFeeCharge(Base):
    """A late fee assessed on a single overdue installment.

    Deliberately its own table — a late fee is NOT profit and is never folded
    into ``profit_component`` or ``unearned_profit_balance``.
    """

    __tablename__ = "late_fee_charges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installment_id: Mapped[int] = mapped_column(
        ForeignKey("installments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
    )
    # Write-off & Recovery feature — mirrors amount_paid's shape.
    amount_written_off: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO, server_default="0"
    )
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    status: Mapped[LateFeeStatus] = mapped_column(
        Enum(LateFeeStatus, native_enum=False, length=20),
        default=LateFeeStatus.assessed,
        nullable=False,
    )

    installment: Mapped["Installment"] = relationship(  # noqa: F821
        back_populates="late_fee_charges"
    )
    contract: Mapped["InstallmentContract"] = relationship(  # noqa: F821
        back_populates="late_fee_charges"
    )

    @property
    def outstanding(self) -> Decimal:
        if self.status == LateFeeStatus.waived:
            return _ZERO
        return (
            (self.amount or _ZERO)
            - (self.amount_paid or _ZERO)
            - (self.amount_written_off or _ZERO)
        )
