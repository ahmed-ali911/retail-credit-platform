from __future__ import annotations

from pydantic import BaseModel


class ContractReportRow(BaseModel):
    contract_id: int
    status: str
    customer_id: int
    customer_name: str
    product_id: int
    product_name: str
    category: str
    tenor_months: int
    installment_sale_price: float
    created_at: str
    outstanding_total: float
    next_due_date: str | None = None


class ContractReportPage(BaseModel):
    items: list[ContractReportRow]
    total: int
    limit: int
    offset: int
    totals: dict = {}
