"""Mock Payment Gateway — settlement batch import + reconciliation exception
queue. See services/gateway_reconciliation.py for why this is a distinct
engine from /reconciliation (the bank-statement module)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.approval import ACTION_GATEWAY_RECON_RESOLVE
from app.models.gateway_settlement import (
    GatewayReconciliationItemStatus,
    ReconciliationItem,
    ReconciliationOutcome,
    SettlementBatch,
)
from app.models.user import User, UserRole
from app.schemas.approval import ApprovalRequestOut
from app.schemas.gateway_settlement import (
    ReconciliationItemOut,
    ReconciliationItemResolveRequest,
    SettlementBatchImport,
    SettlementBatchImportResult,
    SettlementBatchOut,
)
from app.services import approvals as approval_service
from app.services import gateway_reconciliation as recon_service
from app.services.errors import DomainError

router = APIRouter(prefix="/payments", tags=["gateway settlement & reconciliation"])

_FINANCE_ROLES = (UserRole.finance_officer, UserRole.admin)
_VIEW_ROLES = (UserRole.finance_officer, UserRole.credit_manager, UserRole.admin)


@router.post(
    "/settlement-batches",
    response_model=SettlementBatchImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_settlement_batch(
    payload: SettlementBatchImport,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_FINANCE_ROLES)),
):
    """Import the gateway's daily settlement feed (e.g. fetched from the
    separate mock-payment-gateway's ``GET /gateway/settlement-batches/generate``)
    and compare it against this app's own internal records."""
    try:
        summary = recon_service.import_settlement_batch(
            db,
            batch_reference=payload.batch_reference,
            settlement_date=payload.settlement_date,
            currency=payload.currency,
            items=[
                recon_service.BatchItemInput(
                    gateway_transaction_reference=i.gateway_transaction_reference,
                    merchant_reference=i.merchant_reference,
                    settlement_date=i.settlement_date,
                    gross_amount=i.gross_amount,
                    gateway_fee=i.gateway_fee,
                    net_amount=i.net_amount,
                    currency=i.currency,
                    gateway_status=i.gateway_status,
                )
                for i in payload.items
            ],
            actor_id=actor.id,
        )
    except DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.commit()
    db.refresh(summary.batch)
    return SettlementBatchImportResult(
        batch=summary.batch,
        items_processed=summary.items_processed,
        matched=summary.matched,
        exceptions=summary.exceptions,
        missing_in_gateway=summary.missing_in_gateway,
    )


@router.get("/settlement-batches", response_model=list[SettlementBatchOut])
def list_settlement_batches(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    return db.execute(
        select(SettlementBatch).order_by(SettlementBatch.id.desc())
    ).scalars().all()


@router.get("/settlement-batches/{batch_id}", response_model=SettlementBatchOut)
def get_settlement_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    batch = db.get(SettlementBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Settlement batch not found")
    return batch


@router.get("/reconciliation-items", response_model=list[ReconciliationItemOut])
def list_reconciliation_items(
    db: Session = Depends(get_db),
    outcome: ReconciliationOutcome | None = Query(default=None),
    status_: GatewayReconciliationItemStatus | None = Query(default=None, alias="status"),
    batch_id: int | None = Query(default=None),
    _: User = Depends(require_roles(*_VIEW_ROLES)),
):
    stmt = select(ReconciliationItem).order_by(ReconciliationItem.id.desc())
    if outcome is not None:
        stmt = stmt.where(ReconciliationItem.outcome == outcome)
    if status_ is not None:
        stmt = stmt.where(ReconciliationItem.status == status_)
    if batch_id is not None:
        stmt = stmt.where(ReconciliationItem.settlement_batch_id == batch_id)
    return db.execute(stmt).scalars().all()


@router.post(
    "/reconciliation-items/{item_id}/resolve",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def request_reconciliation_resolution(
    item_id: int,
    payload: ReconciliationItemResolveRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*_VIEW_ROLES)),
):
    """Ask for a reconciliation exception to be manually resolved — reuses the
    generic maker-checker: a *different* finance_officer / credit_manager /
    admin must approve via ``POST /approvals/{id}/approve``, which then
    performs the resolution (and, if there's a confirmed monetary variance
    against a known contract, books a SETTLEMENT_DIFFERENCE accounting event)."""
    item = db.get(ReconciliationItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Reconciliation item not found")
    if item.status != GatewayReconciliationItemStatus.open:
        raise HTTPException(
            status_code=409, detail=f"Item {item_id} is already {item.status.value}"
        )
    if approval_service.pending_request_for(db, ACTION_GATEWAY_RECON_RESOLVE, item.id):
        raise HTTPException(
            status_code=409,
            detail="A resolution request is already pending for this item",
        )

    req = approval_service.create_request(
        db,
        action_type=ACTION_GATEWAY_RECON_RESOLVE,
        entity_type="gateway_reconciliation_item",
        entity_id=item.id,
        requested_by=actor.id,
        payload={"reason": payload.reason, "comments": payload.comments},
    )
    db.commit()
    db.refresh(req)
    return req
