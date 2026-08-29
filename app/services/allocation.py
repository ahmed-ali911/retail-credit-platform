"""Payment Allocation Engine — pure, no database.

Two rules, applied together:

1. **Oldest installment first.** The oldest unpaid installment is settled in
   full before *any* money reaches a newer installment.
2. **Within an installment: Late Fee -> Profit -> Principal.**

Rule 1 outranks rule 2: the oldest installment's *principal* is paid before the
next installment's *profit*. (This is the tricky case — see the spanning-two-
installments unit test.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class OutstandingInstallment:
    installment_id: int
    sequence_number: int
    late_fee_outstanding: Decimal
    profit_outstanding: Decimal
    principal_outstanding: Decimal


@dataclass
class InstallmentAllocation:
    installment_id: int
    sequence_number: int
    late_fee: Decimal = _ZERO
    profit: Decimal = _ZERO
    principal: Decimal = _ZERO

    @property
    def total(self) -> Decimal:
        return self.late_fee + self.profit + self.principal


@dataclass
class AllocationResult:
    allocations: list[InstallmentAllocation] = field(default_factory=list)
    allocated_amount: Decimal = _ZERO
    unallocated_amount: Decimal = _ZERO


def allocate(
    payment_amount: Decimal, outstanding: list[OutstandingInstallment]
) -> AllocationResult:
    """Allocate `payment_amount` across `outstanding` (must be oldest-first)."""
    remaining = Decimal(payment_amount)
    if remaining <= _ZERO:
        return AllocationResult(unallocated_amount=max(remaining, _ZERO))

    result = AllocationResult()

    for inst in outstanding:
        if remaining <= _ZERO:
            break

        line = InstallmentAllocation(
            installment_id=inst.installment_id,
            sequence_number=inst.sequence_number,
        )
        for bucket, owed in (
            ("late_fee", inst.late_fee_outstanding),
            ("profit", inst.profit_outstanding),
            ("principal", inst.principal_outstanding),
        ):
            if remaining <= _ZERO or owed <= _ZERO:
                continue
            take = min(remaining, owed)
            setattr(line, bucket, take)
            remaining -= take

        if line.total > _ZERO:
            result.allocations.append(line)

    result.allocated_amount = Decimal(payment_amount) - remaining
    result.unallocated_amount = remaining
    return result
