"""ECL & provision engine: three-stage PD/LGD, configurable stage engine,
manual overrides (maker-checker), versioned config, movement-based provisions.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-10

Additive + one constraint swap:
  * ``accounting_events.event_type`` widened 30 -> 40 (4 new ECL event types).
  * ``ecl_runs`` gains run_ref / status / kind / stage totals / version stamps /
    posting metadata.
  * ``ecl_assessments`` gains rating/segment, automated/override/final triplets
    for stage + PD (12m & lifetime) + LGD + EAD + ECL, movement-based provision
    columns, structured stage triggers, and version stamps. The per-date unique
    constraint is replaced with a per-run one so history is never overwritten.
  * new tables ``ecl_configurations`` (versioned model calibration) and
    ``ecl_overrides`` (approved manual adjustments).

Existing ``ecl_assessments`` rows are backfilled (automated_* = final_* = the
old single-value columns).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUM96 = sa.Numeric(9, 6)
_NUM162 = sa.Numeric(16, 2)


def upgrade() -> None:
    # --- 1. widen the accounting event_type column ----------------------
    with op.batch_alter_table("accounting_events") as batch:
        batch.alter_column(
            "event_type",
            existing_type=sa.String(length=30),
            type_=sa.String(length=40),
            existing_nullable=False,
        )

    # --- 2. versioned ECL configuration -------------------------------
    op.create_table(
        "ecl_configurations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("methodology", sa.String(length=30), nullable=False),
        sa.Column("rating_mapping", sa.JSON(), nullable=False),
        sa.Column("risk_segments", sa.JSON(), nullable=False),
        sa.Column("pd_term_structure", sa.JSON(), nullable=False),
        sa.Column("lgd_model", sa.JSON(), nullable=False),
        sa.Column("stage3_rules", sa.JSON(), nullable=False),
        sa.Column("sicr_rules", sa.JSON(), nullable=False),
        sa.Column("cure_rules", sa.JSON(), nullable=False),
        sa.Column("override_rules", sa.JSON(), nullable=False),
        sa.Column("accounting_mapping", sa.JSON(), nullable=False),
        sa.Column("dpd_provision_pct", sa.JSON(), nullable=False),
        sa.Column("lifetime_loss_rate", _NUM96, nullable=False, server_default="0.10"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_ecl_config_version"),
    )
    op.create_index("ix_ecl_configurations_version", "ecl_configurations", ["version"])
    op.create_index("ix_ecl_configurations_is_active", "ecl_configurations", ["is_active"])

    # --- 3. approved manual overrides -------------------------------
    op.create_table(
        "ecl_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("override_type", sa.String(length=15), nullable=False),
        sa.Column("status", sa.String(length=15), nullable=False),
        sa.Column("reason_code", sa.String(length=40), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.String(length=255), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("automated_value", sa.JSON(), nullable=False),
        sa.Column("approved_value", sa.JSON(), nullable=False),
        sa.Column("ecl_before", _NUM162, nullable=True),
        sa.Column("ecl_after", _NUM162, nullable=True),
        sa.Column("financial_impact", _NUM162, nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("approval_request_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"]),
        sa.ForeignKeyConstraint(["superseded_by"], ["ecl_overrides.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ecl_overrides_contract_id", "ecl_overrides", ["contract_id"])
    op.create_index("ix_ecl_overrides_status", "ecl_overrides", ["status"])

    # --- 4. extend ecl_runs -----------------------------------------
    with op.batch_alter_table("ecl_runs") as b:
        b.add_column(sa.Column("run_ref", sa.String(length=40), nullable=True))
        b.add_column(sa.Column("status", sa.String(length=20), nullable=False, server_default="COMPLETED"))
        b.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="portfolio"))
        b.add_column(sa.Column("contracts_by_stage", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("total_ecl_stage_1", _NUM162, nullable=True))
        b.add_column(sa.Column("total_ecl_stage_2", _NUM162, nullable=True))
        b.add_column(sa.Column("total_ecl_stage_3", _NUM162, nullable=True))
        b.add_column(sa.Column("ecl_config_version", sa.Integer(), nullable=True))
        b.add_column(sa.Column("stage_rule_version", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("pd_model_version", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("lgd_model_version", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("calculation_version", sa.String(length=20), nullable=False, server_default="1.0"))
        b.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        b.add_column(sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("posted_by", sa.Integer(), nullable=True))
        b.create_unique_constraint("uq_ecl_run_ref", ["run_ref"])
        b.create_foreign_key("fk_ecl_runs_posted_by", "users", ["posted_by"], ["id"])
    op.create_index("ix_ecl_runs_run_ref", "ecl_runs", ["run_ref"])
    op.create_index("ix_ecl_runs_status", "ecl_runs", ["status"])

    # --- 5. extend ecl_assessments (+ constraint swap) --------------
    with op.batch_alter_table("ecl_assessments") as b:
        for col in (
            sa.Column("risk_rating", sa.String(length=10), nullable=True),
            sa.Column("risk_segment", sa.String(length=40), nullable=True),
            sa.Column("origination_rating", sa.String(length=10), nullable=True),
            sa.Column("origination_pd_12m", _NUM96, nullable=True),
            sa.Column("automated_stage", sa.Integer(), nullable=True),
            sa.Column("automated_pd_12m", _NUM96, nullable=True),
            sa.Column("automated_pd_lifetime", _NUM96, nullable=True),
            sa.Column("automated_lgd", _NUM96, nullable=True),
            sa.Column("automated_ecl", _NUM162, nullable=True),
            sa.Column("stage_triggers", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("cure_note", sa.Text(), nullable=True),
            sa.Column("override_id", sa.Integer(), nullable=True),
            sa.Column("override_stage", sa.Integer(), nullable=True),
            sa.Column("override_pd_12m", _NUM96, nullable=True),
            sa.Column("override_pd_lifetime", _NUM96, nullable=True),
            sa.Column("override_lgd", _NUM96, nullable=True),
            sa.Column("final_stage", sa.Integer(), nullable=True),
            sa.Column("final_pd_12m", _NUM96, nullable=True),
            sa.Column("final_pd_lifetime", _NUM96, nullable=True),
            sa.Column("final_lgd", _NUM96, nullable=True),
            sa.Column("final_ead", _NUM162, nullable=True),
            sa.Column("final_ecl", _NUM162, nullable=True),
            sa.Column("opening_provision", _NUM162, nullable=True),
            sa.Column("calculated_ecl", _NUM162, nullable=True),
            sa.Column("override_adjustment", _NUM162, nullable=True),
            sa.Column("closing_provision", _NUM162, nullable=True),
            sa.Column("movement_type", sa.String(length=25), nullable=True),
            sa.Column("accounting_event_id", sa.Integer(), nullable=True),
            sa.Column("ecl_config_version", sa.Integer(), nullable=True),
            sa.Column("stage_rule_version", sa.String(length=20), nullable=True),
            sa.Column("pd_model_version", sa.String(length=20), nullable=True),
            sa.Column("lgd_model_version", sa.String(length=20), nullable=True),
            sa.Column("calculation_version", sa.String(length=20), nullable=True),
            sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=True),
        ):
            b.add_column(col)
        b.drop_constraint("uq_ecl_contract_asof", type_="unique")
        b.create_unique_constraint("uq_ecl_run_contract", ["run_id", "contract_id"])
        b.create_foreign_key(
            "fk_ecl_assessments_override", "ecl_overrides", ["override_id"], ["id"]
        )
        b.create_foreign_key(
            "fk_ecl_assessments_acct_event", "accounting_events", ["accounting_event_id"], ["id"]
        )

    # --- 6. backfill existing rows --------------------------------
    op.execute(
        "UPDATE ecl_assessments SET "
        "automated_stage = stage, final_stage = stage, "
        "automated_pd_12m = pd, final_pd_12m = pd, "
        "automated_pd_lifetime = pd, final_pd_lifetime = pd, "
        "automated_lgd = lgd, final_lgd = lgd, "
        "automated_ecl = ecl_amount, final_ecl = ecl_amount, "
        "final_ead = ead, "
        "opening_provision = provision_before, "
        "calculated_ecl = ecl_amount, "
        "closing_provision = ecl_amount, "
        "calculated_at = created_at"
    )
    op.execute("UPDATE ecl_runs SET status = 'COMPLETED', kind = 'portfolio'")


def downgrade() -> None:
    # uq_ecl_contract_asof (contract_id, as_of_date) is about to come back, but
    # it no longer holds once the module has run for real: a contract's
    # day-one origination assessment (run_id NULL) and a same-day portfolio
    # run both land on the same as_of_date — the ordinary first-day flow, not
    # an edge case. Collapse duplicates first, keeping the newest row (highest
    # id = most recently calculated) per (contract_id, as_of_date); a fresh
    # 0013 database has no duplicates, so this is a no-op there.
    op.execute(
        "DELETE FROM ecl_assessments WHERE id NOT IN ("
        "SELECT MAX(id) FROM ecl_assessments GROUP BY contract_id, as_of_date"
        ")"
    )

    with op.batch_alter_table("ecl_assessments") as b:
        b.drop_constraint("fk_ecl_assessments_acct_event", type_="foreignkey")
        b.drop_constraint("fk_ecl_assessments_override", type_="foreignkey")
        b.drop_constraint("uq_ecl_run_contract", type_="unique")
        b.create_unique_constraint("uq_ecl_contract_asof", ["contract_id", "as_of_date"])
        for name in (
            "calculated_at", "calculation_version", "lgd_model_version",
            "pd_model_version", "stage_rule_version", "ecl_config_version",
            "accounting_event_id", "movement_type", "closing_provision",
            "override_adjustment", "calculated_ecl", "opening_provision",
            "final_ecl", "final_ead", "final_lgd", "final_pd_lifetime",
            "final_pd_12m", "final_stage", "override_lgd", "override_pd_lifetime",
            "override_pd_12m", "override_stage", "override_id", "cure_note",
            "stage_triggers", "automated_ecl", "automated_lgd",
            "automated_pd_lifetime", "automated_pd_12m", "automated_stage",
            "origination_pd_12m", "origination_rating", "risk_segment", "risk_rating",
        ):
            b.drop_column(name)

    op.drop_index("ix_ecl_runs_status", "ecl_runs")
    op.drop_index("ix_ecl_runs_run_ref", "ecl_runs")
    with op.batch_alter_table("ecl_runs") as b:
        b.drop_constraint("fk_ecl_runs_posted_by", type_="foreignkey")
        b.drop_constraint("uq_ecl_run_ref", type_="unique")
        for name in (
            "posted_by", "posted_at", "error_message", "calculation_version",
            "lgd_model_version", "pd_model_version", "stage_rule_version",
            "ecl_config_version", "total_ecl_stage_3", "total_ecl_stage_2",
            "total_ecl_stage_1", "contracts_by_stage", "kind", "status", "run_ref",
        ):
            b.drop_column(name)

    op.drop_index("ix_ecl_overrides_status", "ecl_overrides")
    op.drop_index("ix_ecl_overrides_contract_id", "ecl_overrides")
    op.drop_table("ecl_overrides")
    op.drop_index("ix_ecl_configurations_is_active", "ecl_configurations")
    op.drop_index("ix_ecl_configurations_version", "ecl_configurations")
    op.drop_table("ecl_configurations")

    with op.batch_alter_table("accounting_events") as batch:
        batch.alter_column(
            "event_type",
            existing_type=sa.String(length=40),
            type_=sa.String(length=30),
            existing_nullable=False,
        )
