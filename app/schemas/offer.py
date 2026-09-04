from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.references import format_reference
from app.models.offer import OfferStatus


class OfferCreate(BaseModel):
    down_payment_amount: float = Field(ge=0)
    # Defaults to the application's requested_tenor_months when omitted.
    tenor_months: int | None = Field(default=None, gt=0, le=120)


class OfferAccept(BaseModel):
    down_payment_confirmed: bool = False
    down_payment_reference: str | None = None
    # Optional cross-check against the offer's down payment.
    down_payment_amount: float | None = None


class ScheduleLine(BaseModel):
    sequence_number: int
    principal_component: float
    profit_component: float
    total: float


class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    application_id: int
    cash_price: float
    down_payment: float
    tenor_months: int
    profit_rate: float
    installment_sale_price: float
    total_profit: float
    amount_financed: float
    status: OfferStatus
    valid_until: datetime
    created_at: datetime
    down_payment_confirmed: bool
    down_payment_reference: str | None
    accepted_at: datetime | None
    schedule_preview: list[ScheduleLine]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference_code(self) -> str:
        return format_reference("InstallmentOffer", self.id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def application_reference(self) -> str:
        return format_reference("CreditApplication", self.application_id)
