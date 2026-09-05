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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    InstallmentStatus,
)
from app.models.accounting import AccountingEventType
from app.models.payment import LateFeeCharge, LateFeeStatus
from app.services import accounting
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
    late_fees_skipped_contract_cap: int = 0
    total_late_fee_amount: Decimal = Decimal("0.00")
    collection_cases_opened: int = 0
    promises_broken: int = 0
    charges: list[dict] = field(default_factory=list)


def _fee_amount(inst: Installment, rate: Decimal) -> Decimal:
    """A late fee is `late_fee_rate` × the installment's own scheduled total
    (principal + profit)."""
    base = Decimal(str(inst.principal_component)) + Decimal(str(inst.profit_component))
    return (base * rate).quantize(_CENTS, rounding=ROUND_HALF_UP)


def assess_overdue(
    db: Session, *, as_of: date | None = None, actor_id: int | None = None
) -> OverdueSummary:
    config = ConfigService(db)
    grace_days = config.get_int(cfg.KEY_LATE_FEE_GRACE_DAYS)
    rate = Decimal(str(config.get_float(cfg.KEY_LATE_FEE_RATE)))
    # Gap 4 — a real per-contract cap on total late fees. `0` (the shipped
    # placeholder) means "no cap"; a positive value is enforced against the
    # sum of the contract's non-waived fees (existing + charged in this run).
    max_per_contract = Decimal(
        str(config.get_float(cfg.KEY_LATE_FEE_MAX_PER_CONTRACT))
    )
    # `late_fee_once_per_installment` is read only to assert the supported mode.
    # Recurring re-charging is intentionally not built; a fee is assessed at
    # most once per installment regardless of the flag's value (a *waived* fee
    # no longer counts — see Gap 5).
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
    new_charges: list[LateFeeCharge] = []

    # Gap 4 — running total of NON-WAIVED late fees per contract we might touch,
    # seeded from what's already on the ledger and updated as we add fees below.
    contract_ids = {inst.contract_id for inst in rows}
    fee_totals: dict[int, Decimal] = dict.fromkeys(contract_ids, Decimal("0.00"))
    if contract_ids:
        for cid, total in db.execute(
            select(LateFeeCharge.contract_id, func.sum(LateFeeCharge.amount))
            .where(
                LateFeeCharge.contract_id.in_(contract_ids),
                LateFeeCharge.status != LateFeeStatus.waived,
            )
            .group_by(LateFeeCharge.contract_id)
        ).all():
            fee_totals[cid] = Decimal(str(total or 0))

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
        # Gap 5 — the "once per installment" allowance is consumed only by an
        # *active* (assessed / paid) fee. A previously **waived** fee does not
        # block a fresh charge if the installment is still (or newly) overdue.
        already_charged = any(
            c.status != LateFeeStatus.waived for c in inst.late_fee_charges
        )
        if dpd <= grace_days or already_charged:
            continue

        fee = _fee_amount(inst, rate)

        # Gap 4 — enforce the per-contract cap (only when configured > 0).
        if max_per_contract > 0 and (
            fee_totals[inst.contract_id] + fee > max_per_contract
        ):
            summary.late_fees_skipped_contract_cap += 1
            continue

        charge = LateFeeCharge(
            installment_id=inst.id,
            contract_id=inst.contract_id,
            amount=fee,
            status=LateFeeStatus.assessed,
        )
        db.add(charge)
        new_charges.append(charge)
        fee_totals[inst.contract_id] += fee
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

    # --- accounting-event boundary (additive; never blocks assessment) ---
    for charge in new_charges:
        contract = db.get(InstallmentContract, charge.contract_id)
        accounting.emit(
            db,
            event_type=AccountingEventType.late_fee_charged,
            event_reference=f"late-fee-charged-{charge.id}",
            contract=contract,
            amount=charge.amount,
            event_date=charge.assessed_at,
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

    # Gap 3 — "Broken Promise-to-Pay" auto-detection (BDR item #22, greenlit).
    # This is the on-demand check the register asked for: run it as part of the
    # existing overdue job, not a second scheduler. A pending promise whose
    # date has passed with the promised amount not received is marked broken
    # and the transition is logged as a CollectionActivity.
    summary.promises_broken = collections_service.evaluate_overdue_promises(
        db, as_of=as_of, actor_id=actor_id
    )

    db.flush()
    return summary
