"""Pricing / profit engine — pure unit tests, no database."""
from decimal import Decimal

import pytest

from app.services.pricing import PricingError, build_plan


def _sum(lines, attr):
    return sum((getattr(x, attr) for x in lines), Decimal("0"))


@pytest.mark.parametrize(
    "cash_price, down_payment, tenor, rate",
    [
        ("1200.00", "300.00", 12, "0.09"),
        ("5000.00", "500.00", 24, "0.18"),
        ("999.99", "123.45", 18, "0.135"),
        ("2500.00", "375.00", 36, "0.30"),
    ],
)
def test_schedule_reconciles_exactly(cash_price, down_payment, tenor, rate):
    result = build_plan(cash_price, down_payment, tenor, Decimal(rate))

    assert len(result.schedule) == tenor
    assert result.principal_financed == Decimal(cash_price) - Decimal(down_payment)

    # every component reconciles to the totals with zero drift
    assert _sum(result.schedule, "principal_component") == result.principal_financed
    assert _sum(result.schedule, "profit_component") == result.total_profit
    assert _sum(result.schedule, "total") == result.amount_financed
    assert result.amount_financed == result.principal_financed + result.total_profit
    assert result.installment_sale_price == Decimal(cash_price) + result.total_profit


@pytest.mark.parametrize("tenor, rate", [(12, "0.09"), (24, "0.18")])
def test_profit_recognition_declines_over_time(tenor, rate):
    result = build_plan("6000.00", "600.00", tenor, Decimal(rate))
    profits = [line.profit_component for line in result.schedule]

    # never increases from one installment to the next
    assert all(a >= b for a, b in zip(profits, profits[1:]))
    # and actually declines overall (declining-balance shape)
    assert profits[0] > profits[-1]


def test_principal_is_repaid_in_roughly_equal_amounts():
    result = build_plan("1200.00", "0.00", 12, Decimal("0.09"))
    principals = [line.principal_component for line in result.schedule]
    assert max(principals) - min(principals) <= Decimal("0.01")


def test_unsupported_inputs_raise():
    with pytest.raises(PricingError):
        build_plan("1000.00", "1000.00", 12, Decimal("0.09"))  # dp == price
    with pytest.raises(PricingError):
        build_plan("1000.00", "100.00", 0, Decimal("0.09"))  # tenor 0
