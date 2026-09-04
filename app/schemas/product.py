from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.references import format_reference
from app.models.product import ProductCategory


class ProductCreate(BaseModel):
    name: str
    category: ProductCategory = ProductCategory.other
    cash_price: float = Field(gt=0)
    installment_eligible: bool = True
    # Optional explicit opening stock; omitted -> the config-driven default.
    stock_quantity: int | None = Field(default=None, ge=0)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    category: ProductCategory
    cash_price: float
    installment_eligible: bool
    stock_quantity: int
    reserved_quantity: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def available_quantity(self) -> int:
        return (self.stock_quantity or 0) - (self.reserved_quantity or 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("Product", self.id)


class ProductListItem(ProductOut):
    """Same shape as ProductOut — the Step 10 directory needs the stock fields."""


class StockAdjustmentRequest(BaseModel):
    delta: int = Field(description="Positive = restock, negative = correction/write-down")
    reason: str = Field(min_length=1, max_length=500)
