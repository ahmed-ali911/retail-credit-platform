"""Mock Payment Gateway: settlement_batches, gateway_reconciliation_items.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-14

Additive only — a distinct engine from the existing bank-reconciliation
tables (bank_statement_lines / reconciliation_exceptions), not a change to
them; see app/models/gateway_settlement.py for the reuse-decision writeup.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUM142 = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "settlement_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_reference", sa.String(length=100), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column(
            "gateway_name", sa.String(length=50), nullable=False,
            server_default="mock-payment-gateway",
        ),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KWD"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_gross_amount", _NUM142, nullable=False, server_default="0"),
        sa.Column("total_gateway_fee", _NUM142, nullable=False, server_default="0"),
        sa.Column("total_net_amount", _NUM142, nullable=False, server_default="0"),
        sa.Column("imported_by", sa.Integer(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["imported_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_reference", name="uq_settlement_batch_reference"),
    )
    op.create_index(
        "ix_settlement_batches_settlement_date", "settlement_batches", ["settlement_date"]
    )

    op.create_table(
        "gateway_reconciliation_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("settlement_batch_id", sa.Integer(), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("gateway_transaction_reference", sa.String(length=100), nullable=True),
        sa.Column("merchant_reference", sa.String(length=64), nullable=True),
        sa.Column("gross_amount", _NUM142, nullable=True),
        sa.Column("gateway_fee", _NUM142, nullable=True),
        sa.Column("net_amount", _NUM142, nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KWD"),
        sa.Column("gateway_reported_status", sa.String(length=25), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("matched_payment_id", sa.Integer(), nullable=True),
        sa.Column("matched_intent_id", sa.Integer(), nullable=True),
        sa.Column("variance_amount", _NUM142, nullable=True),
        sa.Column("resolution_reason", sa.String(length=255), nullable=True),
        sa.Column("resolution_comments", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["settlement_batch_id"], ["settlement_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["matched_payment_id"], ["payments.id"]),
        sa.ForeignKeyConstraint(["matched_intent_id"], ["payment_intents.id"]),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gateway_recon_items_batch_id", "gateway_reconciliation_items", ["settlement_batch_id"]
    )
    op.create_index(
        "ix_gateway_recon_items_settlement_date",
        "gateway_reconciliation_items", ["settlement_date"],
    )
    op.create_index(
        "ix_gateway_recon_items_merchant_reference",
        "gateway_reconciliation_items", ["merchant_reference"],
    )
    op.create_index(
        "ix_gateway_recon_items_status", "gateway_reconciliation_items", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_gateway_recon_items_status", "gateway_reconciliation_items")
    op.drop_index("ix_gateway_recon_items_merchant_reference", "gateway_reconciliation_items")
    op.drop_index("ix_gateway_recon_items_settlement_date", "gateway_reconciliation_items")
    op.drop_index("ix_gateway_recon_items_batch_id", "gateway_reconciliation_items")
    op.drop_table("gateway_reconciliation_items")

    op.drop_index("ix_settlement_batches_settlement_date", "settlement_batches")
    op.drop_table("settlement_batches")
