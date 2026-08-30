from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class LedgerEntryType(str, enum.Enum):
    principal_scheduled = "principal_scheduled"
    principal_paid = "principal_paid"
    profit_scheduled = "profit_scheduled"
    profit_recognized = "profit_recognized"
    profit_rebated = "profit_rebated"
    late_fee_charged = "late_fee_charged"
    late_fee_paid = "late_fee_paid"
    late_fee_waived = "late_fee_waived"
    down_payment_received = "down_payment_received"
    refund_issued = "refund_issued"


class LedgerRelatedAction(str, enum.Enum):
    origination = "origination"
    payment = "payment"
    settlement = "settlement"
    cancellation = "cancellation"
    return_ = "return"
    waiver = "waiver"


class LedgerEntry(Base):
    """Append-only financial ledger line.

    **Phase 1 (this migration): write-only.** Entries are written alongside the
    existing in-place balance mutations (dual-write). No read path uses the
    ledger yet — `GET /contracts/{id}/receivable` and every other calculation
    are unchanged. A later slice will make this the source of truth for reads
    once the reconciliation tests prove the dual-write is complete and correct.

    ``amount`` is signed from the customer's point of view where a sign is
    meaningful (``refund_issued``: positive = cash owed to the customer,
    negative = still owed by the customer). Paid/charged/rebated types are
    recorded as positive magnitudes.
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_entries_reference", "reference_type", "reference_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        Enum(LedgerEntryType, native_enum=False, length=30), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    related_action: Mapped[LedgerRelatedAction] = mapped_column(
        Enum(LedgerRelatedAction, native_enum=False, length=20), nullable=False
    )
    # Points at the row that caused this entry:
    # "payment" | "contract_closure" | "late_fee_charge" | "approval_request"
    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
