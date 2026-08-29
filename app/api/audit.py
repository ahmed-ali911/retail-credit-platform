from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.audit import AuditEvent
from app.models.user import UserRole
from app.schemas.audit import AuditEventOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get(
    "/events",
    response_model=list[AuditEventOut],
    dependencies=[Depends(require_roles(UserRole.admin, UserRole.credit_manager))],
)
def list_events(
    db: Session = Depends(get_db),
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
):
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc())
    if entity_type is not None:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == str(entity_id))
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    return db.execute(stmt.limit(limit)).scalars().all()
