from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class CheckoutSessionCreate(BaseModel):
    merchant_reference: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="KWD", min_length=3, max_length=3)
    customer_name: str = Field(min_length=1, max_length=200)
    contract_reference: str = Field(min_length=1, max_length=64)
    description: str = ""
    return_url: str
    webhook_url: str | None = None


class CheckoutSessionOut(BaseModel):
    token: str
    checkout_url: str
    expires_at: datetime


class SimulatedOutcome(str, enum.Enum):
    success_settlement = "success_settlement"
    success_authorization_only = "success_authorization_only"
    insufficient_funds = "insufficient_funds"
    customer_cancel = "customer_cancel"
    timeout = "timeout"
    delayed_settlement = "delayed_settlement"
    duplicate_webhook = "duplicate_webhook"
    invalid_signature = "invalid_signature"
    settle_then_reverse = "settle_then_reverse"


class SimulateRequest(BaseModel):
    outcome: SimulatedOutcome


class SimulateResult(BaseModel):
    outcome: SimulatedOutcome
    status: str
    return_url: str


class TransactionOut(BaseModel):
    gateway_transaction_reference: str
    merchant_reference: str
    status: str
    amount: Decimal
    currency: str
    gateway_fee: Decimal | None
    authorization_timestamp: datetime | None
    capture_timestamp: datetime | None
    settlement_timestamp: datetime | None
    failure_code: str | None
    failure_reason: str | None
    simulated_outcome: str | None


class WebhookRetryResult(BaseModel):
    gateway_event_id: str
    attempt_count: int
    last_response_status: int | None
    delivered: bool
