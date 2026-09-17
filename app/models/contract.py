from __future__ import annotations

import enum
from decimal import Decimal
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column

_ZERO = Decimal("0.00")


class ContractStatus(str, enum.Enum):
    created = "created"   # contract exists, product not yet delivered
    active = "active"     # delivered — schedule is live
    closed = "closed"     # ended — see the contract's ContractClosure for the reason


class InstallmentStatus(str, enum.Enum):
    pending = "pending"
    partially_paid = "partially_paid"
    overdue = "overdue"          # past due_date and not fully paid (Step 3)
    paid = "paid"
    # Write-off & Recovery feature — set ONLY by write-off execution, never by
    # the payment engine (`payments.py::_update_installment_status` never
    # produces this value). Distinguishes "this balance is gone because it
    # was formally written off" from "this balance is gone because it was
    # paid" — both zero out principal_outstanding/profit_outstanding, but the
    # financial story is completely different.
    written_off = "written_off"


class InstallmentContract(Base):
    """*How the sale is financed* — tenor, profit, and unearned-profit balance.

    Kept separate from SalesOrder on purpose (what was sold vs. how it is paid).
    """

    __tablename__ = "installment_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=False, unique=True
    )

    tenor_months: Mapped[int] = mapped_column(Integer, nullable=False)
    total_profit: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    # Total profit not yet recognised. Decremented as each installment's profit
    # component is paid (payment processing arrives in the next step).
    unearned_profit_balance: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)

    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, native_enum=False, length=20),
        default=ContractStatus.created,
        nullable=False,
    )
    created_at: Mapped[datetime] = created_at_column()
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sales_order: Mapped["SalesOrder"] = relationship(back_populates="contract")  # noqa: F821
    schedule: Mapped["PaymentSchedule"] = relationship(
        back_populates="contract", uselist=False, cascade="all, delete-orphan"
    )
    installments: Mapped[list["Installment"]] = relationship(
        back_populates="contract",
        order_by="Installment.sequence_number",
        cascade="all, delete-orphan",
    )
    payments: Mapped[list["Payment"]] = relationship(  # noqa: F821
        back_populates="contract",
        order_by="Payment.received_at",
        cascade="all, delete-orphan",
    )
    late_fee_charges: Mapped[list["LateFeeCharge"]] = relationship(  # noqa: F821
        back_populates="contract",
        order_by="LateFeeCharge.assessed_at",
        cascade="all, delete-orphan",
    )
    closure: Mapped["ContractClosure | None"] = relationship(  # noqa: F821
        back_populates="contract", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def is_closed(self) -> bool:
        return self.status == ContractStatus.closed


class PaymentSchedule(Base):
    """Header for the set of installments generated from a contract."""

    __tablename__ = "payment_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = created_at_column()

    contract: Mapped[InstallmentContract] = relationship(back_populates="schedule")
    installments: Mapped[list["Installment"]] = relationship(
        back_populates="schedule",
        order_by="Installment.sequence_number",
        cascade="all, delete-orphan",
    )


class Installment(Base):
    __tablename__ = "installments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("payment_schedules.id", ondelete="CASCADE"), nullable=False
    )

    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    principal_component: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    profit_component: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # How much of each component has been settled by allocated payments (Step 3).
    principal_paid: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO, server_default="0"
    )
    profit_paid: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO, server_default="0"
    )
    # --- Write-off & Recovery feature ---
    # Running totals, same shape as principal_paid/profit_paid, set ONLY by
    # write-off execution (services/write_off.py) — never by the payment
    # engine. Netted out of principal_outstanding/profit_outstanding below,
    # which is what makes a written-off installment automatically invisible
    # to build_receivable(), the ECL EAD calc, exposure, and the payment
    # allocation waterfall (allocation.py already skips any installment whose
    # outstanding is <= 0 — the exact mechanism that already protects a
    # fully-paid installment from re-allocation) — no changes needed in any
    # of those modules.
    principal_written_off: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO, server_default="0"
    )
    profit_written_off: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=_ZERO, server_default="0"
    )
    status: Mapped[InstallmentStatus] = mapped_column(
        Enum(InstallmentStatus, native_enum=False, length=20),
        default=InstallmentStatus.pending,
        nullable=False,
    )

    contract: Mapped[InstallmentContract] = relationship(back_populates="installments")
    schedule: Mapped[PaymentSchedule] = relationship(back_populates="installments")
    late_fee_charges: Mapped[list["LateFeeCharge"]] = relationship(  # noqa: F821
        back_populates="installment",
        order_by="LateFeeCharge.assessed_at",
        cascade="all, delete-orphan",
    )

    @property
    def total_due(self) -> Decimal:
        return _d(self.principal_component) + _d(self.profit_component)

    @property
    def principal_outstanding(self) -> Decimal:
        return (
            _d(self.principal_component) - _d(self.principal_paid) - _d(self.principal_written_off)
        )

    @property
    def profit_outstanding(self) -> Decimal:
        return _d(self.profit_component) - _d(self.profit_paid) - _d(self.profit_written_off)

    @property
    def is_written_off(self) -> bool:
        return _d(self.principal_written_off) > _ZERO or _d(self.profit_written_off) > _ZERO

    @property
    def late_fee_outstanding(self) -> Decimal:
        return sum(
            (c.outstanding for c in self.late_fee_charges), _ZERO
        )

    @property
    def is_fully_paid(self) -> bool:
        # Installment lifecycle tracks principal + profit. Late fees are a
        # separate ledger (LateFeeCharge.status), though the allocation
        # waterfall settles an installment's late fee before its profit
        # anyway, so a 'paid' installment has no outstanding fee in practice.
        return (
            self.principal_outstanding <= _ZERO and self.profit_outstanding <= _ZERO
        )

    @property
    def has_any_payment(self) -> bool:
        return _d(self.principal_paid) > _ZERO or _d(self.profit_paid) > _ZERO


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))
