"""Receivable view for a contract.

Per the Step 3 open-decision note: the Receivable figure is **unpaid principal +
unpaid profit only**. Outstanding late fees are a separate balance, returned
alongside so the distinction stays visible but never mixed in.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.contract import InstallmentContract, InstallmentStatus

_ZERO = Decimal("0.00")


@dataclass
class ReceivableView:
    contract_id: int
    outstanding_principal: Decimal
    outstanding_profit: Decimal
    outstanding_receivable: Decimal        # principal + profit (late fees excluded)
    outstanding_late_fees: Decimal         # separate ledger
    total_installments_paid: int
    total_installments_remaining: int


def build_receivable(contract: InstallmentContract) -> ReceivableView:
    principal = sum((i.principal_outstanding for i in contract.installments), _ZERO)
    profit = sum((i.profit_outstanding for i in contract.installments), _ZERO)
    late_fees = sum(
        (c.outstanding for c in contract.late_fee_charges), _ZERO
    )
    paid = sum(1 for i in contract.installments if i.status == InstallmentStatus.paid)
    total = len(contract.installments)

    return ReceivableView(
        contract_id=contract.id,
        outstanding_principal=principal,
        outstanding_profit=profit,
        outstanding_receivable=principal + profit,
        outstanding_late_fees=late_fees,
        total_installments_paid=paid,
        total_installments_remaining=total - paid,
    )
