from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class ReconExceptionReason(str, enum.Enum):
    no_match = "no_match"
    amount_mismatch = "amount_mismatch"       # reference matched, amount didn't
    duplicate_candidate = "duplicate_candidate"  # 2+ payments could match


class ReconExceptionStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"


class BankStatementLine(Base):
    """One line from the company's bank statement.

    Mock adapter boundary — there is no real bank feed. Lines are recorded one at
    a time via `POST /reconciliation/bank-lines`, standing in for a future import.
    """

    __tablename__ = "bank_statement_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bank_reference: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    matched_payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id"), nullable=True
    )

    matched_payment: Mapped["Payment | None"] = relationship()  # noqa: F821


class ReconciliationException(Base):
    """A bank line the matching engine could not auto-reconcile."""

    __tablename__ = "reconciliation_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bank_line_id: Mapped[int] = mapped_column(
        ForeignKey("bank_statement_lines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason: Mapped[ReconExceptionReason] = mapped_column(
        Enum(ReconExceptionReason, native_enum=False, length=25), nullable=False
    )
    status: Mapped[ReconExceptionStatus] = mapped_column(
        Enum(ReconExceptionStatus, native_enum=False, length=20),
        default=ReconExceptionStatus.open,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    bank_line: Mapped[BankStatementLine] = relationship()
