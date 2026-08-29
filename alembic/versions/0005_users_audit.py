"""users, audit events, customer.user_id

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("before_value", sa.JSON(), nullable=True),
        sa.Column("after_value", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_entity_type", "audit_events", ["entity_type"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])

    with op.batch_alter_table("customers") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_customers_user_id", "users", ["user_id"], ["id"]
        )
        batch.create_index("ix_customers_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.drop_index("ix_customers_user_id")
        batch.drop_constraint("fk_customers_user_id", type_="foreignkey")
        batch.drop_column("user_id")

    op.drop_index("ix_audit_events_timestamp", "audit_events")
    op.drop_index("ix_audit_events_entity_id", "audit_events")
    op.drop_index("ix_audit_events_entity_type", "audit_events")
    op.drop_index("ix_audit_events_action", "audit_events")
    op.drop_index("ix_audit_events_user_id", "audit_events")
    op.drop_table("audit_events")
    op.drop_table("users")
