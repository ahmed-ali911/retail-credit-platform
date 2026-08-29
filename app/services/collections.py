"""Collections workflow — cases and activity logging.

Case lifecycle is automatic and hooks into existing services:
  * overdue assessment (Step 3) opens a case when it first marks an installment
    overdue on a contract with no open case;
  * payment application (Step 3) closes the open case once the contract has no
    overdue installments left.

Logging an activity (a call, an SMS, a promise-to-pay) is a plain operational
action — no approval needed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.collections import (
    CollectionActivity,
    CollectionActivityType,
    CollectionCase,
    CollectionCaseStatus,
    PromiseStatus,
)
from app.models.contract import InstallmentContract, InstallmentStatus
from app.services.audit import record_event
from app.services.errors import DomainError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_open_case(db: Session, contract_id: int) -> CollectionCase | None:
    return db.execute(
        select(CollectionCase).where(
            CollectionCase.contract_id == contract_id,
            CollectionCase.status == CollectionCaseStatus.open,
        )
    ).scalar_one_or_none()


def open_case_if_needed(
    db: Session, contract: InstallmentContract, *, reason: str, actor_id: int | None = None
) -> CollectionCase | None:
    """Open a case for this contract unless one is already open. Idempotent."""
    if get_open_case(db, contract.id) is not None:
        return None
    case = CollectionCase(
        contract_id=contract.id,
        status=CollectionCaseStatus.open,
        opened_reason=reason,
    )
    db.add(case)
    db.flush()
    record_event(
        db,
        user_id=actor_id,
        action="collection_case.opened",
        entity_type="collection_case",
        entity_id=case.id,
        after={"contract_id": contract.id, "opened_reason": reason},
    )
    return case


def _has_overdue(contract: InstallmentContract) -> bool:
    return any(
        i.status == InstallmentStatus.overdue for i in contract.installments
    )


def close_case_if_cleared(
    db: Session, contract: InstallmentContract, *, actor_id: int | None = None
) -> CollectionCase | None:
    """Close the open case once the contract has no overdue installments left."""
    if _has_overdue(contract):
        return None
    case = get_open_case(db, contract.id)
    if case is None:
        return None
    case.status = CollectionCaseStatus.closed
    case.closed_at = _utcnow()
    db.flush()
    record_event(
        db,
        user_id=actor_id,
        action="collection_case.closed",
        entity_type="collection_case",
        entity_id=case.id,
        before={"status": "open"},
        after={"status": "closed", "contract_id": contract.id},
    )
    return case


def log_activity(
    db: Session,
    case: CollectionCase,
    *,
    created_by: int,
    activity_type: CollectionActivityType,
    notes: str | None,
    promised_amount: Decimal | float | None = None,
    promised_date: date | None = None,
) -> CollectionActivity:
    is_promise = activity_type == CollectionActivityType.promise_to_pay

    if is_promise:
        if promised_amount is None or promised_date is None:
            raise DomainError(
                "promise_to_pay requires promised_amount and promised_date"
            )
        amount = Decimal(str(promised_amount))
        p_date = promised_date
        p_status: PromiseStatus | None = PromiseStatus.pending
    else:
        # promise fields are only meaningful for promise_to_pay
        amount = None
        p_date = None
        p_status = None

    activity = CollectionActivity(
        collection_case_id=case.id,
        created_by=created_by,
        activity_type=activity_type,
        notes=notes,
        promised_amount=amount,
        promised_date=p_date,
        promise_status=p_status,
    )
    db.add(activity)
    db.flush()
    record_event(
        db,
        user_id=created_by,
        action="collection.activity_logged",
        entity_type="collection_case",
        entity_id=case.id,
        after={"activity_type": activity_type.value, "activity_id": activity.id},
    )
    return activity
