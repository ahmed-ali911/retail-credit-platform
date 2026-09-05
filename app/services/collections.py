"""Collections workflow — cases and activity logging.

Case lifecycle is automatic and hooks into existing services:
  * overdue assessment (Step 3) opens a case when it first marks an installment
    overdue on a contract with no open case;
  * payment application (Step 3) closes the open case once the contract has no
    overdue installments left.

Promise-to-Pay lifecycle (Gap 3):
  * created `pending` by `log_activity`;
  * marked `kept` by `evaluate_promises_after_payment` (called from payment
    recording) once payments received since the promise cover `promised_amount`;
  * marked `broken` by `evaluate_overdue_promises` (called from the overdue
    job) once `promised_date` has passed with the amount not received;
  * staff can correct any of the above via `set_promise_status` (with a reason).
Every transition also logs a `CollectionActivity` row for the audit trail.

Logging an activity (a call, an SMS, a promise-to-pay) is a plain operational
action — no approval needed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.collections import (
    CollectionActivity,
    CollectionActivityType,
    CollectionCase,
    CollectionCaseStatus,
    PromiseStatus,
)
from app.models.contract import InstallmentContract, InstallmentStatus
from app.models.payment import Payment
from app.services.audit import record_event
from app.services.errors import DomainError

_ZERO = Decimal("0.00")


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


# --------------------------------------------------------------------------- #
# Promise-to-Pay lifecycle (Gap 3)
# --------------------------------------------------------------------------- #
def _paid_since(db: Session, contract_id: int, since: datetime) -> Decimal:
    """Total payment amount recorded against a contract at/after `since`."""
    total = db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.contract_id == contract_id,
            Payment.received_at >= since,
        )
    ).scalar_one()
    return Decimal(str(total or 0))


def _pending_promises_for_contract(
    db: Session, contract_id: int
) -> list[CollectionActivity]:
    return list(
        db.execute(
            select(CollectionActivity)
            .join(CollectionCase, CollectionActivity.collection_case_id == CollectionCase.id)
            .where(
                CollectionCase.contract_id == contract_id,
                CollectionActivity.activity_type == CollectionActivityType.promise_to_pay,
                CollectionActivity.promise_status == PromiseStatus.pending,
            )
        )
        .scalars()
        .all()
    )


def _transition_promise(
    db: Session,
    promise: CollectionActivity,
    new_status: PromiseStatus,
    *,
    actor_id: int,
    note: str,
) -> None:
    """Flip a promise's status and log a CollectionActivity for the transition."""
    before = promise.promise_status.value if promise.promise_status else None
    promise.promise_status = new_status
    db.flush()
    db.add(
        CollectionActivity(
            collection_case_id=promise.collection_case_id,
            created_by=actor_id,
            activity_type=CollectionActivityType.other,
            notes=note,
        )
    )
    db.flush()
    record_event(
        db,
        user_id=actor_id,
        action="collection.promise_transitioned",
        entity_type="collection_case",
        entity_id=promise.collection_case_id,
        before={"promise_status": before},
        after={
            "promise_status": new_status.value,
            "promise_activity_id": promise.id,
        },
    )


def evaluate_promises_after_payment(
    db: Session, contract: InstallmentContract, *, actor_id: int | None = None
) -> int:
    """Called after payment allocation. Marks `kept` any pending promise on this
    contract whose `promised_amount` is now covered by payments received since
    the promise was made. Returns the number transitioned."""
    if actor_id is None:  # a transition must be attributable to a user
        return 0
    kept = 0
    for promise in _pending_promises_for_contract(db, contract.id):
        if promise.promised_amount is None:
            continue
        paid = _paid_since(db, contract.id, promise.created_at)
        if paid >= Decimal(str(promise.promised_amount)):
            _transition_promise(
                db,
                promise,
                PromiseStatus.kept,
                actor_id=actor_id,
                note=(
                    f"Promise-to-pay met: {paid} received since "
                    f"{promise.created_at.date().isoformat()} "
                    f"(promised {promise.promised_amount})."
                ),
            )
            kept += 1
    return kept


def evaluate_overdue_promises(
    db: Session, *, as_of: date, actor_id: int | None = None
) -> int:
    """Called from the overdue-assessment job (Gap 3 / BDR item #22). Marks
    `broken` any pending promise whose `promised_date` has passed as of `as_of`
    without the promised amount being received. Returns the number transitioned."""
    if actor_id is None:  # a transition must be attributable to a user
        return 0
    rows = (
        db.execute(
            select(CollectionActivity)
            .join(CollectionCase, CollectionActivity.collection_case_id == CollectionCase.id)
            .where(
                CollectionActivity.activity_type == CollectionActivityType.promise_to_pay,
                CollectionActivity.promise_status == PromiseStatus.pending,
                CollectionActivity.promised_date.is_not(None),
                CollectionActivity.promised_date < as_of,
            )
        )
        .scalars()
        .all()
    )
    broken = 0
    for promise in rows:
        promised = Decimal(str(promise.promised_amount or 0))
        paid = _paid_since(db, promise.case.contract_id, promise.created_at)
        if paid < promised:
            _transition_promise(
                db,
                promise,
                PromiseStatus.broken,
                actor_id=actor_id,
                note=(
                    f"Promise-to-pay broken: promised {promise.promised_amount} "
                    f"by {promise.promised_date.isoformat()}, only {paid} received."
                ),
            )
            broken += 1
    return broken


def set_promise_status(
    db: Session,
    promise: CollectionActivity,
    *,
    new_status: PromiseStatus,
    reason: str,
    actor_id: int,
) -> CollectionActivity:
    """Staff manual override of a promise's status, with a required reason.
    Corrects an auto-detected `kept`/`broken` (or re-opens to `pending`)."""
    if promise.activity_type != CollectionActivityType.promise_to_pay:
        raise DomainError(
            "Only a promise_to_pay activity has a promise status to override",
            status_code=409,
        )
    _transition_promise(
        db,
        promise,
        new_status,
        actor_id=actor_id,
        note=f"Promise-to-pay status manually set to {new_status.value} by staff: {reason}",
    )
    return promise
