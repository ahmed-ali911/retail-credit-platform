from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.accounting import AccountingEventType
from app.models.gl import (
    AccountType,
    AmountSource,
    EventClassification,
    JournalStatus,
    NormalBalance,
    PostingSide,
)
from app.schemas.accounting import AccountingEventOut


# --------------------------------------------------------------------------- #
# Chart of Accounts
# --------------------------------------------------------------------------- #
class ChartOfAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_code: str
    account_name: str
    account_type: AccountType
    normal_balance: NormalBalance
    is_active: bool
    is_demo: bool
    description: str
    created_at: datetime
    created_by: int | None
    approved_at: datetime | None
    approved_by: int | None


class ProposeAccountCreateIn(BaseModel):
    account_code: str = Field(min_length=1, max_length=20)
    account_name: str = Field(min_length=1, max_length=120)
    account_type: AccountType
    normal_balance: NormalBalance
    is_demo: bool = True
    description: str | None = None


class ProposeAccountUpdateIn(BaseModel):
    account_code: str | None = None
    account_name: str | None = None
    account_type: AccountType | None = None
    normal_balance: NormalBalance | None = None
    description: str | None = None


class ProposeAccountDeactivateIn(BaseModel):
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Event Account Mapping
# --------------------------------------------------------------------------- #
class EventAccountMappingLineOut(BaseModel):
    id: int
    line_sequence: int
    posting_side: PostingSide
    account_id: int
    account_code: str
    amount_source: AmountSource
    multiplier: float
    reverse_on_negative: bool
    description: str | None
    is_active: bool

    @staticmethod
    def from_orm_line(line) -> "EventAccountMappingLineOut":
        return EventAccountMappingLineOut(
            id=line.id,
            line_sequence=line.line_sequence,
            posting_side=line.posting_side,
            account_id=line.account_id,
            account_code=line.account.account_code,
            amount_source=line.amount_source,
            multiplier=float(line.multiplier),
            reverse_on_negative=line.reverse_on_negative,
            description=line.description,
            is_active=line.is_active,
        )


class EventAccountMappingOut(BaseModel):
    id: int
    account_event_type: AccountingEventType
    version: int
    classification: EventClassification
    effective_from: date
    effective_to: date | None
    is_active: bool
    is_demo: bool
    description: str
    change_reason: str | None
    created_at: datetime
    approved_at: datetime | None
    approval_request_id: int | None
    lines: list[EventAccountMappingLineOut]

    @staticmethod
    def from_orm_mapping(mapping) -> "EventAccountMappingOut":
        return EventAccountMappingOut(
            id=mapping.id,
            account_event_type=mapping.account_event_type,
            version=mapping.version,
            classification=mapping.classification,
            effective_from=mapping.effective_from,
            effective_to=mapping.effective_to,
            is_active=mapping.is_active,
            is_demo=mapping.is_demo,
            description=mapping.description,
            change_reason=mapping.change_reason,
            created_at=mapping.created_at,
            approved_at=mapping.approved_at,
            approval_request_id=mapping.approval_request_id,
            lines=[
                EventAccountMappingLineOut.from_orm_line(l)
                for l in sorted(mapping.lines, key=lambda l: l.line_sequence)
            ],
        )


class ProposeMappingLineIn(BaseModel):
    line_sequence: int | None = None
    posting_side: PostingSide
    account_id: int
    amount_source: AmountSource
    multiplier: float = 1.0
    reverse_on_negative: bool = False
    description: str | None = None


class ProposeMappingChangeIn(BaseModel):
    account_event_type: AccountingEventType
    classification: EventClassification
    lines: list[ProposeMappingLineIn] = Field(default_factory=list)
    effective_from: date | None = None
    description: str | None = None
    change_reason: str | None = None


class ProposeMappingDeactivateIn(BaseModel):
    reason: str | None = None


# --------------------------------------------------------------------------- #
# GL Journal
# --------------------------------------------------------------------------- #
class GLJournalLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    line_sequence: int
    posting_side: PostingSide
    account_id: int
    account_code_snapshot: str
    account_name_snapshot: str
    amount: float
    currency: str
    amount_source: AmountSource
    contract_id: int | None
    customer_id: int | None
    description: str | None


class GLJournalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    journal_reference: str
    accounting_event_id: int
    mapping_version_id: int | None
    journal_status: JournalStatus
    event_date: datetime
    posting_date: datetime | None
    accounting_period: str | None
    currency: str
    total_debit: float
    total_credit: float
    is_balanced: bool
    external_gl_reference: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    posted_at: datetime | None


class GLJournalDetailOut(GLJournalOut):
    lines: list[GLJournalLineOut]
    event: AccountingEventOut
