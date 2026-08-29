from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column


class ContractStatus(str, enum.Enum):
    created = "created"   # contract exists, product not yet delivered
    active = "active"     # delivered — schedule is live
    closed = "closed"     # future step


class InstallmentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    partially_paid = "partially_paid"


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
    principal_component: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    profit_component: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[InstallmentStatus] = mapped_column(
        Enum(InstallmentStatus, native_enum=False, length=20),
        default=InstallmentStatus.pending,
        nullable=False,
    )

    contract: Mapped[InstallmentContract] = relationship(back_populates="installments")
    schedule: Mapped[PaymentSchedule] = relationship(back_populates="installments")

    @property
    def total_due(self) -> float:
        return (self.principal_component or 0) + (self.profit_component or 0)
