from __future__ import annotations

import enum

from sqlalchemy import Boolean, Enum, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Placeholder default stock for a brand-new / backfilled product. Mirrors the
# `default_initial_stock_quantity` config parameter — both are clearly fictitious.
DEFAULT_STOCK_FALLBACK = 10


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

    # --- Step 10: minimal stock tracking --------------------------------------
    # Units physically held. A brand-new product is seeded from the
    # `default_initial_stock_quantity` config value; existing rows are backfilled
    # to the same placeholder by migration 0011.
    stock_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_STOCK_FALLBACK, server_default="10"
    )
    # Units committed to a contract but (per the current deduction-point default)
    # not separately held out — kept for a future reservation-at-offer model.
    reserved_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    @property
    def available_quantity(self) -> int:
        return (self.stock_quantity or 0) - (self.reserved_quantity or 0)
