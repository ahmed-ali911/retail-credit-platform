"""Expected Credit Loss (ECL) & provision — engine domain model.

Methodology (CONFIRMED shape, not values):
  * ``three_stage``          — primary. Stage 1 = 12-month ECL, Stages 2 & 3 =
    lifetime ECL. Stage is decided by a **configurable** Stage Determination
    Engine (Stage-3 triggers evaluated first, then Stage-2 / SICR triggers,
    else Stage 1) — NOT a hard DPD ladder.
  * ``simplified_lifetime``  — retained, fully supported, for portfolios Finance
    later designates.
  * ``dpd_banded``           — retained pragmatic provision-% per DPD band.

Every PD / LGD / threshold / mapping value in ``ECLConfiguration`` is a
placeholder and is tagged BUSINESS / RISK MODEL DECISION REQUIRED. Nothing here
claims any number is an IFRS 9 requirement.

Design principle enforced by the schema: **AUTOMATED assessment, MANUAL
override, and FINAL approved assessment are stored separately.** The engine's
own result (``automated_*``) is never mutated by an override; the override is a
separate record; ``final_*`` reflects the override where one is active.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class ECLMethodology(str, enum.Enum):
    three_stage = "three_stage"                  # primary
    simplified_lifetime = "simplified_lifetime"  # retained alternative
    dpd_banded = "dpd_banded"                    # retained pragmatic path


class ECLRunStatus(str, enum.Enum):
    draft = "DRAFT"            # computed, not posted; provisions not final
    calculating = "CALCULATING"
    completed = "COMPLETED"    # computed successfully, awaiting post
    failed = "FAILED"
    posted = "POSTED"          # finalised — accounting events emitted, provisions locked


class ECLStage(int, enum.Enum):
    stage_1 = 1
    stage_2 = 2
    stage_3 = 3


class ECLMovementType(str, enum.Enum):
    created = "created"          # first provision for this contract
    increased = "increased"
    released = "released"        # provision reduced
    unchanged = "unchanged"
    override_adjustment = "override_adjustment"


class ECLOverrideType(str, enum.Enum):
    stage = "STAGE"
    parameter = "PARAMETER"


class ECLOverrideStatus(str, enum.Enum):
    pending = "PENDING"
    approved = "APPROVED"       # approved but not yet inside its effective window (rare)
    rejected = "REJECTED"
    active = "ACTIVE"           # approved and currently in force
    expired = "EXPIRED"
    cancelled = "CANCELLED"     # withdrawn / superseded


class ECLOverrideReasonCode(str, enum.Enum):
    customer_financial_difficulty = "CUSTOMER_FINANCIAL_DIFFICULTY"
    unlikely_to_pay = "UNLIKELY_TO_PAY"
    restructuring_forbearance = "RESTRUCTURING_FORBEARANCE"
    data_quality_issue = "DATA_QUALITY_ISSUE"
    model_limitation = "MODEL_LIMITATION"
    specific_credit_event = "SPECIFIC_CREDIT_EVENT"
    other = "OTHER"


# --------------------------------------------------------------------------- #
# Versioned configuration
# --------------------------------------------------------------------------- #
class ECLConfiguration(Base):
    """One immutable, versioned ECL model-calibration set.

    A change never edits a row — it activates a **new version** (clone + apply
    + swap ``is_active``), gated by maker-checker. Every ``ECLAssessment``
    stamps the ``version`` that produced it, so a historical run stays
    reproducible after Finance revises the model.

    All numeric contents are placeholders — BUSINESS / RISK MODEL DECISION
    REQUIRED (see the audit's BDR-43 … BDR-52).
    """

    __tablename__ = "ecl_configurations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )
    methodology: Mapped[ECLMethodology] = mapped_column(
        Enum(ECLMethodology, native_enum=False, length=30), nullable=False
    )

    # risk_score band -> internal rating grade.  [{min, max, rating}]
    rating_mapping: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # rating grade -> risk segment.  {rating: segment}
    risk_segments: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # segment -> {pd_12m, pd_lifetime}.  Placeholder PD term structure.
    pd_term_structure: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # segment -> lgd  (+ optional "stage_3" override).  Placeholder LGD model.
    lgd_model: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Stage Determination Engine rules (evaluated Stage-3 first, then Stage-2).
    # Each: {id, name, category, param, ...}. See stage_engine.py for the shapes.
    stage3_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sicr_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cure_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    override_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # accounting event_type -> GL mapping placeholder (empty; BDR-31)
    accounting_mapping: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Retained parameters for the non-three-stage paths.
    dpd_provision_pct: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    lifetime_loss_rate: Mapped[Decimal] = mapped_column(
        Numeric(9, 6), nullable=False, default=Decimal("0.10")
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class ECLRun(Base):
    """One ECL calculation cycle.

    ``run_ref`` is a human key (``ECL-RUN-2026-09-001``). A run is never
    overwritten — every run's ``ECLAssessment`` rows are kept. ``POSTED`` runs
    have emitted their accounting events and locked their provisions.
    """

    __tablename__ = "ecl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_ref: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    methodology: Mapped[ECLMethodology] = mapped_column(
        Enum(ECLMethodology, native_enum=False, length=30), nullable=False
    )
    status: Mapped[ECLRunStatus] = mapped_column(
        Enum(ECLRunStatus, native_enum=False, length=20),
        default=ECLRunStatus.completed,
        server_default=ECLRunStatus.completed.value,
        nullable=False,
        index=True,
    )
    # "origination" for the day-one per-contract assessment; "portfolio" for a run.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="portfolio", server_default="portfolio"
    )

    contracts_assessed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    contracts_by_stage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    total_ead: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    total_ecl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    total_ecl_stage_1: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    total_ecl_stage_2: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    total_ecl_stage_3: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    total_provision_movement: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True
    )

    # version stamps — reproducibility if the model changes later
    ecl_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_rule_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pd_model_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lgd_model_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    calculation_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="1.0", server_default="1.0"
    )

    accounting_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_events.id"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    assessments: Mapped[list["ECLAssessment"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------- #
# Per-contract assessment
# --------------------------------------------------------------------------- #
class ECLAssessment(Base):
    """One ECL assessment of one contract inside one run.

    ``UniqueConstraint(run_id, contract_id)`` — one row per contract per run;
    history is never overwritten.
    """

    __tablename__ = "ecl_assessments"
    __table_args__ = (
        UniqueConstraint("run_id", "contract_id", name="uq_ecl_run_contract"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ecl_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    methodology: Mapped[ECLMethodology] = mapped_column(
        Enum(ECLMethodology, native_enum=False, length=30), nullable=False
    )

    # --- inputs -----------------------------------------------------------
    ead: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    dpd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dpd_bucket: Mapped[str | None] = mapped_column(String(20), nullable=True)
    risk_rating: Mapped[str | None] = mapped_column(String(10), nullable=True)
    risk_segment: Mapped[str | None] = mapped_column(String(40), nullable=True)
    origination_rating: Mapped[str | None] = mapped_column(String(10), nullable=True)
    origination_pd_12m: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    # --- automated result (NEVER mutated by an override) -----------------
    automated_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automated_pd_12m: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    automated_pd_lifetime: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    automated_lgd: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    automated_ecl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    # [{id, name, category, triggered: bool, detail}]
    stage_triggers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # final one-line explanation ("SICR criteria satisfied: DPD 35 ≥ 30 …")
    stage_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cure_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- override (denormalised from the active ECLOverride at run time) --
    override_id: Mapped[int | None] = mapped_column(
        ForeignKey("ecl_overrides.id"), nullable=True
    )
    override_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    override_pd_12m: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    override_pd_lifetime: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    override_lgd: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    # --- final approved result -----------------------------------------
    final_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_pd_12m: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    final_pd_lifetime: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    final_lgd: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    final_ead: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    final_ecl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)

    # --- provision movement (movement-based) --------------------------
    opening_provision: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    calculated_ecl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    override_adjustment: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    closing_provision: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    movement_type: Mapped[ECLMovementType | None] = mapped_column(
        Enum(ECLMovementType, native_enum=False, length=25), nullable=True
    )
    accounting_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_events.id"), nullable=True
    )

    # --- legacy / compat columns (populated from final_*) --------------
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pd: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    lgd: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    loss_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    ecl_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    provision_before: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    provision_movement: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)

    # --- versioning / audit ------------------------------------------
    ecl_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_rule_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pd_model_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lgd_model_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    calculation_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped["ECLRun | None"] = relationship(back_populates="assessments")
    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
    override: Mapped["ECLOverride | None"] = relationship(foreign_keys=[override_id])


# --------------------------------------------------------------------------- #
# Manual overrides (created ON APPROVAL — the automated result stays intact)
# --------------------------------------------------------------------------- #
class ECLOverride(Base):
    """An approved manual adjustment to a contract's automated ECL assessment.

    Created only when a maker-checker ``ApprovalRequest`` is approved. Holds
    both the automated value and the approved value; carries its own effective
    window and lifecycle. A new automatic run re-derives the automated result
    independently and applies any ``ACTIVE`` override on top — it never deletes
    the override.
    """

    __tablename__ = "ecl_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    override_type: Mapped[ECLOverrideType] = mapped_column(
        Enum(ECLOverrideType, native_enum=False, length=15), nullable=False
    )
    status: Mapped[ECLOverrideStatus] = mapped_column(
        Enum(ECLOverrideStatus, native_enum=False, length=15),
        default=ECLOverrideStatus.pending,
        nullable=False,
        index=True,
    )
    reason_code: Mapped[ECLOverrideReasonCode] = mapped_column(
        Enum(ECLOverrideReasonCode, native_enum=False, length=40), nullable=False
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    # {stage: 3}  OR  {pd_12m: 0.18, lgd: 0.55, ...}  OR  {risk_rating: "D"}
    automated_value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    approved_value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # financial impact, computed from the assessment at request time
    ecl_before: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    ecl_after: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    financial_impact: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    approval_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=True
    )
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("ecl_overrides.id"), nullable=True
    )
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
