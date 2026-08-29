from __future__ import annotations

import enum

from sqlalchemy import Boolean, Enum, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductCategory(str, enum.Enum):
    electronics = "electronics"
    appliances = "appliances"
    furniture = "furniture"
    automotive = "automotive"
    other = "other"


class Product(Base):
    """Minimal product record for Step 1.

    Only Cash Price exists. Installment Sale Price, profit/margin and
    amortization are intentionally NOT modelled yet — that pricing
    methodology is still an open business decision.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[ProductCategory] = mapped_column(
        Enum(ProductCategory, native_enum=False, length=20),
        default=ProductCategory.other,
        nullable=False,
    )
    cash_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    installment_eligible: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
