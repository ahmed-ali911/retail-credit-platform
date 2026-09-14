"""Mock Payment Gateway integration — domain model.

This is the Retail Credit application's OWN record of what the separate
``mock-payment-gateway`` service told it — never the gateway's own store.
The gateway is treated as an external provider: this application creates a
checkout session and later receives a *signed webhook* reporting the result;
it never reaches into the gateway's database or flips a status on its own say-so.

Design principle enforced throughout this module: a payment must not reduce a
contract's outstanding balance, clear overdue amounts, recognise profit, or
close/update a collections case merely because it was INITIATED. Financial
allocation happens only when a ``PaymentIntent`` reaches the *configured*
final-allocation status (business_rules.yaml key
``payment_gateway_final_allocation_status``, seeded ``SETTLED``) — see
``services/payment_intents.py``. No ``Payment``/``PaymentAllocation`` row
exists before that point, so every other module (collections' promise-to-pay
evaluation, exposure, ECL) that reads the ``payments`` table automatically
sees nothing for an intent that never reached that status — no special-casing
needed there.

Never stores real card numbers, CVVs, bank credentials, or real payment
tokens — this gateway is entirely simulated (see mock-payment-gateway/).
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, by_value, created_at_column, utcnow

_ZERO = Decimal("0.00")


# --------------------------------------------------------------------------- #
# Status model + permitted transitions
# --------------------------------------------------------------------------- #
class PaymentIntentStatus(str, enum.Enum):
    """SCREAMING_CASE values are the deliberate external/API contract (matches
    the gateway's own vocabulary); lowercase names are this codebase's
    Pythonic convention — hence ``values_callable=by_value`` on every column
    below (see app/models/base.py::by_value for why that matters)."""

    initiated = "INITIATED"
    pending = "PENDING"
    authorized = "AUTHORIZED"
    captured = "CAPTURED"
    settled = "SETTLED"
    failed = "FAILED"
    cancelled = "CANCELLED"
    expired = "EXPIRED"
    reversed = "REVERSED"
    partially_refunded = "PARTIALLY_REFUNDED"
    refunded = "REFUNDED"
    chargeback = "CHARGEBACK"


# Explicit, closed transition graph (section 4 of the brief). Anything not
# listed here is refused by `validate_transition` below — including any
# attempt to move a terminal status (FAILED/CANCELLED/EXPIRED/REVERSED/
# REFUNDED/CHARGEBACK) anywhere else, and any attempt to move a status
# "backwards" (e.g. an out-of-order or replayed webhook claiming PENDING
# after this intent is already SETTLED).
PAYMENT_INTENT_TRANSITIONS: dict[PaymentIntentStatus, frozenset[PaymentIntentStatus]] = {
    PaymentIntentStatus.initiated: frozenset(
        {PaymentIntentStatus.pending, PaymentIntentStatus.cancelled, PaymentIntentStatus.expired}
    ),
    PaymentIntentStatus.pending: frozenset(
        {
            PaymentIntentStatus.authorized,
            PaymentIntentStatus.failed,
            PaymentIntentStatus.cancelled,
            PaymentIntentStatus.expired,
        }
    ),
    PaymentIntentStatus.authorized: frozenset(
        {PaymentIntentStatus.captured, PaymentIntentStatus.failed, PaymentIntentStatus.expired}
    ),
    PaymentIntentStatus.captured: frozenset(
        {PaymentIntentStatus.settled, PaymentIntentStatus.failed}
    ),
    PaymentIntentStatus.settled: frozenset(
        {
            PaymentIntentStatus.reversed,
            PaymentIntentStatus.partially_refunded,
            PaymentIntentStatus.refunded,
            PaymentIntentStatus.chargeback,
        }
    ),
    # Only a single PARTIALLY_REFUNDED event followed by one final REFUND is
    # modelled in this version — `can_transition` below treats any same-status
    # webhook as a duplicate (not a transition), so a second, distinct partial
    # -refund event is out of scope for now (it would need its own dedicated
    # allowed self-transition, deliberately not added — TBD if the demo needs
    # multiple sequential partial refunds).
    PaymentIntentStatus.partially_refunded: frozenset({PaymentIntentStatus.refunded}),
    # Terminal — no outbound transition permitted.
    PaymentIntentStatus.failed: frozenset(),
    PaymentIntentStatus.cancelled: frozenset(),
    PaymentIntentStatus.expired: frozenset(),
    PaymentIntentStatus.reversed: frozenset(),
    PaymentIntentStatus.refunded: frozenset(),
    PaymentIntentStatus.chargeback: frozenset(),
}

# Statuses at/after which money has moved — used by services/payment_intents.py
# to decide whether a *reversal* code path applies (vs. a plain failed/expired
# intent, which never allocated anything and so needs no compensating entry).
SETTLED_STATUSES = frozenset({PaymentIntentStatus.settled})
TERMINAL_STATUSES = frozenset(
    {
        PaymentIntentStatus.failed,
        PaymentIntentStatus.cancelled,
        PaymentIntentStatus.expired,
        PaymentIntentStatus.reversed,
        PaymentIntentStatus.refunded,
        PaymentIntentStatus.chargeback,
    }
)


def can_transition(current: PaymentIntentStatus, target: PaymentIntentStatus) -> bool:
    if current == target:
        return False  # a same-status webhook is a duplicate, not a transition
    return target in PAYMENT_INTENT_TRANSITIONS.get(current, frozenset())


class PaymentPurpose(str, enum.Enum):
    """What the customer selected on the Customer Payment Screen (section 3)."""

    current_installment = "current_installment"
    overdue_amount = "overdue_amount"
    full_outstanding = "full_outstanding"
    partial = "partial"


class PaymentIntent(Base):
    """One customer attempt to pay through the Mock Payment Gateway.

    Created the moment the customer picks an amount and clicks Pay — this is
    deliberately a *separate* row from ``Payment`` (payments.py), which is
    only ever created once this intent reaches the configured final-allocation
    status. An intent that never gets there (FAILED/CANCELLED/EXPIRED) leaves
    no trace whatsoever in the balance-affecting tables.
    """

    __tablename__ = "payment_intents"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_intent_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_reference: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("installment_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="KWD", server_default="KWD"
    )
    payment_purpose: Mapped[PaymentPurpose] = mapped_column(
        Enum(PaymentPurpose, native_enum=False, length=25), nullable=False
    )
    status: Mapped[PaymentIntentStatus] = mapped_column(
        Enum(PaymentIntentStatus, native_enum=False, length=25, values_callable=by_value),
        default=PaymentIntentStatus.initiated,
        server_default=PaymentIntentStatus.initiated.value,
        nullable=False,
        index=True,
    )
    gateway_session_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    gateway_name: Mapped[str] = mapped_column(
        String(50), nullable=False, default="mock-payment-gateway",
        server_default="mock-payment-gateway",
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    contract: Mapped["InstallmentContract"] = relationship()  # noqa: F821
    customer: Mapped["Customer"] = relationship()  # noqa: F821
    # No `payment` relationship/FK here on purpose: the resulting Payment (once
    # checkpoint 2 creates one) is looked up the other way —
    # `select(Payment).where(Payment.payment_intent_id == intent.id)` — via
    # payments.py. A second FK column here pointing back at `payments.id`
    # would form a circular dependency between the two tables (each
    # referencing the other), which SQLAlchemy cannot topologically sort for
    # DDL create/drop without extra `use_alter` ceremony — avoided entirely by
    # only ever storing the relationship in one direction.
    transactions: Mapped[list["GatewayTransaction"]] = relationship(
        back_populates="payment_intent", order_by="GatewayTransaction.id"
    )


class GatewayTransaction(Base):
    """This application's record of the gateway's side of one PaymentIntent.

    One ``PaymentIntent`` can accumulate more than one row here over its
    life (e.g. an AUTHORIZED transaction followed by a later SETTLED one for
    a delayed-settlement demo scenario) — never edited in place, only appended
    to, so the full gateway-reported history is preserved.
    """

    __tablename__ = "gateway_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_intent_id: Mapped[int] = mapped_column(
        ForeignKey("payment_intents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gateway_transaction_reference: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    gateway_status: Mapped[PaymentIntentStatus] = mapped_column(
        Enum(PaymentIntentStatus, native_enum=False, length=25, values_callable=by_value),
        nullable=False,
    )
    authorized_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    captured_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    settled_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    gateway_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    authorization_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    capture_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settlement_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # A reference/id into the gateway's own transaction record — NOT the raw
    # payload (never stored; simulated statuses only, but kept minimal on
    # principle). Retry/dead-letter reprocessing re-queries the gateway's own
    # GET /gateway/transactions/{reference} for ground truth rather than
    # replaying a stored blob.
    raw_response_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    payment_intent: Mapped[PaymentIntent] = relationship(back_populates="transactions")


class WebhookProcessingStatus(str, enum.Enum):
    received = "RECEIVED"
    processed = "PROCESSED"
    duplicate = "DUPLICATE"                    # same gateway_event_id seen before
    rejected_signature = "REJECTED_SIGNATURE"  # HMAC did not verify
    rejected_stale = "REJECTED_STALE"          # timestamp outside the replay window
    rejected_out_of_order = "REJECTED_OUT_OF_ORDER"  # would move status backwards
    failed = "FAILED"                          # verified + in-order, but processing raised


class WebhookEvent(Base):
    """Every inbound webhook call, verified or not — the audit/replay/dead-
    letter log. ``gateway_event_id`` is the idempotency key: a second delivery
    of the same event is recognised here before any financial code runs, and
    is acknowledged successfully without processing the effect twice.

    Stores a hash of the payload (tamper-evidence for audit) rather than the
    payload itself, and no secrets — only the decomposed, already-non-
    sensitive fields a simulated gateway payload ever carries.
    """

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_event_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payment_reference: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # The status this event claims to report (before any validation) — used
    # for the out-of-order check even when the event is ultimately rejected.
    claimed_status: Mapped[str | None] = mapped_column(String(25), nullable=True)
    # The gateway's own event timestamp (not our received_at) — replay/
    # staleness is judged against this.
    event_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    processing_status: Mapped[WebhookProcessingStatus] = mapped_column(
        Enum(WebhookProcessingStatus, native_enum=False, length=30, values_callable=by_value),
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
