"""minimal product stock tracking: products.stock_quantity / reserved_quantity

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-01

Two additive columns. Existing products are backfilled to the placeholder
opening stock (10 — mirrors `default_initial_stock_quantity` in the config
seed). `reserved_quantity` starts at 0. No other table changes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(
            sa.Column(
                "stock_quantity",
                sa.Integer(),
                nullable=False,
                server_default="10",
            )
        )
        batch.add_column(
            sa.Column(
                "reserved_quantity",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_column("reserved_quantity")
        batch.drop_column("stock_quantity")
