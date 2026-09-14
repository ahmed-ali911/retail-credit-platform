from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.references import format_reference
from app.models.payment_gateway import PaymentIntentStatus, PaymentPurpose, WebhookProcessingStatus


# --------------------------------------------------------------------------- #
# Customer Payment Screen (contract payment options)
# --------------------------------------------------------------------------- #
class PaymentOptionsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    contract_id: int
    currency: str
    next_installment_amount: float | None
    next_installment_due_date: date | None
    overdue_amount: float
    late_fees_outstanding: float
    total_outstanding: float
    minimum_partial_amount: float
    can_pay_current_installment: bool
    can_pay_overdue: bool
    can_pay_full_outstanding: bool
    can_pay_partial: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_reference(self) -> str:
        return format_reference("InstallmentContract", self.contract_id)


# --------------------------------------------------------------------------- #
# PaymentIntent
# --------------------------------------------------------------------------- #
class PaymentIntentCreate(BaseModel):
    contract_id: int
    payment_purpose: PaymentPurpose
    amount: float | None = Field(default=None, gt=0)  # required only for payment_purpose=partial
    idempotency_key: str = Field(min_length=1, max_length=120)


class GatewayTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    gateway_transaction_reference: str
    gateway_status: PaymentIntentStatus
    authorized_amount: float | None
    captured_amount: float | None
    settled_amount: float | None
    gateway_fee: float | None
    authorization_timestamp: datetime | None
    capture_timestamp: datetime | None
    settlement_timestamp: datetime | None
    failure_code: str | None
    failure_reason: str | None
    created_at: datetime


class PaymentIntentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    payment_reference: str
    customer_id: int
    contract_id: int
    requested_amount: float
    currency: str
    payment_purpose: PaymentPurpose
    status: PaymentIntentStatus
    gateway_session_id: str | None
    gateway_name: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    transactions: list[GatewayTransactionOut] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_reference(self) -> str:
        return format_reference("InstallmentContract", self.contract_id)


class CheckoutSessionOut(BaseModel):
    payment_reference: str
    checkout_url: str
    expires_at: datetime | None
    status: PaymentIntentStatus


class PaymentStatusOut(BaseModel):
    """GET /payments/{payment_reference}/status — the payment-status timeline
    (section 13, Customer Payment Screen)."""

    payment_reference: str
    status: PaymentIntentStatus
    requested_amount: float
    currency: str
    contract_id: int
    created_at: datetime
    updated_at: datetime
    transactions: list[GatewayTransactionOut] = []


# --------------------------------------------------------------------------- #
# Webhook processing result (internal — not what the gateway sees, which is
# just an HTTP status; this is returned to callers of the endpoint for tests
# and operational visibility)
# --------------------------------------------------------------------------- #
class WebhookEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    gateway_event_id: str
    event_type: str
    payment_reference: str
    claimed_status: str | None
    signature_valid: bool
    processing_status: WebhookProcessingStatus
    retry_count: int
    received_at: datetime
    processed_at: datetime | None
    failure_reason: str | None
