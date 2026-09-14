from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.gateway_settlement import GatewayReconciliationItemStatus, ReconciliationOutcome


class SettlementBatchItemIn(BaseModel):
    gateway_transaction_reference: str = Field(min_length=1, max_length=100)
    merchant_reference: str = Field(min_length=1, max_length=64)
    settlement_date: date
    gross_amount: float = Field(gt=0)
    gateway_fee: float = Field(ge=0)
    net_amount: float
    currency: str = Field(default="KWD", min_length=3, max_length=3)
    gateway_status: str = Field(min_length=1, max_length=25)


class SettlementBatchImport(BaseModel):
    batch_reference: str = Field(min_length=1, max_length=100)
    settlement_date: date
    currency: str = Field(default="KWD", min_length=3, max_length=3)
    items: list[SettlementBatchItemIn]


class SettlementBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    batch_reference: str
    settlement_date: date
    gateway_name: str
    currency: str
    item_count: int
    total_gross_amount: float
    total_gateway_fee: float
    total_net_amount: float
    imported_by: int | None
    imported_at: datetime


class ReconciliationItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    settlement_batch_id: int | None
    settlement_date: date
    gateway_transaction_reference: str | None
    merchant_reference: str | None
    gross_amount: float | None
    gateway_fee: float | None
    net_amount: float | None
    currency: str
    gateway_reported_status: str | None
    outcome: ReconciliationOutcome
    status: GatewayReconciliationItemStatus
    matched_payment_id: int | None
    matched_intent_id: int | None
    variance_amount: float | None
    resolution_reason: str | None
    resolution_comments: str | None
    resolved_by: int | None
    resolved_at: datetime | None
    created_at: datetime


class SettlementBatchImportResult(BaseModel):
    batch: SettlementBatchOut
    items_processed: int
    matched: int
    exceptions: int
    missing_in_gateway: int


class ReconciliationItemResolveRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
    comments: str | None = Field(default=None, max_length=2000)
