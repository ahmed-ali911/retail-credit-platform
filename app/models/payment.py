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


class LateFeeStatus(str, enum.Enum):
    assessed = "assessed"
    waived = "waived"          # future maker-checker endpoint
    paid = "paid"


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
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, length=20), nullable=False
    )
    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
    )
    unallocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO
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
        return (self.amount or _ZERO) - (self.amount_paid or _ZERO)
