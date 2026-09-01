"""accounting-event boundary: accounting_events (fills Gap Matrix G-07)

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-31

One new table, purely additive. No existing table or column changes — accounting
events are generated downstream of events that already happen and never alter
any business row.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounting_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("event_reference", sa.String(length=120), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="KWD"
        ),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "accounting_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("external_gl_reference", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "retry_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_accounting_events_event_type", "accounting_events", ["event_type"]
    )
    op.create_index(
        "ix_accounting_events_event_reference",
        "accounting_events",
        ["event_reference"],
        unique=True,
    )
    op.create_index(
        "ix_accounting_events_contract_id", "accounting_events", ["contract_id"]
    )
    op.create_index(
        "ix_accounting_events_customer_id", "accounting_events", ["customer_id"]
    )
    op.create_index(
        "ix_accounting_events_accounting_status",
        "accounting_events",
        ["accounting_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_accounting_events_accounting_status", "accounting_events")
    op.drop_index("ix_accounting_events_customer_id", "accounting_events")
    op.drop_index("ix_accounting_events_contract_id", "accounting_events")
    op.drop_index("ix_accounting_events_event_reference", "accounting_events")
    op.drop_index("ix_accounting_events_event_type", "accounting_events")
    op.drop_table("accounting_events")
