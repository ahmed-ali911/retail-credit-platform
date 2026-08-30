"""immutable ledger (dual-write) + manual-review fields on assessment_results

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- P0-1: immutable financial ledger (write-only in this phase) ---
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("related_action", sa.String(length=20), nullable=False),
        sa.Column("reference_type", sa.String(length=40), nullable=False),
        sa.Column("reference_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_entries_contract_id", "ledger_entries", ["contract_id"]
    )
    op.create_index(
        "ix_ledger_entries_reference",
        "ledger_entries",
        ["reference_type", "reference_id"],
    )

    # --- P0-2: manual verification fields on the existing assessment table ---
    with op.batch_alter_table("assessment_results") as batch:
        batch.add_column(
            sa.Column(
                "source",
                sa.String(length=20),
                nullable=False,
                server_default="automated",
            )
        )
        batch.add_column(sa.Column("reviewed_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_assessment_results_reviewed_by", "users", ["reviewed_by"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("assessment_results") as batch:
        batch.drop_constraint(
            "fk_assessment_results_reviewed_by", type_="foreignkey"
        )
        batch.drop_column("notes")
        batch.drop_column("reviewed_by")
        batch.drop_column("source")

    op.drop_index("ix_ledger_entries_reference", "ledger_entries")
    op.drop_index("ix_ledger_entries_contract_id", "ledger_entries")
    op.drop_table("ledger_entries")
