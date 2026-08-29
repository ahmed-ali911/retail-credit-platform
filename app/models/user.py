from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column


class UserRole(str, enum.Enum):
    admin = "admin"
    credit_officer = "credit_officer"
    credit_manager = "credit_manager"
    sales_employee = "sales_employee"
    finance_officer = "finance_officer"
    customer = "customer"
    collections_officer = "collections_officer"  # Step 6


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=20), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()
