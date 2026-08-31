"""Customer exposure aggregation (P0-4, fixes assessment finding S-3).

Credit Assessment previously never looked at a customer's other contracts on
this platform — obligations were self-reported only. This sums the real
outstanding balance across all of a customer's non-closed contracts, reusing the
per-contract Receivable calculation (never a second copy of that math).

**Aggregation level:** only ``company_wide`` is implemented — one sum across
every contract regardless of product / category / brand / business unit. Other
levels (e.g. per-category) are a still-open business decision (assessment
BDR-07/08); configuring one raises rather than silently under-counting.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.contract import ContractStatus, InstallmentContract
from app.models.credit_application import CreditApplication
from app.models.sales_order import SalesOrder
from app.services import config_service as cfg
from app.services.config_service import ConfigService
from app.services.receivable import build_receivable

_ZERO = Decimal("0.00")
_COMPANY_WIDE = "company_wide"


@dataclass
class ContractExposure:
    contract_id: int
    status: str
    outstanding_principal: Decimal
    outstanding_profit: Decimal
    outstanding_late_fees: Decimal

    @property
    def outstanding_total(self) -> Decimal:
        return (
            self.outstanding_principal
            + self.outstanding_profit
            + self.outstanding_late_fees
        )


@dataclass
class CustomerExposure:
    customer_id: int
    aggregation_level: str
    total_outstanding: Decimal
    contracts: list[ContractExposure]


def compute_exposure(db: Session, customer_id: int) -> CustomerExposure:
    """Aggregate outstanding balance across a customer's non-closed contracts."""
    level = str(ConfigService(db).get(cfg.KEY_EXPOSURE_AGGREGATION_LEVEL)).strip()
    if level != _COMPANY_WIDE:
        raise ValueError(
            f"exposure_aggregation_level={level!r} is not implemented; "
            f"only {_COMPANY_WIDE!r} is supported"
        )

    contracts = (
        db.execute(
            select(InstallmentContract)
            .join(SalesOrder, InstallmentContract.sales_order_id == SalesOrder.id)
            .join(
                CreditApplication,
                SalesOrder.application_id == CreditApplication.id,
            )
            .where(
                CreditApplication.customer_id == customer_id,
                InstallmentContract.status != ContractStatus.closed,
            )
            .order_by(InstallmentContract.id)
        )
        .scalars()
        .all()
    )

    breakdown: list[ContractExposure] = []
    total = _ZERO
    for contract in contracts:
        rec = build_receivable(contract)
        item = ContractExposure(
            contract_id=contract.id,
            status=contract.status.value,
            outstanding_principal=rec.outstanding_principal,
            outstanding_profit=rec.outstanding_profit,
            outstanding_late_fees=rec.outstanding_late_fees,
        )
        breakdown.append(item)
        total += item.outstanding_total

    return CustomerExposure(
        customer_id=customer_id,
        aggregation_level=level,
        total_outstanding=total,
        contracts=breakdown,
    )
