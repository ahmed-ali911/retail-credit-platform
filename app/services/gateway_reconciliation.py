"""Mock Payment Gateway — daily settlement-batch reconciliation.

See ``models/gateway_settlement.py`` for why this is a distinct engine from
the existing bank-reconciliation module rather than an extension of it.

Nothing here touches a contract's balance, a Payment's allocation, or
Collections — this layer only *compares* the gateway's settlement feed
against what this app's own webhook processing already recorded, and flags
what doesn't line up. Manual resolution goes through the existing generic
maker-checker (``services/approvals.py``), never applied directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import AccountingEventType
from app.models.gateway_settlement import (
    GatewayReconciliationItemStatus,
    ReconciliationItem,
    ReconciliationOutcome,
    SettlementBatch,
)
from app.models.payment import Payment, PaymentReconciliationStatus, PaymentSource
from app.models.payment_gateway import GatewayTransaction, PaymentIntent, PaymentIntentStatus
from app.services import accounting
from app.services.audit import record_event
from app.services.errors import DomainError

_CENTS = Decimal("0.01")


def _money(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BatchItemInput:
    gateway_transaction_reference: str
    merchant_reference: str
    settlement_date: date
    gross_amount: Decimal
    gateway_fee: Decimal
    net_amount: Decimal
    currency: str
    gateway_status: str


@dataclass
class ImportSummary:
    batch: SettlementBatch
    items_processed: int = 0
    matched: int = 0
    exceptions: int = 0
    missing_in_gateway: int = 0


def _settled_transaction_for(db: Session, intent_id: int) -> GatewayTransaction | None:
    return db.execute(
        select(GatewayTransaction)
        .where(
            GatewayTransaction.payment_intent_id == intent_id,
            GatewayTransaction.gateway_status == PaymentIntentStatus.settled,
        )
        .order_by(GatewayTransaction.id.desc())
    ).scalars().first()


def _classify_item(
    db: Session, item: BatchItemInput, *, seen_refs: set[str]
) -> tuple[ReconciliationOutcome, int | None, int | None, Decimal | None]:
    """Returns (outcome, matched_intent_id, matched_payment_id, variance_amount)."""
    if item.gateway_transaction_reference in seen_refs:
        return ReconciliationOutcome.duplicate, None, None, None
    seen_refs.add(item.gateway_transaction_reference)

    if not item.merchant_reference:
        return ReconciliationOutcome.unresolved, None, None, None

    intent = db.execute(
        select(PaymentIntent).where(PaymentIntent.payment_reference == item.merchant_reference)
    ).scalar_one_or_none()
    if intent is None:
        return ReconciliationOutcome.missing_in_internal_system, None, None, None

    txn = _settled_transaction_for(db, intent.id)
    if txn is None or intent.status != PaymentIntentStatus.settled:
        # The gateway's feed says this settled; our own webhook processing
        # hasn't reached (or has since moved past, e.g. a reversal) SETTLED.
        return ReconciliationOutcome.status_mismatch, intent.id, None, None

    our_gross = _money(txn.settled_amount)
    batch_gross = _money(item.gross_amount)
    if our_gross != batch_gross:
        variance = (batch_gross or Decimal("0")) - (our_gross or Decimal("0"))
        return ReconciliationOutcome.amount_mismatch, intent.id, None, variance

    our_date = txn.settlement_timestamp.date() if txn.settlement_timestamp else None
    if our_date is not None and our_date != item.settlement_date:
        return ReconciliationOutcome.date_mismatch, intent.id, None, None

    payment = db.execute(
        select(Payment).where(
            Payment.payment_intent_id == intent.id, Payment.source == PaymentSource.gateway
        )
    ).scalars().first()
    return ReconciliationOutcome.matched, intent.id, (payment.id if payment else None), None


def import_settlement_batch(
    db: Session,
    *,
    batch_reference: str,
    settlement_date: date,
    currency: str = "KWD",
    items: list[BatchItemInput],
    actor_id: int | None,
) -> ImportSummary:
    existing = db.execute(
        select(SettlementBatch).where(SettlementBatch.batch_reference == batch_reference)
    ).scalar_one_or_none()
    if existing is not None:
        raise DomainError(
            f"Settlement batch {batch_reference!r} has already been imported", status_code=409
        )

    batch = SettlementBatch(
        batch_reference=batch_reference,
        settlement_date=settlement_date,
        currency=currency,
        item_count=len(items),
        total_gross_amount=sum((_money(i.gross_amount) or Decimal("0") for i in items), Decimal("0")),
        total_gateway_fee=sum((_money(i.gateway_fee) or Decimal("0") for i in items), Decimal("0")),
        total_net_amount=sum((_money(i.net_amount) or Decimal("0") for i in items), Decimal("0")),
        imported_by=actor_id,
    )
    db.add(batch)
    db.flush()

    summary = ImportSummary(batch=batch)
    seen_refs: set[str] = set()
    batch_merchant_refs: set[str] = set()

    for item in items:
        summary.items_processed += 1
        if item.merchant_reference:
            batch_merchant_refs.add(item.merchant_reference)
        outcome, intent_id, payment_id, variance = _classify_item(db, item, seen_refs=seen_refs)

        row = ReconciliationItem(
            settlement_batch_id=batch.id,
            settlement_date=item.settlement_date,
            gateway_transaction_reference=item.gateway_transaction_reference,
            merchant_reference=item.merchant_reference,
            gross_amount=_money(item.gross_amount),
            gateway_fee=_money(item.gateway_fee),
            net_amount=_money(item.net_amount),
            currency=item.currency,
            gateway_reported_status=item.gateway_status,
            outcome=outcome,
            status=(
                GatewayReconciliationItemStatus.resolved
                if outcome == ReconciliationOutcome.matched
                else GatewayReconciliationItemStatus.open
            ),
            matched_intent_id=intent_id,
            matched_payment_id=payment_id,
            variance_amount=variance,
            resolved_at=_utcnow() if outcome == ReconciliationOutcome.matched else None,
        )
        db.add(row)
        db.flush()

        if outcome == ReconciliationOutcome.matched:
            summary.matched += 1
            if payment_id is not None:
                payment = db.get(Payment, payment_id)
                if payment is not None:
                    payment.reconciliation_status = PaymentReconciliationStatus.reconciled
        else:
            summary.exceptions += 1

    # Reverse sweep: our own SETTLED transactions on this date that the
    # gateway's feed never mentioned at all.
    our_settled = db.execute(
        select(GatewayTransaction, PaymentIntent)
        .join(PaymentIntent, GatewayTransaction.payment_intent_id == PaymentIntent.id)
        .where(GatewayTransaction.gateway_status == PaymentIntentStatus.settled)
    ).all()
    for txn, intent in our_settled:
        if txn.settlement_timestamp is None or txn.settlement_timestamp.date() != settlement_date:
            continue
        if intent.payment_reference in batch_merchant_refs:
            continue
        row = ReconciliationItem(
            settlement_batch_id=batch.id,
            settlement_date=settlement_date,
            gateway_transaction_reference=txn.gateway_transaction_reference,
            merchant_reference=intent.payment_reference,
            gross_amount=_money(txn.settled_amount),
            gateway_fee=_money(txn.gateway_fee),
            net_amount=(
                _money(txn.settled_amount) - _money(txn.gateway_fee)
                if txn.settled_amount is not None and txn.gateway_fee is not None
                else None
            ),
            currency=intent.currency,
            gateway_reported_status=None,
            outcome=ReconciliationOutcome.missing_in_gateway,
            status=GatewayReconciliationItemStatus.open,
            matched_intent_id=intent.id,
            variance_amount=_money(txn.settled_amount),
        )
        db.add(row)
        summary.items_processed += 1
        summary.missing_in_gateway += 1
        summary.exceptions += 1

    db.flush()
    record_event(
        db,
        user_id=actor_id,
        action="gateway_reconciliation.batch_imported",
        entity_type="settlement_batch",
        entity_id=batch.id,
        after={
            "batch_reference": batch_reference,
            "settlement_date": settlement_date.isoformat(),
            "items_processed": summary.items_processed,
            "matched": summary.matched,
            "exceptions": summary.exceptions,
        },
    )
    return summary


# --------------------------------------------------------------------------- #
# Manual resolution (executed by the approval workflow — see approvals._execute)
# --------------------------------------------------------------------------- #
def apply_resolution(
    db: Session,
    item: ReconciliationItem,
    *,
    actor_id: int | None,
    reason: str,
    comments: str | None,
) -> None:
    if item.status != GatewayReconciliationItemStatus.open:
        raise DomainError(
            f"Reconciliation item {item.id} is already {item.status.value}", status_code=409
        )

    item.status = GatewayReconciliationItemStatus.resolved
    item.resolution_reason = reason
    item.resolution_comments = comments
    item.resolved_by = actor_id
    item.resolved_at = _utcnow()

    if item.matched_payment_id is not None:
        payment = db.get(Payment, item.matched_payment_id)
        if payment is not None:
            payment.reconciliation_status = PaymentReconciliationStatus.reconciled

    # A resolved variance that has a real contract behind it (i.e. we know
    # which intent it concerns) is booked as a SETTLEMENT_DIFFERENCE
    # accounting event — the confirmed monetary gap between what the gateway
    # says it settled and what this app recorded. No event for outcomes with
    # no monetary gap (STATUS_MISMATCH/DATE_MISMATCH with matching amounts,
    # DUPLICATE) or with no known contract (MISSING_IN_INTERNAL_SYSTEM).
    if item.variance_amount and item.variance_amount != 0 and item.matched_intent_id is not None:
        intent = db.get(PaymentIntent, item.matched_intent_id)
        if intent is not None:
            accounting.emit(
                db,
                event_type=AccountingEventType.settlement_difference,
                event_reference=f"settlement-difference-{item.id}",
                contract=intent.contract,
                amount=item.variance_amount,
                event_date=_utcnow(),
                source_table="gateway_reconciliation_item",
                source_id=item.id,
            )

    record_event(
        db,
        user_id=actor_id,
        action="gateway_reconciliation.item_resolved",
        entity_type="gateway_reconciliation_item",
        entity_id=item.id,
        before={"status": "open"},
        after={"status": "resolved", "outcome": item.outcome.value, "reason": reason},
    )
    db.flush()
