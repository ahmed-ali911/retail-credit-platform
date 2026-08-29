"""contract closure (early settlement / cancellation / return)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contract_closures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("financial_adjustment", sa.Numeric(14, 2), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id", name="uq_contract_closure"),
    )


def downgrade() -> None:
    op.drop_table("contract_closures")
