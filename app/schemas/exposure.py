from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContractExposureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    contract_id: int
    status: str
    outstanding_principal: float
    outstanding_profit: float
    outstanding_late_fees: float
    outstanding_total: float


class CustomerExposureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    customer_id: int
    aggregation_level: str
    total_outstanding: float
    contracts: list[ContractExposureOut]
