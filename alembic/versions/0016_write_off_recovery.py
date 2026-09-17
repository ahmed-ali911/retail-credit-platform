"""Write-off & Recovery: write_off_requests, write_off_executions,
write_off_recoveries; extends installments (principal_written_off,
profit_written_off), late_fee_charges (amount_written_off), collection_cases
(closed_reason).

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-18

Additive only — no existing column is dropped, renamed, or retyped, and no
existing row's data changes. The three new *_written_off columns default
every pre-existing (and every future non-write-off) row to 0.00, so
principal_outstanding/profit_outstanding/LateFeeCharge.outstanding are
numerically unchanged until a write-off actually happens (a later
checkpoint — this migration only lands the domain model; nothing writes to
write_off_executions or write_off_recoveries yet). No enum value added in
this checkpoint required a schema change: every enum in this codebase is
``native_enum=False`` (a plain, unconstrained string column), so a new
Python enum member (InstallmentStatus.written_off, LateFeeStatus.
written_off, ClosureReason.write_off, LedgerEntryType.*_written_off,
LedgerRelatedAction.write_off, AccountingEventType.write_off_executed /
partial_write_off_executed / recovery_received / recovery_adjustment) is a
code-only addition.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUM142 = sa.Numeric(14, 2)
_NUM162 = sa.Numeric(16, 2)


def upgrade() -> None:
    # --- 1. extend installments -----------------------------------------
    with op.batch_alter_table("installments") as b:
        b.add_column(sa.Column("principal_written_off", _NUM142, nullable=False, server_default="0"))
        b.add_column(sa.Column("profit_written_off", _NUM142, nullable=False, server_default="0"))

    # --- 2. extend late_fee_charges ---------------------------------------
    with op.batch_alter_table("late_fee_charges") as b:
        b.add_column(sa.Column("amount_written_off", _NUM142, nullable=False, server_default="0"))

    # --- 3. extend collection_cases ---------------------------------------
    with op.batch_alter_table("collection_cases") as b:
        b.add_column(sa.Column("closed_reason", sa.String(length=20), nullable=True))

    # --- 4. write_off_requests --------------------------------------------
    op.create_table(
        "write_off_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("write_off_type", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="PENDING"),
        sa.Column("reason_code", sa.String(length=35), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.String(length=255), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("eligibility_status", sa.String(length=15), nullable=False),
        sa.Column("eligibility_snapshot", sa.JSON(), nullable=False),
        sa.Column("is_exception", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exception_justification", sa.Text(), nullable=True),
        sa.Column("snapshot_principal_outstanding", _NUM142, nullable=False),
        sa.Column("snapshot_profit_outstanding", _NUM142, nullable=False),
        sa.Column("snapshot_late_fee_outstanding", _NUM142, nullable=False),
        sa.Column("snapshot_other_charges_outstanding", _NUM142, nullable=False, server_default="0"),
        sa.Column("snapshot_total_outstanding", _NUM142, nullable=False),
        sa.Column("snapshot_dpd", sa.Integer(), nullable=True),
        sa.Column("snapshot_ecl_stage", sa.Integer(), nullable=True),
        sa.Column("snapshot_ecl_amount", _NUM162, nullable=True),
        sa.Column("snapshot_provision_amount", _NUM162, nullable=True),
        sa.Column("snapshot_collections_case_status", sa.String(length=20), nullable=True),
        sa.Column("snapshot_collections_case_id", sa.Integer(), nullable=True),
        sa.Column("requested_principal", _NUM142, nullable=False, server_default="0"),
        sa.Column("requested_profit", _NUM142, nullable=False, server_default="0"),
        sa.Column("requested_late_fee", _NUM142, nullable=False, server_default="0"),
        sa.Column("requested_other_charges", _NUM142, nullable=False, server_default="0"),
        sa.Column("approval_request_id", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["snapshot_collections_case_id"], ["collection_cases.id"]),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_write_off_requests_contract_id", "write_off_requests", ["contract_id"])
    op.create_index("ix_write_off_requests_customer_id", "write_off_requests", ["customer_id"])
    op.create_index("ix_write_off_requests_status", "write_off_requests", ["status"])

    # --- 5. write_off_executions (schema only — nothing writes here yet) ---
    op.create_table(
        "write_off_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("write_off_request_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("write_off_type", sa.String(length=10), nullable=False),
        sa.Column("executed_principal", _NUM142, nullable=False, server_default="0"),
        sa.Column("executed_profit", _NUM142, nullable=False, server_default="0"),
        sa.Column("executed_late_fee", _NUM142, nullable=False, server_default="0"),
        sa.Column("executed_other_charges", _NUM142, nullable=False, server_default="0"),
        sa.Column("remaining_principal", _NUM142, nullable=False, server_default="0"),
        sa.Column("remaining_profit", _NUM142, nullable=False, server_default="0"),
        sa.Column("remaining_late_fee", _NUM142, nullable=False, server_default="0"),
        sa.Column("ecl_stage_snapshot", sa.Integer(), nullable=True),
        sa.Column("ecl_amount_snapshot", _NUM162, nullable=True),
        sa.Column("provision_amount_snapshot", _NUM162, nullable=True),
        sa.Column("contract_closure_id", sa.Integer(), nullable=True),
        sa.Column("collection_case_id", sa.Integer(), nullable=True),
        sa.Column("accounting_event_id", sa.Integer(), nullable=True),
        sa.Column("executed_by", sa.Integer(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["write_off_request_id"], ["write_off_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["contract_closure_id"], ["contract_closures.id"]),
        sa.ForeignKeyConstraint(["collection_case_id"], ["collection_cases.id"]),
        sa.ForeignKeyConstraint(["accounting_event_id"], ["accounting_events.id"]),
        sa.ForeignKeyConstraint(["executed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("write_off_request_id", name="uq_write_off_execution_request"),
    )
    op.create_index("ix_write_off_executions_contract_id", "write_off_executions", ["contract_id"])

    # --- 6. write_off_recoveries (schema only — nothing writes here yet) ---
    op.create_table(
        "write_off_recoveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("write_off_execution_id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("external_reference", sa.String(length=120), nullable=False),
        sa.Column("amount", _NUM142, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KWD"),
        sa.Column("recovery_date", sa.Date(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=True),
        sa.Column("allocated_principal", _NUM142, nullable=False, server_default="0"),
        sa.Column("allocated_profit", _NUM142, nullable=False, server_default="0"),
        sa.Column("allocated_late_fee", _NUM142, nullable=False, server_default="0"),
        sa.Column("allocated_other_charges", _NUM142, nullable=False, server_default="0"),
        sa.Column("accounting_event_id", sa.Integer(), nullable=True),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["write_off_execution_id"], ["write_off_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.ForeignKeyConstraint(["accounting_event_id"], ["accounting_events.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "write_off_execution_id", "external_reference", name="uq_recovery_execution_reference"
        ),
    )
    op.create_index(
        "ix_write_off_recoveries_execution_id", "write_off_recoveries", ["write_off_execution_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_write_off_recoveries_execution_id", "write_off_recoveries")
    op.drop_table("write_off_recoveries")

    op.drop_index("ix_write_off_executions_contract_id", "write_off_executions")
    op.drop_table("write_off_executions")

    op.drop_index("ix_write_off_requests_status", "write_off_requests")
    op.drop_index("ix_write_off_requests_customer_id", "write_off_requests")
    op.drop_index("ix_write_off_requests_contract_id", "write_off_requests")
    op.drop_table("write_off_requests")

    with op.batch_alter_table("collection_cases") as b:
        b.drop_column("closed_reason")

    with op.batch_alter_table("late_fee_charges") as b:
        b.drop_column("amount_written_off")

    with op.batch_alter_table("installments") as b:
        b.drop_column("profit_written_off")
        b.drop_column("principal_written_off")
