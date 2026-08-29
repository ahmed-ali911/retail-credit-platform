"""Offer generation and acceptance.

Acceptance is the hinge of this step: it turns a priced offer into a
SalesOrder + InstallmentContract + PaymentSchedule (with the declining-balance
installment breakdown), still without any real payment processing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dates import add_months
from app.models.credit_application import ApplicationStatus, CreditApplication
from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    PaymentSchedule,
)
from app.models.offer import InstallmentOffer, OfferStatus
from app.models.sales_order import SalesOrder
from app.services import config_service as cfg
from app.services import pricing
from app.services.config_service import ConfigService
from app.services.errors import DomainError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_offer(
    db: Session,
    application: CreditApplication,
    *,
    down_payment_amount: float,
    tenor_months: int | None = None,
) -> InstallmentOffer:
    if application.status != ApplicationStatus.approved:
        raise DomainError(
            f"An offer can only be generated for an approved application "
            f"(current status: {application.status.value})",
            status_code=409,
        )

    config = ConfigService(db)
    product = application.product
    tenor = tenor_months or application.requested_tenor_months

    min_pct = Decimal(str(config.get_float(cfg.KEY_MIN_DOWN_PAYMENT_PCT)))
    min_down_payment = (Decimal(str(product.cash_price)) * min_pct).quantize(Decimal("0.01"))
    if Decimal(str(down_payment_amount)) < min_down_payment:
        raise DomainError(
            f"Down payment {down_payment_amount:.2f} is below the required minimum "
            f"of {min_down_payment} ({min_pct * 100:g}% of the cash price)"
        )

    try:
        result = pricing.price_offer(
            db,
            cash_price=product.cash_price,
            tenor_months=tenor,
            down_payment=down_payment_amount,
        )
    except pricing.PricingError as exc:
        raise DomainError(str(exc)) from exc

    # Supersede any still-open offer for the same application.
    open_offers = db.execute(
        select(InstallmentOffer).where(
            InstallmentOffer.application_id == application.id,
            InstallmentOffer.status == OfferStatus.presented,
        )
    ).scalars().all()
    for old in open_offers:
        old.status = OfferStatus.expired

    validity_days = config.get_int(cfg.KEY_OFFER_VALIDITY_DAYS)
    offer = InstallmentOffer(
        application_id=application.id,
        cash_price=result.cash_price,
        down_payment=result.down_payment,
        tenor_months=result.tenor_months,
        profit_rate=result.profit_rate,
        installment_sale_price=result.installment_sale_price,
        total_profit=result.total_profit,
        amount_financed=result.amount_financed,
        schedule_preview=result.schedule_preview(),
        status=OfferStatus.presented,
        valid_until=_utcnow() + timedelta(days=validity_days),
    )
    db.add(offer)
    db.flush()
    return offer


def _is_expired(offer: InstallmentOffer) -> bool:
    valid_until = offer.valid_until
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    return _utcnow() > valid_until


def accept_offer(
    db: Session,
    offer: InstallmentOffer,
    *,
    down_payment_confirmed: bool,
    down_payment_reference: str | None,
    down_payment_amount: float | None = None,
) -> InstallmentContract:
    if offer.status == OfferStatus.accepted:
        raise DomainError("This offer has already been accepted", status_code=409)

    if offer.status == OfferStatus.expired or _is_expired(offer):
        offer.status = OfferStatus.expired
        db.flush()
        raise DomainError("This offer has expired", status_code=409)

    if not down_payment_confirmed:
        # Offer stays 'presented', nothing is created.
        raise DomainError(
            "down_payment_confirmed must be true to accept the offer; "
            "the offer remains presented"
        )

    if (
        down_payment_amount is not None
        and Decimal(str(down_payment_amount)) != Decimal(str(offer.down_payment))
    ):
        raise DomainError(
            f"down_payment_amount {down_payment_amount:.2f} does not match the "
            f"offer down payment of {offer.down_payment}"
        )

    application = offer.application

    sales_order = SalesOrder(
        application_id=application.id,
        product_id=application.product_id,
        offer_id=offer.id,
        sale_price=offer.installment_sale_price,
        down_payment_amount=offer.down_payment,
    )
    db.add(sales_order)
    db.flush()

    contract = InstallmentContract(
        sales_order_id=sales_order.id,
        tenor_months=offer.tenor_months,
        total_profit=offer.total_profit,
        unearned_profit_balance=offer.total_profit,
        status=ContractStatus.created,
    )
    db.add(contract)
    db.flush()

    schedule = PaymentSchedule(contract_id=contract.id)
    db.add(schedule)
    db.flush()

    base_date = _utcnow().date()
    for line in offer.schedule_preview:
        db.add(
            Installment(
                contract_id=contract.id,
                schedule_id=schedule.id,
                sequence_number=line["sequence_number"],
                due_date=add_months(base_date, line["sequence_number"]),
                principal_component=Decimal(str(line["principal_component"])),
                profit_component=Decimal(str(line["profit_component"])),
            )
        )

    offer.status = OfferStatus.accepted
    offer.accepted_at = _utcnow()
    offer.down_payment_confirmed = True
    offer.down_payment_reference = down_payment_reference

    db.flush()
    return contract


def confirm_delivery(db: Session, contract: InstallmentContract) -> InstallmentContract:
    if contract.status != ContractStatus.created:
        raise DomainError(
            f"Delivery can only be confirmed for a contract in 'created' status "
            f"(current: {contract.status.value})",
            status_code=409,
        )
    contract.status = ContractStatus.active
    contract.activated_at = _utcnow()
    db.flush()
    return contract
