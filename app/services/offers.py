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
from app.models.credit_application import (
    ApplicationStatus,
    AssessmentResult,
    AssessmentSource,
    CreditApplication,
)
from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    PaymentSchedule,
)
from app.models.accounting import AccountingEventType
from app.models.offer import InstallmentOffer, OfferStatus
from app.models.product import Product
from app.models.sales_order import SalesOrder
from app.services import accounting
from app.services import config_service as cfg
from app.services import pricing
from app.services.config_service import ConfigService
from app.services.errors import DomainError


class AffordabilityBlocked(DomainError):
    """The priced offer's real peak installment breaches the debt-burden limit
    and `offer_affordability_gate_mode` is `block`."""

    def __init__(self, message: str):
        super().__init__(message, status_code=422)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _affordability_recheck(
    db: Session,
    application: CreditApplication,
    *,
    peak_installment: Decimal,
    chosen_down_payment: Decimal,
    config: ConfigService,
    actor_id: int | None,
) -> dict:
    """Re-test the customer's real peak monthly burden against the same
    `maximum_debt_burden_ratio` used at application time, and persist the result
    as an `AssessmentResult` (source=offer_affordability_recheck) — the same
    audit shape as the P0-2 manual review. Returns a summary dict."""
    max_dbr = config.get_float(cfg.KEY_MAX_DBR)
    gate_mode = str(config.get(cfg.KEY_OFFER_AFFORDABILITY_GATE_MODE)).strip().lower()

    profile = application.customer.profile
    income = float(profile.monthly_income) if profile else 0.0
    obligations = float(profile.existing_monthly_obligations) if profile else 0.0
    peak = float(peak_installment)

    dbr = round((obligations + peak) / income, 4) if income > 0 else None
    affordable = dbr is not None and dbr <= max_dbr
    outcome = "pass" if affordable else "fail"

    db.add(
        AssessmentResult(
            application_id=application.id,
            decision=outcome,
            source=AssessmentSource.offer_affordability_recheck,
            reviewed_by=actor_id,
            estimated_installment=peak,
            debt_burden_ratio=dbr,
            triggered_rules=[
                {
                    "rule": "offer_affordability_recheck",
                    "outcome": outcome,
                    "reason": (
                        f"real peak installment {peak:.2f}; DBR "
                        f"{dbr if dbr is not None else 'n/a'} vs maximum "
                        f"{max_dbr:.4f}"
                    ),
                }
            ],
            config_snapshot={
                cfg.KEY_MAX_DBR: max_dbr,
                cfg.KEY_OFFER_AFFORDABILITY_GATE_MODE: gate_mode,
                "peak_installment": peak,
                "chosen_down_payment": float(chosen_down_payment),
                "monthly_income": income,
                "existing_obligations": obligations,
                "outcome": outcome,
            },
        )
    )
    db.flush()
    return {
        "affordable": affordable,
        "dbr": dbr,
        "max_dbr": round(max_dbr, 4),
        "peak_installment": round(peak, 2),
        "gate_mode": gate_mode,
    }


def generate_offer(
    db: Session,
    application: CreditApplication,
    *,
    down_payment_amount: float,
    tenor_months: int | None = None,
    actor_id: int | None = None,
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

    # Step 10 — the one new stock rule: can't offer a product with no unit free.
    if product.available_quantity <= 0:
        raise DomainError(
            f"Product '{product.name}' is out of stock "
            f"(available {product.available_quantity}).",
            status_code=422,
        )

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

    # P0-3: re-test affordability against the REAL priced schedule. Profit is
    # front-loaded, so the largest single installment (normally the first) is
    # the customer's actual peak monthly burden — the conservative figure.
    peak_installment = max(
        (line.total for line in result.schedule), default=Decimal("0")
    )
    recheck = _affordability_recheck(
        db,
        application,
        peak_installment=peak_installment,
        chosen_down_payment=result.down_payment,
        config=config,
        actor_id=actor_id,
    )
    if not recheck["affordable"] and recheck["gate_mode"] == "block":
        raise AffordabilityBlocked(
            f"Offer not generated: the real peak monthly installment "
            f"{recheck['peak_installment']:.2f} pushes the debt-burden ratio to "
            f"{recheck['dbr']}, over the maximum of {recheck['max_dbr']}. "
            f"A larger down payment or a shorter tenor is required. "
            f"(offer_affordability_gate_mode=block)"
        )

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

    # Step 10 — deduct one unit at contract creation (the working default
    # deduction point; BUSINESS DECISION REQUIRED — see the README register).
    # Additive: never blocks contract creation.
    product = db.get(Product, application.product_id)
    if product is not None:
        product.stock_quantity = (product.stock_quantity or 0) - 1

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

    # --- accounting-event boundary (additive; never blocks delivery) ---
    # Sale price and down payment are both recognised at delivery confirmation.
    sales_order = contract.sales_order
    accounting.emit(
        db,
        event_type=AccountingEventType.contract_activated,
        event_reference=f"contract-activated-{contract.id}",
        contract=contract,
        amount=sales_order.sale_price,
        event_date=contract.activated_at,
    )
    accounting.emit(
        db,
        event_type=AccountingEventType.down_payment_received,
        event_reference=f"down-payment-received-{contract.id}",
        contract=contract,
        amount=sales_order.down_payment_amount,
        event_date=contract.activated_at,
    )
    db.flush()
    return contract
