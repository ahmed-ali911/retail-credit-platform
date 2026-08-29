from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column


class OfferStatus(str, enum.Enum):
    presented = "presented"
    accepted = "accepted"
    expired = "expired"


class InstallmentOffer(Base):
    """A priced installment proposal generated from an approved application.

    Holds a frozen snapshot of the pricing computation plus a schedule preview,
    so what the customer accepted is exactly what becomes the contract.
    """

    __tablename__ = "installment_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id"), nullable=False, index=True
    )

    cash_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    down_payment: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    tenor_months: Mapped[int] = mapped_column(Integer, nullable=False)
    profit_rate: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)

    installment_sale_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    total_profit: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    amount_financed: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)

    # list of {sequence_number, principal_component, profit_component, total}
    schedule_preview: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus, native_enum=False, length=20),
        default=OfferStatus.presented,
        nullable=False,
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    # Down-payment collection record (stubbed — no real gateway this step).
    down_payment_confirmed: Mapped[bool] = mapped_column(default=False, nullable=False)
    down_payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    application: Mapped["CreditApplication"] = relationship()  # noqa: F821
