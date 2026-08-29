from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Integer, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column


class ApplicationChannel(str, enum.Enum):
    online = "online"
    branch = "branch"


class ApplicationStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    under_assessment = "under_assessment"
    approved = "approved"
    rejected = "rejected"
    referred = "referred"


class CreditApplication(Base):
    """The customer's request for installment terms and its credit evaluation.

    This entity represents the request and its assessment — nothing else. It
    must never be merged with a future Sales Order / Installment Contract.
    """

    __tablename__ = "credit_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )

    requested_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    requested_tenor_months: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[ApplicationChannel] = mapped_column(
        Enum(ApplicationChannel, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, native_enum=False, length=20),
        default=ApplicationStatus.draft,
        nullable=False,
    )
    created_at: Mapped[datetime] = created_at_column()
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")

    customer: Mapped["Customer"] = relationship()  # noqa: F821
    product: Mapped["Product"] = relationship()  # noqa: F821
    assessments: Mapped[list["AssessmentResult"]] = relationship(
        back_populates="application",
        order_by="AssessmentResult.created_at",
        cascade="all, delete-orphan",
    )

    @property
    def latest_assessment(self) -> "AssessmentResult | None":
        return self.assessments[-1] if self.assessments else None


class AssessmentResult(Base):
    """Persisted output of one run of the Credit Assessment Engine (audit trail).

    Part of the assessment engine, not a business entity in its own right. A new
    row is written each time an application is assessed.
    """

    __tablename__ = "assessment_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)

    # Snapshot of the inputs and thresholds used, plus the per-rule outcomes.
    estimated_installment: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    debt_burden_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    triggered_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = created_at_column()

    application: Mapped[CreditApplication] = relationship(back_populates="assessments")
