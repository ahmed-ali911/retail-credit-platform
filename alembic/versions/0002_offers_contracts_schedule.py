"""offers, sales orders, installment contracts, payment schedule

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "installment_offers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("cash_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("down_payment", sa.Numeric(14, 2), nullable=False),
        sa.Column("tenor_months", sa.Integer(), nullable=False),
        sa.Column("profit_rate", sa.Numeric(8, 4), nullable=False),
        sa.Column("installment_sale_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_profit", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_financed", sa.Numeric(14, 2), nullable=False),
        sa.Column("schedule_preview", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("down_payment_confirmed", sa.Boolean(), nullable=False),
        sa.Column("down_payment_reference", sa.String(length=100), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["credit_applications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_installment_offers_application_id", "installment_offers", ["application_id"]
    )

    op.create_table(
        "sales_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("sale_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("down_payment_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["credit_applications.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["offer_id"], ["installment_offers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("offer_id"),
    )
    op.create_index(
        "ix_sales_orders_application_id", "sales_orders", ["application_id"]
    )
    op.create_index("ix_sales_orders_product_id", "sales_orders", ["product_id"])

    op.create_table(
        "installment_contracts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("tenor_months", sa.Integer(), nullable=False),
        sa.Column("total_profit", sa.Numeric(14, 2), nullable=False),
        sa.Column("unearned_profit_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sales_order_id"),
    )

    op.create_table(
        "payment_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id"),
    )

    op.create_table(
        "installments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("principal_component", sa.Numeric(14, 2), nullable=False),
        sa.Column("profit_component", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["payment_schedules.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_installments_contract_id", "installments", ["contract_id"])


def downgrade() -> None:
    op.drop_index("ix_installments_contract_id", "installments")
    op.drop_table("installments")
    op.drop_table("payment_schedules")
    op.drop_table("installment_contracts")
    op.drop_index("ix_sales_orders_product_id", "sales_orders")
    op.drop_index("ix_sales_orders_application_id", "sales_orders")
    op.drop_table("sales_orders")
    op.drop_index("ix_installment_offers_application_id", "installment_offers")
    op.drop_table("installment_offers")
