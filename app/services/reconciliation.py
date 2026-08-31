"""Bank reconciliation — the boundary between "customer payment succeeded" and
"money is matched against the company's bank records" (P0-5, fixes S-5).

Payment recording and allocation are unchanged. This layer only *observes*: it
matches recorded `Payment` rows against mock `BankStatementLine` rows and flags
what it cannot match. Nothing here blocks or alters a payment, allocation or
closure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.payment import Payment, PaymentReconciliationStatus
from app.models.reconciliation import (
    BankStatementLine,
    ReconExceptionReason,
    ReconExceptionStatus,
    ReconciliationException,
)
from app.services import config_service as cfg
from app.services.audit import record_event
from app.services.config_service import ConfigService
from app.services.errors import DomainError

_CENTS = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MatchRunSummary:
    lines_processed: int = 0
    matched: int = 0
    exceptions_created: int = 0


# --------------------------------------------------------------------------- #
# Ingestion (mock adapter boundary — no real bank feed)
# --------------------------------------------------------------------------- #
def ingest_bank_line(
    db: Session,
    *,
    bank_reference: str,
    amount,
    value_date: date,
    actor_id: int | None,
) -> BankStatementLine:
    line = BankStatementLine(
        bank_reference=bank_reference.strip(),
        amount=_money(amount),
        value_date=value_date,
    )
    db.add(line)
    db.flush()
    record_event(
        db,
        user_id=actor_id,
        action="reconciliation.bank_line_ingested",
        entity_type="bank_statement_line",
        entity_id=line.id,
        after={"bank_reference": line.bank_reference, "amount": float(line.amount)},
    )
    return line


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _refs(payment: Payment) -> set[str]:
    refs = {payment.external_reference}
    if payment.gateway_reference:
        refs.add(payment.gateway_reference)
    return refs


def _classify(
    db: Session, line: BankStatementLine, tolerance_days: int
) -> tuple[Payment | None, ReconExceptionReason | None]:
    """Returns (matched payment, None) or (None, exception reason).

    Only `unreconciled` payments are eligible — so this is naturally idempotent.
    """
    unreconciled = (
        db.execute(
            select(Payment).where(
                Payment.reconciliation_status
                == PaymentReconciliationStatus.unreconciled
            )
        )
        .scalars()
        .all()
    )
    line_amount = _money(line.amount)

    # Rule 1 — exact reference match (external_reference or gateway_reference)
    by_ref = [p for p in unreconciled if line.bank_reference in _refs(p)]
    if len(by_ref) == 1:
        payment = by_ref[0]
        if _money(payment.amount) == line_amount:
            return payment, None
        return None, ReconExceptionReason.amount_mismatch
    if len(by_ref) > 1:
        return None, ReconExceptionReason.duplicate_candidate

    # Rule 2 — fallback: same amount, value date within tolerance
    by_amount_date = [
        p
        for p in unreconciled
        if _money(p.amount) == line_amount
        and abs((p.received_at.date() - line.value_date).days) <= tolerance_days
    ]
    if len(by_amount_date) == 1:
        return by_amount_date[0], None
    if len(by_amount_date) > 1:
        return None, ReconExceptionReason.duplicate_candidate

    # Rule 3 — nothing
    return None, ReconExceptionReason.no_match


def _link(
    db: Session,
    line: BankStatementLine,
    payment: Payment,
    *,
    actor_id: int | None,
    via: str,
) -> None:
    line.matched_payment_id = payment.id
    payment.reconciliation_status = PaymentReconciliationStatus.reconciled
    record_event(
        db,
        user_id=actor_id,
        action="payment.reconciled",
        entity_type="payment",
        entity_id=payment.id,
        after={"bank_line_id": line.id, "via": via},
    )


def run_matching(db: Session, *, actor_id: int | None) -> MatchRunSummary:
    tolerance = ConfigService(db).get_int(cfg.KEY_RECON_DATE_TOLERANCE_DAYS)

    # A line is "done" once it is matched OR already has an exception — so
    # re-running never re-matches or duplicates an exception.
    excepted = select(ReconciliationException.bank_line_id)
    lines = (
        db.execute(
            select(BankStatementLine)
            .where(
                BankStatementLine.matched_payment_id.is_(None),
                BankStatementLine.id.not_in(excepted),
            )
            .order_by(BankStatementLine.id)
        )
        .scalars()
        .all()
    )

    summary = MatchRunSummary()
    for line in lines:
        summary.lines_processed += 1
        payment, reason = _classify(db, line, tolerance)
        if payment is not None:
            _link(db, line, payment, actor_id=actor_id, via="auto")
            summary.matched += 1
            continue

        db.add(
            ReconciliationException(
                bank_line_id=line.id, reason=reason, status=ReconExceptionStatus.open
            )
        )
        summary.exceptions_created += 1
        if reason == ReconExceptionReason.amount_mismatch:
            # exactly one payment matched by reference but with the wrong amount
            by_ref = [
                p
                for p in db.execute(
                    select(Payment).where(
                        Payment.reconciliation_status
                        == PaymentReconciliationStatus.unreconciled
                    )
                )
                .scalars()
                .all()
                if line.bank_reference in _refs(p)
            ]
            if len(by_ref) == 1:
                by_ref[0].reconciliation_status = PaymentReconciliationStatus.exception
        record_event(
            db,
            user_id=actor_id,
            action="reconciliation.exception_opened",
            entity_type="bank_statement_line",
            entity_id=line.id,
            after={"reason": reason.value},
        )

    db.flush()
    return summary


# --------------------------------------------------------------------------- #
# Manual resolution (executed by the approval workflow — see approvals._execute)
# --------------------------------------------------------------------------- #
def apply_manual_match(
    db: Session,
    exception: ReconciliationException,
    payment: Payment,
    *,
    actor_id: int | None,
) -> None:
    if exception.status != ReconExceptionStatus.open:
        raise DomainError(
            f"Reconciliation exception {exception.id} is already "
            f"{exception.status.value}",
            status_code=409,
        )
    line = db.get(BankStatementLine, exception.bank_line_id)
    if line is None:
        raise DomainError("Bank line no longer exists", status_code=409)
    if line.matched_payment_id is not None:
        raise DomainError("Bank line is already matched", status_code=409)

    _link(db, line, payment, actor_id=actor_id, via="manual")
    exception.status = ReconExceptionStatus.resolved
    exception.resolved_at = _utcnow()
    exception.resolved_by = actor_id
    record_event(
        db,
        user_id=actor_id,
        action="reconciliation.manual_matched",
        entity_type="reconciliation_exception",
        entity_id=exception.id,
        before={"status": "open"},
        after={
            "status": "resolved",
            "payment_id": payment.id,
            "bank_line_id": line.id,
        },
    )
    db.flush()
