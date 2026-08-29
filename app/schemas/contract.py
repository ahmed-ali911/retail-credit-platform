from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.contract import ContractStatus, InstallmentStatus


class SalesOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    application_id: int
    product_id: int
    offer_id: int
    sale_price: float
    down_payment_amount: float
    created_at: datetime


class InstallmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contract_id: int
    sequence_number: int
    due_date: date
    principal_component: float
    profit_component: float
    total_due: float
    status: InstallmentStatus


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sales_order_id: int
    tenor_months: int
    total_profit: float
    unearned_profit_balance: float
    status: ContractStatus
    created_at: datetime
    activated_at: datetime | None
    sales_order: SalesOrderOut
    installments: list[InstallmentOut]


class AcceptResult(BaseModel):
    offer_id: int
    sales_order_id: int
    contract_id: int
    contract: ContractOut
