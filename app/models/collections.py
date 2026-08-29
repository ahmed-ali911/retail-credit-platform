from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column, utcnow


class CollectionCaseStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class CollectionActivityType(str, enum.Enum):
    call = "call"
    sms = "sms"
    email = "email"
    visit = "visit"
    promise_to_pay = "promise_to_pay"
    other = "other"


class PromiseStatus(str, enum.Enum):
    pending = "pending"
    kept = "kept"
    broken = "broken"


class CollectionCase(Base):
    """Operational collections case for a contract with overdue installments.

    At most one *open* case per contract — enforced by a partial unique index
    and re-checked in the service layer.
    """

    __tablename__ = "collection_cases"
    __table_args__ = (
        Index(
            "uq_open_collection_case_per_contract",
            "contract_id",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[CollectionCaseStatus] = mapped_column(
        Enum(CollectionCaseStatus, native_enum=False, length=20),
        default=CollectionCaseStatus.open,
        nullable=False,
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    opened_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
    activities: Mapped[list["CollectionActivity"]] = relationship(
        back_populates="case",
        order_by="CollectionActivity.created_at",
        cascade="all, delete-orphan",
    )


class CollectionActivity(Base):
    __tablename__ = "collection_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_case_id: Mapped[int] = mapped_column(
        ForeignKey("collection_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    activity_type: Mapped[CollectionActivityType] = mapped_column(
        Enum(CollectionActivityType, native_enum=False, length=20), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    # Only meaningful when activity_type == promise_to_pay.
    promised_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    promised_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    promise_status: Mapped[PromiseStatus | None] = mapped_column(
        Enum(PromiseStatus, native_enum=False, length=20), nullable=True
    )

    case: Mapped[CollectionCase] = relationship(back_populates="activities")
    user: Mapped["User"] = relationship()  # noqa: F821
