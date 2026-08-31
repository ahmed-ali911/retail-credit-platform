"""SQLAlchemy models.

Importing this package imports every model so that `Base.metadata` is complete
(used by Alembic autogenerate and by the test suite's create_all).
"""
from app.models.base import Base
from app.models.config_parameter import ConfigParameter
from app.models.user import User, UserRole
from app.models.audit import AuditEvent
from app.models.customer import Customer, CustomerProfile, CustomerStatus
from app.models.product import Product, ProductCategory
from app.models.credit_application import (
    ApplicationChannel,
    ApplicationStatus,
    AssessmentResult,
    AssessmentSource,
    CreditApplication,
)
from app.models.ledger import LedgerEntry, LedgerEntryType, LedgerRelatedAction
from app.models.offer import InstallmentOffer, OfferStatus
from app.models.sales_order import SalesOrder
from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    InstallmentStatus,
    PaymentSchedule,
)
from app.models.payment import (
    LateFeeCharge,
    LateFeeStatus,
    Payment,
    PaymentAllocation,
    PaymentStatus,
)
from app.models.closure import ClosureReason, ContractClosure
from app.models.collections import (
    CollectionActivity,
    CollectionActivityType,
    CollectionCase,
    CollectionCaseStatus,
    PromiseStatus,
)
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.reconciliation import (
    BankStatementLine,
    ReconExceptionReason,
    ReconExceptionStatus,
    ReconciliationException,
)

__all__ = [
    "Base",
    "ConfigParameter",
    "User",
    "UserRole",
    "AuditEvent",
    "Customer",
    "CustomerProfile",
    "CustomerStatus",
    "Product",
    "ProductCategory",
    "ApplicationChannel",
    "ApplicationStatus",
    "AssessmentResult",
    "AssessmentSource",
    "CreditApplication",
    "LedgerEntry",
    "LedgerEntryType",
    "LedgerRelatedAction",
    "InstallmentOffer",
    "OfferStatus",
    "SalesOrder",
    "ContractStatus",
    "Installment",
    "InstallmentContract",
    "InstallmentStatus",
    "PaymentSchedule",
    "LateFeeCharge",
    "LateFeeStatus",
    "Payment",
    "PaymentAllocation",
    "PaymentStatus",
    "ClosureReason",
    "ContractClosure",
    "CollectionActivity",
    "CollectionActivityType",
    "CollectionCase",
    "CollectionCaseStatus",
    "PromiseStatus",
    "ApprovalRequest",
    "ApprovalStatus",
    "BankStatementLine",
    "ReconExceptionReason",
    "ReconExceptionStatus",
    "ReconciliationException",
]
