"""Immutable financial ledger — write helper (Phase 1: dual-write only).

Every money-relevant movement in payment allocation, early settlement,
cancellation, return, and late-fee waiver writes a `LedgerEntry` here **in
addition to** the existing in-place balance mutation. Nothing reads the ledger
yet; `tests/test_ledger.py` proves that summing the entries reproduces the
figures the existing (unchanged) calculations already report.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models.ledger import LedgerEntry, LedgerEntryType, LedgerRelatedAction

_CENTS = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def record_entry(
    db: Session,
    *,
    contract_id: int,
    entry_type: LedgerEntryType,
    amount,
    related_action: LedgerRelatedAction,
    reference_type: str,
    reference_id: int,
    created_by: int | None = None,
) -> LedgerEntry:
    entry = LedgerEntry(
        contract_id=contract_id,
        entry_type=entry_type,
        amount=_money(amount),
        related_action=related_action,
        reference_type=reference_type,
        reference_id=reference_id,
        created_by=created_by,
    )
    db.add(entry)
    return entry
