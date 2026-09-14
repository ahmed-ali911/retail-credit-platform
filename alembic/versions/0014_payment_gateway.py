"""Mock Payment Gateway: payment_intents, gateway_transactions, webhook_events;
extends payments (source, payment_intent_id), payment_allocations
(reversed_by_allocation_id), AccountingEventType, PromiseStatus.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-14

Additive only — no existing column is dropped, renamed, or retyped, and no
existing row's data changes. `payments.source` defaults every pre-existing
(and every future staff-entered) row to 'staff'; `payments.payment_intent_id`
/ `payment_allocations.reversed_by_allocation_id` are nullable FKs, NULL
everywhere until a gateway payment/reversal actually happens.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUM142 = sa.Numeric(14, 2)


def upgrade() -> None:
    # --- 1. payment_intents ------------------------------------------------
    op.create_table(
        "payment_intents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("payment_reference", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("requested_amount", _NUM142, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KWD"),
        sa.Column("payment_purpose", sa.String(length=25), nullable=False),
        sa.Column("status", sa.String(length=25), nullable=False, server_default="INITIATED"),
        sa.Column("gateway_session_id", sa.String(length=100), nullable=True),
        sa.Column(
            "gateway_name", sa.String(length=50), nullable=False,
            server_default="mock-payment-gateway",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["installment_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_reference", name="uq_payment_intent_reference"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_intent_idempotency_key"),
    )
    op.create_index("ix_payment_intents_customer_id", "payment_intents", ["customer_id"])
    op.create_index("ix_payment_intents_contract_id", "payment_intents", ["contract_id"])
    op.create_index("ix_payment_intents_status", "payment_intents", ["status"])
    op.create_index(
        "ix_payment_intents_gateway_session_id", "payment_intents", ["gateway_session_id"]
    )

    # --- 2. gateway_transactions --------------------------------------------
    op.create_table(
        "gateway_transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("payment_intent_id", sa.Integer(), nullable=False),
        sa.Column("gateway_transaction_reference", sa.String(length=100), nullable=False),
        sa.Column("gateway_status", sa.String(length=25), nullable=False),
        sa.Column("authorized_amount", _NUM142, nullable=True),
        sa.Column("captured_amount", _NUM142, nullable=True),
        sa.Column("settled_amount", _NUM142, nullable=True),
        sa.Column("gateway_fee", _NUM142, nullable=True),
        sa.Column("authorization_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capture_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=50), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("raw_response_reference", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_intent_id"], ["payment_intents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gateway_transactions_payment_intent_id", "gateway_transactions", ["payment_intent_id"]
    )
    op.create_index(
        "ix_gateway_transactions_reference", "gateway_transactions",
        ["gateway_transaction_reference"],
    )

    # --- 3. webhook_events ---------------------------------------------------
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("gateway_event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payment_reference", sa.String(length=64), nullable=False),
        sa.Column("claimed_status", sa.String(length=25), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("processing_status", sa.String(length=30), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gateway_event_id", name="uq_webhook_event_gateway_event_id"),
    )
    op.create_index("ix_webhook_events_payment_reference", "webhook_events", ["payment_reference"])

    # --- 4. extend payments ---------------------------------------------------
    with op.batch_alter_table("payments") as b:
        b.add_column(
            sa.Column("source", sa.String(length=10), nullable=False, server_default="staff")
        )
        b.add_column(sa.Column("payment_intent_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_payments_payment_intent", "payment_intents", ["payment_intent_id"], ["id"]
        )
    op.create_index("ix_payments_payment_intent_id", "payments", ["payment_intent_id"])

    # --- 5. extend payment_allocations ----------------------------------------
    with op.batch_alter_table("payment_allocations") as b:
        b.add_column(sa.Column("reversed_by_allocation_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_payment_allocations_reversed_by",
            "payment_allocations",
            ["reversed_by_allocation_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("payment_allocations") as b:
        b.drop_constraint("fk_payment_allocations_reversed_by", type_="foreignkey")
        b.drop_column("reversed_by_allocation_id")

    op.drop_index("ix_payments_payment_intent_id", "payments")
    with op.batch_alter_table("payments") as b:
        b.drop_constraint("fk_payments_payment_intent", type_="foreignkey")
        b.drop_column("payment_intent_id")
        b.drop_column("source")

    op.drop_index("ix_webhook_events_payment_reference", "webhook_events")
    op.drop_table("webhook_events")

    op.drop_index("ix_gateway_transactions_reference", "gateway_transactions")
    op.drop_index("ix_gateway_transactions_payment_intent_id", "gateway_transactions")
    op.drop_table("gateway_transactions")

    op.drop_index("ix_payment_intents_gateway_session_id", "payment_intents")
    op.drop_index("ix_payment_intents_status", "payment_intents")
    op.drop_index("ix_payment_intents_contract_id", "payment_intents")
    op.drop_index("ix_payment_intents_customer_id", "payment_intents")
    op.drop_table("payment_intents")
