from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class AuditEvent(Base):
    """One row per state-changing action across the platform.

    ``entity_id`` is stored as a string so it can hold both integer PKs and
    string keys (e.g. a config parameter key). ``before_value`` /
    ``after_value`` are minimal snapshots, not field-by-field diffs.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    before_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    user: Mapped["User | None"] = relationship()  # noqa: F821
