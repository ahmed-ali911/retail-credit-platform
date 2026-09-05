"""ECL & provision — first slice: ecl_runs, ecl_assessments; accounting_events.contract_id nullable

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-29

Additive: two new tables for ECL assessment history and portfolio runs. The one
change to an existing table is making ``accounting_events.contract_id``
nullable — a portfolio-level ``ecl_provision_movement`` event summarises a
movement across the whole book and has no single contract. No data is altered.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("accounting_events") as batch:
        batch.alter_column("contract_id", existing_type=sa.Integer(), nullable=True)

    op.create_table(
        "ecl_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("methodology", sa.String(length=30), nullable=False),
        sa.Column(
            "contracts_assessed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_ead", sa.Numeric(16, 2), nullable=False, server_default="0"
        ),
        sa.Column("total_ecl", sa.Numeric(16, 2), nullable=True),
        sa.Column("total_provision_movement", sa.Numeric(16, 2), nullable=True),
        sa.Column("accounting_event_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["accounting_event_id"], ["accounting_events.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ecl_runs_as_of_date", "ecl_runs", ["as_of_date"])

    op.create_table(
        "ecl_assessments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("methodology", sa.String(length=30), nullable=False),
        sa.Column("ead", sa.Numeric(16, 2), nullable=False),
        sa.Column("dpd", sa.Integer(), nullable=False),
        sa.Column("dpd_bucket", sa.String(length=20), nullable=True),
        sa.Column("stage", sa.Integer(), nullable=True),
        sa.Column("stage_reason", sa.Text(), nullable=True),
        sa.Column("pd", sa.Numeric(9, 6), nullable=True),
        sa.Column("lgd", sa.Numeric(9, 6), nullable=True),
        sa.Column("loss_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("ecl_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("provision_before", sa.Numeric(16, 2), nullable=True),
        sa.Column("provision_movement", sa.Numeric(16, 2), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["ecl_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contract_id", "as_of_date", name="uq_ecl_contract_asof"
        ),
    )
    op.create_index(
        "ix_ecl_assessments_run_id", "ecl_assessments", ["run_id"]
    )
    op.create_index(
        "ix_ecl_assessments_contract_id", "ecl_assessments", ["contract_id"]
    )
    op.create_index(
        "ix_ecl_assessments_customer_id", "ecl_assessments", ["customer_id"]
    )
    op.create_index(
        "ix_ecl_assessments_as_of_date", "ecl_assessments", ["as_of_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_ecl_assessments_as_of_date", "ecl_assessments")
    op.drop_index("ix_ecl_assessments_customer_id", "ecl_assessments")
    op.drop_index("ix_ecl_assessments_contract_id", "ecl_assessments")
    op.drop_index("ix_ecl_assessments_run_id", "ecl_assessments")
    op.drop_table("ecl_assessments")
    op.drop_index("ix_ecl_runs_as_of_date", "ecl_runs")
    op.drop_table("ecl_runs")
    with op.batch_alter_table("accounting_events") as batch:
        batch.alter_column("contract_id", existing_type=sa.Integer(), nullable=False)
