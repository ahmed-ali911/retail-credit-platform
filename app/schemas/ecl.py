from __future__ import annotations

from pydantic import BaseModel


class EclRunRequest(BaseModel):
    as_of: str | None = None  # ISO date; defaults to today


class EclRunResult(BaseModel):
    run_id: int
    as_of_date: str
    methodology: str
    contracts_assessed: int
    total_ead: float
    total_ecl: float | None
    total_provision_movement: float | None
    accounting_event_id: int | None
    pd_lgd_note: str | None
