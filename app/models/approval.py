from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow

# action_type values used this step.
ACTION_LATE_FEE_WAIVE = "late_fee.waive"
ACTION_CONFIG_UPDATE = "config.update"


class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ApprovalRequest(Base):
    """Generic maker-checker request.

    Core rule (enforced in the service layer): ``decided_by`` must never equal
    ``requested_by`` — you cannot approve or reject your own request, whatever
    your role.
    """

    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)

    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False, length=20),
        default=ApprovalStatus.pending,
        nullable=False,
        index=True,
    )
    decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    requester: Mapped["User"] = relationship(foreign_keys=[requested_by])  # noqa: F821
    decider: Mapped["User | None"] = relationship(foreign_keys=[decided_by])  # noqa: F821
