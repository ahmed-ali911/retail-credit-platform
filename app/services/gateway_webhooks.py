"""Mock Payment Gateway — inbound webhook processing (section 10).

The single entry point is ``process_webhook``. It never trusts the gateway's
claimed status blindly: every event is (1) checked for a signature, (2)
checked for replay/staleness, (3) checked for whether we've already recorded
this exact ``gateway_event_id`` (idempotency), and only then (4) checked
against the closed transition graph in ``models/payment_gateway.py`` before
anything in this database changes. A duplicate is acknowledged (200) without
reprocessing; an out-of-order event is acknowledged and recorded but not
applied; a bad signature is rejected outright.

Reaching the *configured* final-allocation status (SETTLED by default) calls
the existing payments.py::record_payment() engine (``_apply_final_allocation``
below); a later REVERSED/REFUNDED/CHARGEBACK calls the new
services/payment_reversal.py (``_apply_reversal`` below) to undo it via
compensating records.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.accounting import AccountingEventType
from app.models.payment import PaymentSource
from app.models.payment_gateway import (
    GatewayTransaction,
    PaymentIntent,
    PaymentIntentStatus,
    WebhookEvent,
    WebhookProcessingStatus,
    can_transition,
)
from app.services import accounting, webhook_security
from app.services.errors import DomainError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _d(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class WebhookOutcome:
    webhook_event: WebhookEvent
    http_status: int
    # Populated only when processing_status == PROCESSED — the settlement
    # hook's own result, once checkpoint 2 implements it. None otherwise.
    intent: PaymentIntent | None = None


def process_webhook(
    db: Session, *, headers: dict[str, str], raw_body: bytes
) -> WebhookOutcome:
    settings = get_settings()

    try:
        import json

        payload = json.loads(raw_body or b"{}")
    except ValueError:
        payload = {}

    gateway_event_id = str(payload.get("gateway_event_id") or "").strip()
    event_type = str(payload.get("event_type") or "")
    payment_reference = str(payload.get("payment_reference") or "")
    claimed_status = payload.get("status")
    event_timestamp = _parse_timestamp(payload.get("timestamp"))
    hash_ = webhook_security.payload_hash(raw_body)

    if not gateway_event_id:
        raise DomainError("gateway_event_id is required", status_code=400)

    # --- 1. idempotency: a previously-seen event is acknowledged, never
    #        reprocessed, regardless of what the resent envelope now says.
    #        gateway_event_id is UNIQUE (one row per real gateway event, not
    #        per delivery attempt) — a replayed delivery bumps retry_count on
    #        that SAME row rather than inserting a second one; its
    #        processing_status stays whatever the first delivery produced
    #        (normally PROCESSED) so a caller can see the real outcome. ---
    existing = db.execute(
        select(WebhookEvent).where(WebhookEvent.gateway_event_id == gateway_event_id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.retry_count += 1
        db.flush()
        return WebhookOutcome(webhook_event=existing, http_status=200)

    # --- 2. signature ---------------------------------------------------
    sig_header = headers.get("x-gateway-signature") or headers.get("X-Gateway-Signature")
    valid, reason, ts = webhook_security.verify_signature(
        secret=settings.gateway_webhook_secret, header_value=sig_header, raw_body=raw_body
    )
    stale = webhook_security.is_stale(ts, max_age_seconds=settings.gateway_webhook_max_age_seconds)

    if not valid:
        rejected = WebhookEvent(
            gateway_event_id=gateway_event_id,
            event_type=event_type,
            payment_reference=payment_reference,
            claimed_status=str(claimed_status) if claimed_status else None,
            event_timestamp=event_timestamp,
            payload_hash=hash_,
            signature_valid=False,
            processing_status=WebhookProcessingStatus.rejected_signature,
            received_at=_utcnow(),
            processed_at=_utcnow(),
            failure_reason=reason,
        )
        db.add(rejected)
        db.flush()
        return WebhookOutcome(webhook_event=rejected, http_status=401)

    # --- 3. replay / staleness ------------------------------------------
    if stale:
        rejected = WebhookEvent(
            gateway_event_id=gateway_event_id,
            event_type=event_type,
            payment_reference=payment_reference,
            claimed_status=str(claimed_status) if claimed_status else None,
            event_timestamp=event_timestamp,
            payload_hash=hash_,
            signature_valid=True,
            processing_status=WebhookProcessingStatus.rejected_stale,
            received_at=_utcnow(),
            processed_at=_utcnow(),
            failure_reason=(
                f"signature timestamp outside the "
                f"{settings.gateway_webhook_max_age_seconds}s replay window"
            ),
        )
        db.add(rejected)
        db.flush()
        return WebhookOutcome(webhook_event=rejected, http_status=400)

    # --- 4. resolve the target intent + validate the transition ----------
    event_row = WebhookEvent(
        gateway_event_id=gateway_event_id,
        event_type=event_type,
        payment_reference=payment_reference,
        claimed_status=str(claimed_status) if claimed_status else None,
        event_timestamp=event_timestamp,
        payload_hash=hash_,
        signature_valid=True,
        processing_status=WebhookProcessingStatus.received,
        received_at=_utcnow(),
    )
    db.add(event_row)
    db.flush()

    intent = db.execute(
        select(PaymentIntent).where(PaymentIntent.payment_reference == payment_reference)
    ).scalar_one_or_none()
    if intent is None:
        event_row.processing_status = WebhookProcessingStatus.failed
        event_row.processed_at = _utcnow()
        event_row.failure_reason = f"no PaymentIntent found for reference {payment_reference!r}"
        db.flush()
        return WebhookOutcome(webhook_event=event_row, http_status=200, intent=None)

    try:
        target_status = PaymentIntentStatus(str(claimed_status))
    except ValueError:
        event_row.processing_status = WebhookProcessingStatus.failed
        event_row.processed_at = _utcnow()
        event_row.failure_reason = f"unrecognised status {claimed_status!r}"
        db.flush()
        return WebhookOutcome(webhook_event=event_row, http_status=200, intent=intent)

    if not can_transition(intent.status, target_status):
        event_row.processing_status = WebhookProcessingStatus.rejected_out_of_order
        event_row.processed_at = _utcnow()
        event_row.failure_reason = (
            f"cannot move PaymentIntent {intent.payment_reference} from "
            f"{intent.status.value} to {target_status.value} — not a permitted "
            f"transition (out-of-order, replayed, or stale-relative-to-current-state event)"
        )
        db.flush()
        return WebhookOutcome(webhook_event=event_row, http_status=200, intent=intent)

    # --- 5. apply: record the gateway's own transaction row (append-only —
    #        an intent can accumulate several, e.g. AUTHORIZED then later
    #        SETTLED for a delayed-settlement demo) and transition the intent.
    try:
        transaction = GatewayTransaction(
            payment_intent_id=intent.id,
            gateway_transaction_reference=str(
                payload.get("gateway_transaction_reference") or gateway_event_id
            ),
            gateway_status=target_status,
            authorized_amount=_d(payload.get("authorized_amount")),
            captured_amount=_d(payload.get("captured_amount")),
            settled_amount=_d(payload.get("settled_amount")),
            gateway_fee=_d(payload.get("gateway_fee")),
            authorization_timestamp=_parse_timestamp(payload.get("authorization_timestamp")),
            capture_timestamp=_parse_timestamp(payload.get("capture_timestamp")),
            settlement_timestamp=_parse_timestamp(payload.get("settlement_timestamp")),
            failure_code=payload.get("failure_code"),
            failure_reason=payload.get("failure_reason"),
            raw_response_reference=str(
                payload.get("gateway_transaction_reference") or gateway_event_id
            ),
        )
        db.add(transaction)
        intent.status = target_status
        db.flush()

        if target_status == _final_allocation_status(db):
            _apply_final_allocation(db, intent, transaction)
        elif target_status in _REVERSAL_STATUSES:
            _apply_reversal(db, intent, target_status)
        # PARTIALLY_REFUNDED is recorded (the transition above already
        # succeeded) but deliberately triggers no financial effect here — the
        # webhook payload carries no partial-refund amount to allocate
        # against specific installments. TBD — Business Approval Required:
        # confirm the partial-refund amount contract before automating this.

        event_row.processing_status = WebhookProcessingStatus.processed
        event_row.processed_at = _utcnow()
        db.flush()
        return WebhookOutcome(webhook_event=event_row, http_status=200, intent=intent)
    except Exception as exc:  # noqa: BLE001 — dead-letter: log and 500 so the
        # gateway's own retry logic (a genuinely transient/bug failure, not a
        # business-rule rejection — those are handled above and never raise)
        # has a reason to try again later.
        db.rollback()
        event_row = db.execute(
            select(WebhookEvent).where(WebhookEvent.gateway_event_id == gateway_event_id)
        ).scalar_one_or_none()
        if event_row is None:
            event_row = WebhookEvent(
                gateway_event_id=gateway_event_id,
                event_type=event_type,
                payment_reference=payment_reference,
                claimed_status=str(claimed_status) if claimed_status else None,
                event_timestamp=event_timestamp,
                payload_hash=hash_,
                signature_valid=True,
                processing_status=WebhookProcessingStatus.failed,
                received_at=_utcnow(),
            )
            db.add(event_row)
        event_row.processing_status = WebhookProcessingStatus.failed
        event_row.processed_at = _utcnow()
        event_row.failure_reason = str(exc)
        db.flush()
        return WebhookOutcome(webhook_event=event_row, http_status=500, intent=None)


_REVERSAL_STATUSES = frozenset(
    {PaymentIntentStatus.reversed, PaymentIntentStatus.refunded, PaymentIntentStatus.chargeback}
)


def _final_allocation_status(db: Session) -> PaymentIntentStatus:
    from app.services.config_service import KEY_PAYMENT_GATEWAY_FINAL_STATUS, ConfigService

    raw = ConfigService(db).get(KEY_PAYMENT_GATEWAY_FINAL_STATUS)
    return PaymentIntentStatus(str(raw))


def _apply_final_allocation(
    db: Session, intent: PaymentIntent, transaction: GatewayTransaction
) -> None:
    """Reuse decision (confirmed with the user before this was written): call
    the EXISTING payments.py::record_payment() — the same engine
    POST /contracts/{id}/payments already uses — rather than building a
    second, parallel allocation implementation. `external_reference` is this
    intent's own `payment_reference`, so a redelivered webhook that somehow
    reaches this function twice (shouldn't happen — gateway_event_id
    idempotency and the closed transition graph both prevent it) still can't
    double-allocate: record_payment's own idempotent-replay check catches it.
    """
    from app.services import payments as payments_service
    from app.services.users import ensure_system_actor_id

    settings = get_settings()
    actor_id = ensure_system_actor_id(db, settings.system_gateway_username)
    contract = intent.contract

    outcome = payments_service.record_payment(
        db,
        contract,
        amount=float(intent.requested_amount),
        external_reference=intent.payment_reference,
        actor_id=actor_id,
        source=PaymentSource.gateway,
        payment_intent_id=intent.id,
    )

    if not outcome.replayed and transaction.gateway_fee:
        accounting.emit(
            db,
            event_type=AccountingEventType.gateway_fee_recognized,
            event_reference=f"gateway-fee-{transaction.id}",
            contract=contract,
            amount=transaction.gateway_fee,
            event_date=transaction.settlement_timestamp or _utcnow(),
        )


def _apply_reversal(db: Session, intent: PaymentIntent, target_status: PaymentIntentStatus) -> None:
    """A settled gateway payment was taken back (REVERSED/REFUNDED/
    CHARGEBACK) — see services/payment_reversal.py for the compensating-
    record logic. Never runs for PARTIALLY_REFUNDED (see the caller)."""
    from app.services.payment_reversal import reverse_settled_payment
    from app.services.users import ensure_system_actor_id

    settings = get_settings()
    actor_id = ensure_system_actor_id(db, settings.system_gateway_username)
    reverse_settled_payment(db, intent, final_status=target_status, actor_id=actor_id)
