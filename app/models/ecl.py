"""Expected Credit Loss (ECL) & provision — first implementation slice.

The one framing rule this module is built around (external accounting review):

    ECL assessment applies to EVERY contract from the moment its receivable is
    first recognised (contract activation), continuously — not only to
    delinquent ones. What changes over the life of a contract is not *whether*
    an ECL exists but *which calculation path* produces it:

      * ``simplified_lifetime`` (Path A) — lifetime ECL from day one, no staging.
      * ``three_stage``        (Path B) — IFRS 9 general 3-stage model. The
        30+/90+ DPD thresholds are *rebuttable presumptions* that prompt a
        staging review, combined with at least one other risk signal — never a
        hard unconditional stage change. PD/LGD are left ``NULL`` until a real
        source exists (never fabricated, never silently shown as zero).
      * ``dpd_banded``         (Path C) — pragmatic provision-% per DPD band.
        The only path fully computable today; the shipped default.

Which path is the approved accounting policy is a BUSINESS DECISION still open.
All three are structurally present and selected by the ``ecl_methodology``
config switch.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
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
    simplified_lifetime = "simplified_lifetime"  # Path A
    three_stage = "three_stage"                  # Path B
    dpd_banded = "dpd_banded"                    # Path C (default)


class ECLRun(Base):
    """One on-demand portfolio ECL recalculation (the same on-demand pattern as
    ``POST /jobs/assess-overdue`` — not a scheduler). Holds the run-level
    totals surfaced by the "Run ECL Calculation" panel and links the single
    ``ecl_provision_movement`` accounting event the run emits."""

    __tablename__ = "ecl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    methodology: Mapped[ECLMethodology] = mapped_column(
        Enum(ECLMethodology, native_enum=False, length=30), nullable=False
    )
    contracts_assessed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_ead: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    # NULL when the active methodology cannot produce an ECL figure today
    # (Path B, no PD/LGD source) — never coerced to 0.
    total_ecl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    total_provision_movement: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True
    )
    accounting_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_events.id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    assessments: Mapped[list["ECLAssessment"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ECLAssessment(Base):
    """One ECL assessment of one contract as of one date.

    Created:
      * at contract activation — the initial "day one" assessment (``run_id``
        NULL);
      * by every portfolio ``ECLRun`` thereafter (``run_id`` set).

    ``UniqueConstraint(contract_id, as_of_date)`` makes re-assessing the same
    contract on the same date an upsert, so re-running a job is safe.
    """

    __tablename__ = "ecl_assessments"
    __table_args__ = (
        UniqueConstraint("contract_id", "as_of_date", name="uq_ecl_contract_asof"),
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

    # Exposure at default — outstanding receivable (principal + profit) plus
    # outstanding late fees, straight from the existing Receivable calculation.
    ead: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    dpd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Path C only — the DPD band label (e.g. "current", "31-60"); NULL otherwise.
    dpd_bucket: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Path B only — IFRS 9 stage (1/2/3) and the reasoning; NULL under A/C.
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Path B only — left NULL until a real PD/LGD source is configured.
    # NEVER written as 0 or a fabricated figure (see the module docstring).
    pd: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    lgd: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    # The effective loss rate actually applied (Path C bucket %, or Path A
    # lifetime loss rate). NULL under Path B (no computable rate without PD/LGD).
    loss_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    # The calculated ECL. NULL — not 0 — when the active methodology has no
    # real inputs to compute it (Path B).
    ecl_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)

    # Provision movement vs this contract's previous assessment.
    provision_before: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True
    )
    provision_movement: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True
    )

    # The exact config values in force at calculation time — same
    # "config snapshot per decision" principle as Credit Assessment.
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped["ECLRun | None"] = relationship(back_populates="assessments")
    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
