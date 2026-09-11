from __future__ import annotations

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class EclRunRequest(BaseModel):
    as_of: str | None = None  # ISO date; defaults to today
    post: bool = False        # also finalise (emit accounting events) in one call


class EclRunResult(BaseModel):
    run_id: int
    run_ref: str
    status: str
    as_of_date: str
    methodology: str
    ecl_config_version: int
    contracts_assessed: int
    contracts_by_stage: dict
    total_ead: float
    total_ecl: float | None
    total_ecl_by_stage: dict
    total_provision_movement: float | None
    accounting_event_id: int | None
    posted: bool


# --------------------------------------------------------------------------- #
# Manual overrides (maker-checker)
# --------------------------------------------------------------------------- #
class StageOverrideRequest(BaseModel):
    stage: int = Field(ge=1, le=3)
    reason_code: str
    justification: str = Field(min_length=10)
    evidence_ref: str | None = None
    comments: str | None = None
    effective_from: str | None = None   # ISO date; defaults to today
    effective_to: str | None = None     # ISO date; defaults to today + validity days
    review_date: str | None = None


class ParameterOverrideRequest(BaseModel):
    pd_12m: float | None = Field(default=None, ge=0, le=1)
    pd_lifetime: float | None = Field(default=None, ge=0, le=1)
    lgd: float | None = Field(default=None, ge=0, le=1)
    ead: float | None = Field(default=None, ge=0)
    risk_rating: str | None = None
    reason_code: str
    justification: str = Field(min_length=10)
    evidence_ref: str | None = None
    comments: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    review_date: str | None = None


# --------------------------------------------------------------------------- #
# Configuration (maker-checker)
# --------------------------------------------------------------------------- #
class EclConfigUpdateRequest(BaseModel):
    changes: dict = Field(min_length=1)
    notes: str | None = None
