from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column


class CustomerStatus(str, enum.Enum):
    active = "Active"
    inactive = "Inactive"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # National ID or equivalent identifier (Civil ID, passport, ...).
    national_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[CustomerStatus] = mapped_column(
        Enum(CustomerStatus, native_enum=False, length=20),
        default=CustomerStatus.active,
        nullable=False,
    )

    # Stubbed until a real credit-bureau integration exists. Manually set.
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Links this Customer to a login (role='customer') for ownership checks.
    # Nullable and set manually / via helper for now — no self-service signup.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = created_at_column()

    user: Mapped["User | None"] = relationship()  # noqa: F821
    profile: Mapped["CustomerProfile | None"] = relationship(
        back_populates="customer", uselist=False, cascade="all, delete-orphan"
    )


class CustomerProfile(Base):
    """Kept as a separate entity from Customer on purpose.

    Customer = identity + relationship status.
    CustomerProfile = the financial / KYC picture used for assessment.
    """

    __tablename__ = "customer_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    employer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    monthly_income: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    existing_monthly_obligations: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )

    address_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="profile")
