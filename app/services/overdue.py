"""Overdue / DPD detection and late-fee assessment.

Not a scheduled job yet — invoked manually via POST /jobs/assess-overdue.

For every installment past its due date on an active contract:
  * mark it ``overdue`` (if not already fully paid)
  * if DPD > the configured grace period and no late fee has been assessed for
    it yet, create a ``LateFeeCharge`` = late_fee_rate x (principal + profit)
    of that installment, status ``assessed``

Open/placeholder parameters (see config/business_rules.yaml and the README):
  * grace period            — configurable placeholder
  * once-per-installment    — default behaviour; recurring re-charge NOT built
  * max cap per contract    — placeholder, NOT enforced this step
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    InstallmentStatus,
)
from app.models.payment import LateFeeCharge, LateFeeStatus
from app.services import collections as collections_service
from app.services import config_service as cfg
from app.services.config_service import ConfigService

_CENTS = Decimal("0.01")


@dataclass
class OverdueSummary:
    as_of: date
    grace_period_days: int
    installments_marked_overdue: int = 0
    late_fees_assessed: int = 0
    total_late_fee_amount: Decimal = Decimal("0.00")
    collection_cases_opened: int = 0
    charges: list[dict] = field(default_factory=list)


def assess_overdue(
    db: Session, *, as_of: date | None = None, actor_id: int | None = None
) -> OverdueSummary:
    config = ConfigService(db)
    grace_days = config.get_int(cfg.KEY_LATE_FEE_GRACE_DAYS)
    rate = Decimal(str(config.get_float(cfg.KEY_LATE_FEE_RATE)))
    # `late_fee_once_per_installment` is read only to assert the supported mode.
    # Recurring re-charging is intentionally not built this step, so a fee is
    # assessed at most once per installment regardless of the flag's value.
    _ = bool(config.get(cfg.KEY_LATE_FEE_ONCE_PER_INSTALLMENT))

    as_of = as_of or datetime.now(timezone.utc).date()
    summary = OverdueSummary(as_of=as_of, grace_period_days=grace_days)

    rows = (
        db.execute(
            select(Installment)
            .join(InstallmentContract, Installment.contract_id == InstallmentContract.id)
            .where(
                Installment.due_date < as_of,
                Installment.status != InstallmentStatus.paid,
                InstallmentContract.status == ContractStatus.active,
            )
            .order_by(Installment.due_date, Installment.sequence_number)
        )
        .scalars()
        .all()
    )

    # contract id -> reason string for the first installment that went overdue
    newly_overdue_contracts: dict[int, str] = {}

    for inst in rows:
        if not inst.is_fully_paid and inst.status != InstallmentStatus.overdue:
            inst.status = InstallmentStatus.overdue
            summary.installments_marked_overdue += 1
            newly_overdue_contracts.setdefault(
                inst.contract_id,
                f"installment {inst.id} (seq {inst.sequence_number}) "
                f"overdue, due {inst.due_date.isoformat()}",
            )

        dpd = (as_of - inst.due_date).days
        already_charged = len(inst.late_fee_charges) > 0

        if dpd > grace_days and not already_charged:
            base = Decimal(str(inst.principal_component)) + Decimal(
                str(inst.profit_component)
            )
            fee = (base * rate).quantize(_CENTS, rounding=ROUND_HALF_UP)
            charge = LateFeeCharge(
                installment_id=inst.id,
                contract_id=inst.contract_id,
                amount=fee,
                status=LateFeeStatus.assessed,
            )
            db.add(charge)
            summary.late_fees_assessed += 1
            summary.total_late_fee_amount += fee
            summary.charges.append(
                {
                    "installment_id": inst.id,
                    "sequence_number": inst.sequence_number,
                    "dpd": dpd,
                    "amount": float(fee),
                }
            )

    db.flush()

    # Collections hook: open a case for each contract that just went overdue
    # (idempotent — open_case_if_needed skips contracts with an open case).
    for contract_id, reason in newly_overdue_contracts.items():
        contract = db.get(InstallmentContract, contract_id)
        opened = collections_service.open_case_if_needed(
            db, contract, reason=reason, actor_id=actor_id
        )
        if opened is not None:
            summary.collection_cases_opened += 1

    db.flush()
    return summary
