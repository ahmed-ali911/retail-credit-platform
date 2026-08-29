"""Installment Pricing / Profit Engine.

Retail installment sale, not a loan: the customer buys a product at an
*installment sale price* = cash price + profit. There is no "interest" here —
the markup is **profit**, fixed at contract time.

Pricing inputs
--------------
* ``cash_price``            – the product's cash price (Step 1 Product)
* ``down_payment``          – collected up front, reduces the financed principal
* ``tenor_months``          – number of monthly installments
* ``profit_rate``           – total profit rate for that tenor, from the
                              configurable tenor -> rate table (ConfigService)

Derived amounts
---------------
* ``principal_financed``    = cash_price - down_payment
* ``total_profit``          = principal_financed * profit_rate   (whole-of-term)
* ``installment_sale_price``= cash_price + total_profit
* ``amount_financed``       = installment_sale_price - down_payment
                            = principal_financed + total_profit

Amortization — declining-balance profit recognition
---------------------------------------------------
Principal is repaid in equal monthly amounts, so the outstanding principal
declines linearly. Profit is recognised **proportionally to that outstanding
principal**: installment ``i`` (1-indexed, N installments) carries weight
``N - i + 1``. Early installments therefore carry more profit than later ones
(the reducing-balance shape), and profit-per-installment never increases.

Both columns are reconciled with cumulative rounding: the rounded cumulative
principal and profit hit their exact totals on the final installment, so the
schedule sums to ``principal_financed`` and ``total_profit`` with zero drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.services import config_service as cfg
from app.services.config_service import ConfigService

_CENTS = Decimal("0.01")


class PricingError(ValueError):
    """Raised when an offer cannot be priced (e.g. unsupported tenor)."""


def _money(value) -> Decimal:
    return Decimal(value).quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class InstallmentLine:
    sequence_number: int
    principal_component: Decimal
    profit_component: Decimal

    @property
    def total(self) -> Decimal:
        return self.principal_component + self.profit_component

    def as_dict(self) -> dict:
        return {
            "sequence_number": self.sequence_number,
            "principal_component": float(self.principal_component),
            "profit_component": float(self.profit_component),
            "total": float(self.total),
        }


@dataclass(frozen=True)
class PricingResult:
    cash_price: Decimal
    down_payment: Decimal
    tenor_months: int
    profit_rate: Decimal
    principal_financed: Decimal
    total_profit: Decimal
    installment_sale_price: Decimal
    amount_financed: Decimal
    schedule: list[InstallmentLine]

    def schedule_preview(self) -> list[dict]:
        return [line.as_dict() for line in self.schedule]


def build_plan(
    cash_price, down_payment, tenor_months: int, profit_rate
) -> PricingResult:
    """Pure pricing computation — no database, fully unit-testable."""
    cash_price = _money(cash_price)
    down_payment = _money(down_payment)
    profit_rate = Decimal(str(profit_rate))

    if tenor_months < 1:
        raise PricingError("tenor_months must be >= 1")
    if down_payment < 0:
        raise PricingError("down_payment cannot be negative")
    if down_payment >= cash_price:
        raise PricingError("down_payment must be less than the cash price")

    principal_financed = cash_price - down_payment
    total_profit = _money(principal_financed * profit_rate)
    installment_sale_price = cash_price + total_profit
    amount_financed = principal_financed + total_profit

    n = tenor_months
    total_weight = Decimal(n * (n + 1) // 2)  # sum of N, N-1, ..., 1

    schedule: list[InstallmentLine] = []
    prev_principal_cum = Decimal("0")
    prev_profit_cum = Decimal("0")
    cumulative_weight = Decimal("0")

    for i in range(1, n + 1):
        cumulative_weight += Decimal(n - i + 1)

        if i < n:
            principal_cum = _money(principal_financed * Decimal(i) / Decimal(n))
            profit_cum = _money(total_profit * cumulative_weight / total_weight)
        else:
            # final installment absorbs all remaining rounding
            principal_cum = principal_financed
            profit_cum = total_profit

        schedule.append(
            InstallmentLine(
                sequence_number=i,
                principal_component=principal_cum - prev_principal_cum,
                profit_component=profit_cum - prev_profit_cum,
            )
        )
        prev_principal_cum = principal_cum
        prev_profit_cum = profit_cum

    return PricingResult(
        cash_price=cash_price,
        down_payment=down_payment,
        tenor_months=tenor_months,
        profit_rate=profit_rate,
        principal_financed=principal_financed,
        total_profit=total_profit,
        installment_sale_price=installment_sale_price,
        amount_financed=amount_financed,
        schedule=schedule,
    )


def resolve_profit_rate(db: Session, tenor_months: int) -> Decimal:
    """Look up the profit rate for a tenor from the configurable rate table."""
    table = ConfigService(db).get_json(cfg.KEY_TENOR_PROFIT_RATE_TABLE)
    key = str(int(tenor_months))
    if key not in table:
        supported = ", ".join(sorted(table, key=int)) or "(none configured)"
        raise PricingError(
            f"No profit rate configured for a {tenor_months}-month tenor. "
            f"Supported tenors: {supported}"
        )
    return Decimal(str(table[key]))


def price_offer(
    db: Session, *, cash_price, tenor_months: int, down_payment
) -> PricingResult:
    """Config-backed entry point used by the offer endpoint."""
    profit_rate = resolve_profit_rate(db, tenor_months)
    return build_plan(cash_price, down_payment, tenor_months, profit_rate)
