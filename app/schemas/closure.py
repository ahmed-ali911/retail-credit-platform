from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.closure import ClosureReason
from app.schemas.approval import ApprovalRequestOut


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
    # BDR item #7 — the effective rebate differs from the config default, so
    # settling against this quote needs maker-checker approval first.
    is_deviation: bool = False


class SettleRequest(BaseModel):
    amount: float = Field(gt=0)
    external_reference: str = Field(min_length=1, max_length=100)
    # BDR item #7 — optional staff-granted early-settlement profit rebate.
    # Supply one or the other, never both (422). Omitted → 0% (config default).
    requested_rebate_pct: float | None = Field(default=None, ge=0, le=1)
    requested_rebate_amount: float | None = Field(default=None, ge=0)


class CloseRequest(BaseModel):
    notes: str | None = None


class SettleResult(BaseModel):
    contract_id: int
    status: str  # "closed" when settled now, "pending_approval" on a deviation
    quote: SettlementQuoteOut
    closure: ContractClosureOut | None = None
    # BDR item #7 — set instead of `closure` when the rebate deviates from the
    # default: the settlement waits for a different approver.
    pending_approval: ApprovalRequestOut | None = None


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
