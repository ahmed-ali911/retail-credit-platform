"""Audit-trail helper. One call per state-changing action."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent


def record_event(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: object | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        before_value=before,
        after_value=after,
    )
    db.add(event)
    return event
