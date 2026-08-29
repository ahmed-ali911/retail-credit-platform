from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class ClosureReason(str, enum.Enum):
    normal = "normal"                   # reached maturity (not produced this step)
    early_settlement = "early_settlement"
    cancellation = "cancellation"       # pre-delivery
    return_ = "return"                  # post-delivery


class ContractClosure(Base):
    """Exactly one per contract. A contract is never marked ``closed`` without one.

    ``financial_adjustment`` is signed **from the customer's point of view**:
      * positive  -> net cash owed TO the customer (a refund / rebate paid out)
      * negative  -> net cash the customer still owes the company
      * NULL      -> no monetary adjustment recorded (e.g. a plain early
                     settlement, where the payoff is collected via /settle and
                     the detail lives on the settlement quote)
    """

    __tablename__ = "contract_closures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    reason: Mapped[ClosureReason] = mapped_column(
        Enum(ClosureReason, native_enum=False, length=20), nullable=False
    )
    financial_adjustment: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract: Mapped["InstallmentContract"] = relationship(  # noqa: F821
        back_populates="closure"
    )
