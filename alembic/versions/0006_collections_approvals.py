"""collections + maker-checker approvals; collections_officer role

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29

The `collections_officer` role needs no DDL — `users.role` is a plain
String(20) (native_enum=False), so widening the Python enum is enough.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_reason", sa.String(length=255), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_cases_contract_id", "collection_cases", ["contract_id"]
    )
    # At most one OPEN case per contract.
    op.create_index(
        "uq_open_collection_case_per_contract",
        "collection_cases",
        ["contract_id"],
        unique=True,
        sqlite_where=sa.text("status = 'open'"),
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "collection_activities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collection_case_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("activity_type", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promised_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("promised_date", sa.Date(), nullable=True),
        sa.Column("promise_status", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(
            ["collection_case_id"], ["collection_cases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_activities_collection_case_id",
        "collection_activities",
        ["collection_case_id"],
    )

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_requests_action_type", "approval_requests", ["action_type"]
    )
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_approval_requests_status", "approval_requests")
    op.drop_index("ix_approval_requests_action_type", "approval_requests")
    op.drop_table("approval_requests")
    op.drop_index(
        "ix_collection_activities_collection_case_id", "collection_activities"
    )
    op.drop_table("collection_activities")
    op.drop_index("uq_open_collection_case_per_contract", "collection_cases")
    op.drop_index("ix_collection_cases_contract_id", "collection_cases")
    op.drop_table("collection_cases")
