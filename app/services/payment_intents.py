"""Mock Payment Gateway — Payment Intent lifecycle (sections 3, 4, 6 of the brief).

Creating a ``PaymentIntent`` (and even opening its checkout session) never
touches ``Payment``/``PaymentAllocation``/installment balances/collections —
those only move once the intent reaches the *configured* final-allocation
status via a verified webhook (see ``gateway_webhooks.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import contract_owner_customer_id
from app.core.config import get_settings
from app.core.references import format_reference
from app.models.contract import ContractStatus, InstallmentContract, InstallmentStatus
from app.models.payment_gateway import PaymentIntent, PaymentIntentStatus, PaymentPurpose
from app.services import payment_gateway_client as gateway_client
from app.services.config_service import (
    KEY_PAYMENT_INTENT_EXPIRY_MINUTES,
    KEY_PAYMENT_MINIMUM_PARTIAL_AMOUNT,
    ConfigService,
)
from app.services.errors import DomainError
from app.services.receivable import build_receivable

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Customer Payment Screen data (section 3, step 2)
# --------------------------------------------------------------------------- #
@dataclass
class PaymentOptions:
    contract_id: int
    currency: str
    next_installment_amount: Decimal | None
    next_installment_due_date: date | None
    overdue_amount: Decimal
    late_fees_outstanding: Decimal
    total_outstanding: Decimal
    minimum_partial_amount: Decimal
    can_pay_current_installment: bool
    can_pay_overdue: bool
    can_pay_full_outstanding: bool
    can_pay_partial: bool


def compute_payment_options(db: Session, contract: InstallmentContract) -> PaymentOptions:
    receivable = build_receivable(contract)
    live = sorted(
        (
            i for i in contract.installments
            if i.principal_outstanding > _ZERO or i.profit_outstanding > _ZERO
        ),
        key=lambda i: i.sequence_number,
    )
    next_installment = live[0] if live else None
    overdue = [i for i in live if i.status == InstallmentStatus.overdue]
    overdue_amount = _money(
        sum((i.principal_outstanding + i.profit_outstanding for i in overdue), _ZERO)
    )
    total_outstanding = _money(
        receivable.outstanding_receivable + receivable.outstanding_late_fees
    )
    minimum_partial = _money(
        ConfigService(db).get_float(KEY_PAYMENT_MINIMUM_PARTIAL_AMOUNT)
    )

    return PaymentOptions(
        contract_id=contract.id,
        currency="KWD",
        next_installment_amount=(
            _money(next_installment.total_due) if next_installment else None
        ),
        next_installment_due_date=next_installment.due_date if next_installment else None,
        overdue_amount=overdue_amount,
        late_fees_outstanding=_money(receivable.outstanding_late_fees),
        total_outstanding=total_outstanding,
        minimum_partial_amount=minimum_partial,
        can_pay_current_installment=next_installment is not None,
        can_pay_overdue=overdue_amount > _ZERO,
        can_pay_full_outstanding=total_outstanding > _ZERO,
        can_pay_partial=total_outstanding > minimum_partial,
    )


@dataclass
class IntentOutcome:
    intent: PaymentIntent
    replayed: bool  # True if idempotency_key matched an existing intent —
                     # caller must not audit-log this as a fresh creation.


def _resolve_requested_amount(
    options: PaymentOptions, *, purpose: PaymentPurpose, amount: Decimal | None
) -> Decimal:
    if purpose == PaymentPurpose.current_installment:
        if options.next_installment_amount is None:
            raise DomainError("This contract has no upcoming installment to pay.")
        return options.next_installment_amount
    if purpose == PaymentPurpose.overdue_amount:
        if options.overdue_amount <= _ZERO:
            raise DomainError("This contract has no overdue amount to pay.")
        return options.overdue_amount
    if purpose == PaymentPurpose.full_outstanding:
        return options.total_outstanding
    # partial
    if amount is None:
        raise DomainError("A partial payment requires an explicit amount.")
    amount = _money(amount)
    if amount < options.minimum_partial_amount:
        raise DomainError(
            f"amount {amount} is below the minimum permitted partial payment of "
            f"{options.minimum_partial_amount}"
        )
    return amount


# --------------------------------------------------------------------------- #
# Create + checkout
# --------------------------------------------------------------------------- #
def create_intent(
    db: Session,
    contract: InstallmentContract,
    *,
    purpose: PaymentPurpose,
    amount: Decimal | None,
    actor_id: int,
    idempotency_key: str,
) -> IntentOutcome:
    idempotency_key = (idempotency_key or "").strip()
    if not idempotency_key:
        raise DomainError("idempotency_key is required")

    existing = db.execute(
        select(PaymentIntent).where(PaymentIntent.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return IntentOutcome(intent=existing, replayed=True)

    if contract.status != ContractStatus.active:
        raise DomainError(
            f"Payments can only be initiated against an active contract "
            f"(current status: {contract.status.value})",
            status_code=409,
        )

    customer_id = contract_owner_customer_id(db, contract)
    if customer_id is None:
        raise DomainError("Contract has no customer on record", status_code=409)

    options = compute_payment_options(db, contract)
    requested = _resolve_requested_amount(options, purpose=purpose, amount=amount)
    if requested <= _ZERO:
        raise DomainError("amount must be greater than zero")
    if requested > options.total_outstanding:
        raise DomainError(
            f"amount {requested} exceeds the contract's current total outstanding "
            f"of {options.total_outstanding}"
        )

    intent = PaymentIntent(
        payment_reference="",  # set below, once we have an id
        idempotency_key=idempotency_key,
        customer_id=customer_id,
        contract_id=contract.id,
        requested_amount=requested,
        currency=options.currency,
        payment_purpose=purpose,
        status=PaymentIntentStatus.initiated,
        created_by=actor_id,
    )
    db.add(intent)
    db.flush()  # assigns intent.id
    intent.payment_reference = format_reference("PaymentIntent", intent.id)
    db.flush()
    return IntentOutcome(intent=intent, replayed=False)


def create_checkout(db: Session, intent: PaymentIntent) -> gateway_client.CheckoutSessionResult:
    """Ask the gateway for a hosted checkout session and move
    INITIATED -> PENDING. Raises DomainError (a 4xx to the caller) if the
    intent isn't in a state a checkout can be opened for, or if the gateway
    call itself fails — neither case touches any balance."""
    if intent.status != PaymentIntentStatus.initiated:
        raise DomainError(
            f"Cannot open a checkout session for a payment intent in status "
            f"{intent.status.value} (must be INITIATED)",
            status_code=409,
        )

    settings = get_settings()
    minutes = ConfigService(db).get_int(KEY_PAYMENT_INTENT_EXPIRY_MINUTES)
    contract = intent.contract
    customer = intent.customer

    result = gateway_client.create_checkout_session(
        payment_reference=intent.payment_reference,
        amount=intent.requested_amount,
        currency=intent.currency,
        customer_name=customer.name,
        contract_reference=format_reference("InstallmentContract", contract.id),
        description=f"Installment payment — {intent.payment_purpose.value.replace('_', ' ')}",
        return_url=f"{settings.frontend_base_url}/payments/{intent.payment_reference}/status",
    )

    intent.gateway_session_id = result.token
    intent.expires_at = _utcnow() + timedelta(minutes=minutes)
    intent.status = PaymentIntentStatus.pending
    db.flush()
    return result
