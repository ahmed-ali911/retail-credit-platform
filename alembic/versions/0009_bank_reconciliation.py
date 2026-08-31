"""bank reconciliation: bank_statement_lines, reconciliation_exceptions,
two additive Payment columns (P0-5, fixes S-5)

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-31

Additive only. `payments.reconciliation_status` lands NOT NULL with a
server default of ``unreconciled`` so every existing row is backfilled and no
existing payment/allocation/closure behavior changes. `gateway_reference` is
nullable and unused by today's flows. The date-tolerance knob is a
`config_parameters` row seeded from YAML, so it needs no DDL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("payments") as batch:
        batch.add_column(
            sa.Column(
                "gateway_reference", sa.String(length=100), nullable=True
            )
        )
        batch.add_column(
            sa.Column(
                "reconciliation_status",
                sa.String(length=20),
                nullable=False,
                server_default="unreconciled",
            )
        )

    op.create_table(
        "bank_statement_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bank_reference", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matched_payment_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["matched_payment_id"], ["payments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bank_statement_lines_bank_reference",
        "bank_statement_lines",
        ["bank_reference"],
    )

    op.create_table(
        "reconciliation_exceptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bank_line_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=25), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bank_line_id"], ["bank_statement_lines.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reconciliation_exceptions_bank_line_id",
        "reconciliation_exceptions",
        ["bank_line_id"],
    )
    op.create_index(
        "ix_reconciliation_exceptions_status",
        "reconciliation_exceptions",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reconciliation_exceptions_status", "reconciliation_exceptions"
    )
    op.drop_index(
        "ix_reconciliation_exceptions_bank_line_id", "reconciliation_exceptions"
    )
    op.drop_table("reconciliation_exceptions")
    op.drop_index(
        "ix_bank_statement_lines_bank_reference", "bank_statement_lines"
    )
    op.drop_table("bank_statement_lines")
    with op.batch_alter_table("payments") as batch:
        batch.drop_column("reconciliation_status")
        batch.drop_column("gateway_reference")
