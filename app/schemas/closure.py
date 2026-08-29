from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.closure import ClosureReason


class ContractClosureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contract_id: int
    reason: ClosureReason
    financial_adjustment: float | None
    closed_at: datetime
    notes: str | None


class SettlementQuoteOut(BaseModel):
    contract_id: int
    outstanding_principal: float
    outstanding_late_fees: float
    unearned_profit_total: float
    profit_rebate_pct: float
    profit_rebate_amount: float
    profit_still_charged: float
    final_payoff_amount: float
    quote_expiry: datetime


class SettleRequest(BaseModel):
    amount: float = Field(gt=0)
    external_reference: str = Field(min_length=1, max_length=100)


class CloseRequest(BaseModel):
    notes: str | None = None


class SettleResult(BaseModel):
    contract_id: int
    status: str
    quote: SettlementQuoteOut
    closure: ContractClosureOut


class CancellationResultOut(BaseModel):
    contract_id: int
    status: str
    down_payment_amount: float
    refund_pct: float
    down_payment_refund: float
    closure: ContractClosureOut


class ReturnResultOut(BaseModel):
    contract_id: int
    status: str
    ownership_transfers_on_delivery: bool
    down_payment_amount: float
    refund_pct: float
    down_payment_refund: float
    settlement_shape_payoff: float
    net_adjustment: float
    closure: ContractClosureOut
