"""Chart of Accounts + Versioned Posting Rules + Double-Entry Journal:
chart_of_accounts, event_account_mappings, event_account_mapping_lines,
gl_journals, gl_journal_lines; extends accounting_events (source_table,
source_id).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-18

Additive only — no existing column is dropped, renamed, or retyped, and no
existing row's data changes. `accounting_events.source_table`/`source_id`
are nullable and populated going forward only (every pre-existing row stays
NULL). `gl_journals`/`gl_journal_lines` are defined now, with the rest of
the domain model, so this migration lands once — journal GENERATION (the
code that actually writes to these two tables) is a later checkpoint,
mirroring exactly how migration 0016 landed `write_off_executions` and
`write_off_recoveries` a full checkpoint before either table was written to.
No enum value added in this checkpoint required a schema change beyond the
five new tables and two new columns below: every enum in this codebase is
``native_enum=False`` (a plain, unconstrained string column).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUM142 = sa.Numeric(14, 2)


def upgrade() -> None:
    # --- 1. extend accounting_events --------------------------------------
    with op.batch_alter_table("accounting_events") as b:
        b.add_column(sa.Column("source_table", sa.String(length=40), nullable=True))
        b.add_column(sa.Column("source_id", sa.Integer(), nullable=True))

    # --- 2. chart_of_accounts ----------------------------------------------
    op.create_table(
        "chart_of_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_code", sa.String(length=20), nullable=False),
        sa.Column("account_name", sa.String(length=120), nullable=False),
        sa.Column("account_type", sa.String(length=15), nullable=False),
        sa.Column("normal_balance", sa.String(length=10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_code", name="uq_chart_of_accounts_code"),
    )
    op.create_index("ix_chart_of_accounts_account_code", "chart_of_accounts", ["account_code"])

    # --- 3. event_account_mappings ------------------------------------------
    op.create_table(
        "event_account_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_event_type", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=15), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approval_request_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_event_type", "version", name="uq_mapping_event_type_version"
        ),
    )
    op.create_index(
        "ix_event_account_mappings_event_type", "event_account_mappings", ["account_event_type"]
    )
    op.create_index(
        "ix_event_account_mappings_is_active", "event_account_mappings", ["is_active"]
    )

    # --- 4. event_account_mapping_lines -------------------------------------
    op.create_table(
        "event_account_mapping_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mapping_id", sa.Integer(), nullable=False),
        sa.Column("line_sequence", sa.Integer(), nullable=False),
        sa.Column("posting_side", sa.String(length=10), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("amount_source", sa.String(length=45), nullable=False),
        sa.Column("multiplier", sa.Numeric(6, 4), nullable=False, server_default="1"),
        sa.Column("reverse_on_negative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["mapping_id"], ["event_account_mappings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["chart_of_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_account_mapping_lines_mapping_id", "event_account_mapping_lines", ["mapping_id"]
    )

    # --- 5. gl_journals (schema only — nothing writes here yet) ------------
    op.create_table(
        "gl_journals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("journal_reference", sa.String(length=40), nullable=False),
        sa.Column("accounting_event_id", sa.Integer(), nullable=False),
        sa.Column("mapping_version_id", sa.Integer(), nullable=True),
        sa.Column("journal_status", sa.String(length=15), nullable=False, server_default="UNMAPPED"),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posting_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accounting_period", sa.String(length=7), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KWD"),
        sa.Column("total_debit", _NUM142, nullable=False, server_default="0"),
        sa.Column("total_credit", _NUM142, nullable=False, server_default="0"),
        sa.Column("is_balanced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("external_gl_reference", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["accounting_event_id"], ["accounting_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mapping_version_id"], ["event_account_mappings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("journal_reference", name="uq_gl_journals_reference"),
        sa.UniqueConstraint("accounting_event_id", name="uq_gl_journals_event"),
    )
    op.create_index("ix_gl_journals_reference", "gl_journals", ["journal_reference"])
    op.create_index("ix_gl_journals_event_id", "gl_journals", ["accounting_event_id"])
    op.create_index("ix_gl_journals_status", "gl_journals", ["journal_status"])

    # --- 6. gl_journal_lines (schema only — nothing writes here yet) -------
    op.create_table(
        "gl_journal_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("journal_id", sa.Integer(), nullable=False),
        sa.Column("line_sequence", sa.Integer(), nullable=False),
        sa.Column("posting_side", sa.String(length=10), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("account_code_snapshot", sa.String(length=20), nullable=False),
        sa.Column("account_name_snapshot", sa.String(length=120), nullable=False),
        sa.Column("amount", _NUM142, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KWD"),
        sa.Column("amount_source", sa.String(length=45), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["journal_id"], ["gl_journals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["chart_of_accounts.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["installment_contracts.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gl_journal_lines_journal_id", "gl_journal_lines", ["journal_id"])


def downgrade() -> None:
    op.drop_index("ix_gl_journal_lines_journal_id", "gl_journal_lines")
    op.drop_table("gl_journal_lines")

    op.drop_index("ix_gl_journals_status", "gl_journals")
    op.drop_index("ix_gl_journals_event_id", "gl_journals")
    op.drop_index("ix_gl_journals_reference", "gl_journals")
    op.drop_table("gl_journals")

    op.drop_index("ix_event_account_mapping_lines_mapping_id", "event_account_mapping_lines")
    op.drop_table("event_account_mapping_lines")

    op.drop_index("ix_event_account_mappings_is_active", "event_account_mappings")
    op.drop_index("ix_event_account_mappings_event_type", "event_account_mappings")
    op.drop_table("event_account_mappings")

    op.drop_index("ix_chart_of_accounts_account_code", "chart_of_accounts")
    op.drop_table("chart_of_accounts")

    with op.batch_alter_table("accounting_events") as b:
        b.drop_column("source_id")
        b.drop_column("source_table")
