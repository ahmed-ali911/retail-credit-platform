"""payments, allocations, late fee charges; installment paid columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "installments",
        sa.Column(
            "principal_paid", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "installments",
        sa.Column(
            "profit_paid", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("external_reference", sa.String(length=100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("unallocated_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contract_id", "external_reference", name="uq_payment_ref"
        ),
    )
    op.create_index("ix_payments_contract_id", "payments", ["contract_id"])

    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("installment_id", sa.Integer(), nullable=False),
        sa.Column("late_fee_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("profit_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("principal_amount", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["installment_id"], ["installments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_allocations_payment_id", "payment_allocations", ["payment_id"]
    )
    op.create_index(
        "ix_payment_allocations_installment_id",
        "payment_allocations",
        ["installment_id"],
    )

    op.create_table(
        "late_fee_charges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("installment_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_paid", sa.Numeric(14, 2), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["installment_id"], ["installments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_late_fee_charges_installment_id", "late_fee_charges", ["installment_id"]
    )
    op.create_index(
        "ix_late_fee_charges_contract_id", "late_fee_charges", ["contract_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_late_fee_charges_contract_id", "late_fee_charges")
    op.drop_index("ix_late_fee_charges_installment_id", "late_fee_charges")
    op.drop_table("late_fee_charges")
    op.drop_index("ix_payment_allocations_installment_id", "payment_allocations")
    op.drop_index("ix_payment_allocations_payment_id", "payment_allocations")
    op.drop_table("payment_allocations")
    op.drop_index("ix_payments_contract_id", "payments")
    op.drop_table("payments")
    op.drop_column("installments", "profit_paid")
    op.drop_column("installments", "principal_paid")
