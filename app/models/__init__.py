"""SQLAlchemy models.

Importing this package imports every model so that `Base.metadata` is complete
(used by Alembic autogenerate and by the test suite's create_all).
"""
from app.models.base import Base
from app.models.config_parameter import ConfigParameter
from app.models.customer import Customer, CustomerProfile, CustomerStatus
from app.models.product import Product, ProductCategory
from app.models.credit_application import (
    ApplicationChannel,
    ApplicationStatus,
    AssessmentResult,
    CreditApplication,
)
from app.models.offer import InstallmentOffer, OfferStatus
from app.models.sales_order import SalesOrder
from app.models.contract import (
    ContractStatus,
    Installment,
    InstallmentContract,
    InstallmentStatus,
    PaymentSchedule,
)

__all__ = [
    "Base",
    "ConfigParameter",
    "Customer",
    "CustomerProfile",
    "CustomerStatus",
    "Product",
    "ProductCategory",
    "ApplicationChannel",
    "ApplicationStatus",
    "AssessmentResult",
    "CreditApplication",
    "InstallmentOffer",
    "OfferStatus",
    "SalesOrder",
    "ContractStatus",
    "Installment",
    "InstallmentContract",
    "InstallmentStatus",
    "PaymentSchedule",
]
