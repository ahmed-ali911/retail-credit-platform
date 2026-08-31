"""widen assessment_results.source for the P0-3 affordability re-check

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-31

Only a column widen — `offer_affordability_recheck` (26 chars) does not fit the
`VARCHAR(20)` added in 0007. The affordability config flag itself is a
`config_parameters` row seeded from YAML, so it needs no DDL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("assessment_results") as batch:
        batch.alter_column(
            "source",
            existing_type=sa.String(length=20),
            type_=sa.String(length=40),
            existing_nullable=False,
            existing_server_default="automated",
        )


def downgrade() -> None:
    with op.batch_alter_table("assessment_results") as batch:
        batch.alter_column(
            "source",
            existing_type=sa.String(length=40),
            type_=sa.String(length=20),
            existing_nullable=False,
            existing_server_default="automated",
        )
