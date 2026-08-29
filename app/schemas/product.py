from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.product import ProductCategory


class ProductCreate(BaseModel):
    name: str
    category: ProductCategory = ProductCategory.other
    cash_price: float = Field(gt=0)
    installment_eligible: bool = True


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    category: ProductCategory
    cash_price: float
    installment_eligible: bool
