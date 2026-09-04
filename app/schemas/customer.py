from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.references import format_reference
from app.models.customer import CustomerStatus


class CustomerProfileIn(BaseModel):
    employer_name: str | None = None
    employment_type: str | None = None
    monthly_income: float = Field(ge=0)
    existing_monthly_obligations: float = Field(default=0, ge=0)
    address_line: str | None = None
    city: str | None = None
    contact_phone: str | None = None


class CustomerProfileOut(CustomerProfileIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int


class CustomerCreate(BaseModel):
    name: str
    national_id: str
    phone: str | None = None
    email: str | None = None
    status: CustomerStatus = CustomerStatus.active
    risk_score: int | None = Field(default=None, ge=0, le=1000)
    profile: CustomerProfileIn


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    national_id: str
    phone: str | None
    email: str | None
    status: CustomerStatus
    risk_score: int | None
    created_at: datetime
    profile: CustomerProfileOut | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("Customer", self.id)


class CustomerListItem(BaseModel):
    """Compact row for the Step 10 customer directory (`GET /customers?search=`)."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    national_id: str
    status: CustomerStatus
    risk_score: int | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("Customer", self.id)
