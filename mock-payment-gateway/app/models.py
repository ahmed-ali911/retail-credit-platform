"""Mock Payment Gateway — the gateway's OWN record of checkout sessions,
transactions, and the webhooks it has sent.

This is deliberately a separate database from retail-credit-api's. The two
services only ever talk over HTTP (checkout-session creation, webhook
delivery) — see ../../app/services/payment_gateway_client.py on the other
side. Never stores real card numbers, CVVs, bank credentials, or real
payment tokens — every outcome here is a simulated button click.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    # Naive UTC — SQLite (this service's only store) drops tzinfo on
    # read-back regardless of the column's `timezone=True` flag, so storing
    # aware datetimes here just produces spurious naive-vs-aware comparison
    # errors later. Every timestamp column below is naive UTC consistently;
    # the outbound webhook payload itself (webhook_sender.py) still carries a
    # proper timezone-qualified ISO string for the cross-service contract.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token() -> str:
    return uuid.uuid4().hex


def new_gateway_transaction_reference() -> str:
    return f"GWTXN-{uuid.uuid4().hex[:16].upper()}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


class CheckoutSessionStatus(str, enum.Enum):
    open = "open"
    consumed = "consumed"
    expired = "expired"


class GatewayTransactionStatus(str, enum.Enum):
    """Mirrors retail-credit-api's PaymentIntentStatus values exactly — this
    service and that one must speak the identical status vocabulary since it
    crosses the webhook wire. Kept as an independent enum (not a shared
    import) because these are two separate codebases/deployables."""

    pending = "PENDING"
    authorized = "AUTHORIZED"
    captured = "CAPTURED"
    settled = "SETTLED"
    failed = "FAILED"
    cancelled = "CANCELLED"
    expired = "EXPIRED"
    reversed = "REVERSED"


class CheckoutSession(Base):
    __tablename__ = "checkout_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True, default=new_token)
    merchant_reference: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contract_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    return_url: Mapped[str] = mapped_column(String(500), nullable=False)
    webhook_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[CheckoutSessionStatus] = mapped_column(
        Enum(CheckoutSessionStatus, native_enum=False, length=20),
        default=CheckoutSessionStatus.open, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    transaction: Mapped["GatewayTransactionRecord"] = relationship(
        back_populates="checkout_session", uselist=False
    )


class GatewayTransactionRecord(Base):
    """This gateway's own ledger of what it has told the merchant about one
    checkout session. One row per session; ``status`` is the latest state
    this gateway believes is true (independent of whether retail-credit-api
    has actually processed the corresponding webhook yet — the two are
    intentionally allowed to disagree until the webhook lands, which is the
    whole point of reconciliation)."""

    __tablename__ = "gateway_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_transaction_reference: Mapped[str] = mapped_column(
        String(40), nullable=False, unique=True, index=True, default=new_gateway_transaction_reference
    )
    checkout_session_id: Mapped[int] = mapped_column(
        ForeignKey("checkout_sessions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    merchant_reference: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[GatewayTransactionStatus] = mapped_column(
        Enum(GatewayTransactionStatus, native_enum=False, length=20), nullable=False,
        default=GatewayTransactionStatus.pending,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    gateway_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    authorization_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    capture_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settlement_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    simulated_outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    checkout_session: Mapped[CheckoutSession] = relationship(back_populates="transaction")
    deliveries: Mapped[list["WebhookDeliveryLog"]] = relationship(
        back_populates="transaction", order_by="WebhookDeliveryLog.id"
    )


class WebhookDeliveryLog(Base):
    """The gateway's own outbox. Unlike retail-credit-api's WebhookEvent (which
    deliberately never stores the raw payload, only a hash), this side is the
    *sender* — keeping the exact payload it sent is what makes
    POST /gateway/webhooks/{event_id}/retry meaningful (byte-identical resend,
    not a reconstruction)."""

    __tablename__ = "webhook_delivery_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_event_id: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    gateway_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("gateway_transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    claimed_status: Mapped[str] = mapped_column(String(25), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    webhook_url: Mapped[str] = mapped_column(String(500), nullable=False)
    corrupt_signature: Mapped[bool] = mapped_column(default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_response_body: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivered: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    transaction: Mapped[GatewayTransactionRecord] = relationship(back_populates="deliveries")
