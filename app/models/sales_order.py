from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column


class SalesOrder(Base):
    """The product sale itself — *what was sold and for how much*.

    Kept separate from InstallmentContract, which answers *how it is financed*.
    """

    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("installment_offers.id"), nullable=False, unique=True
    )

    # Installment sale price (cash price + total profit).
    sale_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    down_payment_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    product: Mapped["Product"] = relationship()  # noqa: F821
    contract: Mapped["InstallmentContract"] = relationship(  # noqa: F821
        back_populates="sales_order", uselist=False
    )
