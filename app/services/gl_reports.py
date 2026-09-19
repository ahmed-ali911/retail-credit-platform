"""Chart of Accounts — Checkpoint 3 reports: Chart of Accounts, Event Mapping
Register, General Ledger by Account, Trial Balance, Unmapped/Failed Events.

Reuses the existing `reports.py::ReportResult` / `export()` / `EXPORT_FORMATS`
infrastructure — no parallel CSV/XLSX/PDF rendering (non-negotiable rule #4).
Every function here returns a `ReportResult` so the existing generic
frontend table + `_maybe_export`/`_result` API pattern (`app/api/reports.py`)
works unchanged for this feature too.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounting import AccountingEvent, AccountingEventType
from app.models.gl import (
    ChartOfAccount,
    EventAccountMapping,
    EventAccountMappingLine,
    GLJournal,
    GLJournalLine,
    JournalStatus,
    NormalBalance,
)
from app.services import coa as coa_service
from app.services.reports import ReportResult

_ZERO = Decimal("0.00")


def _f(v) -> float:
    return float(v or 0)


# --------------------------------------------------------------------------- #
# A. Chart of Accounts
# --------------------------------------------------------------------------- #
def chart_of_accounts_report(db: Session) -> ReportResult:
    accounts = coa_service.list_accounts(db)
    usage_counts = dict(
        db.execute(
            select(EventAccountMappingLine.account_id, func.count(EventAccountMappingLine.id))
            .group_by(EventAccountMappingLine.account_id)
        ).all()
    )
    rows = [
        {
            "account_code": a.account_code,
            "account_name": a.account_name,
            "account_type": a.account_type.value,
            "normal_balance": a.normal_balance.value,
            "is_active": a.is_active,
            "is_demo": a.is_demo,
            "usage_count": usage_counts.get(a.id, 0),
            "created_at": a.created_at.isoformat() if a.created_at else "",
            "approved_at": a.approved_at.isoformat() if a.approved_at else "",
        }
        for a in accounts
    ]
    return ReportResult(
        fieldnames=[
            "account_code", "account_name", "account_type", "normal_balance",
            "is_active", "is_demo", "usage_count", "created_at", "approved_at",
        ],
        rows=rows,
        title="Chart of Accounts",
        sum_fields=["usage_count"],
    )


# --------------------------------------------------------------------------- #
# B. Event Mapping Register — one row per mapping LINE (a SUMMARY_ONLY /
# RESERVED mapping, with no lines, still gets one row so it stays visible).
# --------------------------------------------------------------------------- #
def event_mapping_register_report(db: Session) -> ReportResult:
    mappings = list(
        db.execute(select(EventAccountMapping).order_by(
            EventAccountMapping.account_event_type, EventAccountMapping.version.desc()
        )).scalars()
    )
    rows: list[dict] = []
    for m in mappings:
        base = {
            "event_type": m.account_event_type.value,
            "version": m.version,
            "classification": m.classification.value,
            "effective_from": m.effective_from.isoformat() if m.effective_from else "",
            "effective_to": m.effective_to.isoformat() if m.effective_to else "",
            "is_active": m.is_active,
            "is_demo": m.is_demo,
            "change_reason": m.change_reason or "",
        }
        if not m.lines:
            rows.append({**base, "line_sequence": "", "posting_side": "", "account_code": "", "amount_source": ""})
            continue
        for line in sorted(m.lines, key=lambda l: l.line_sequence):
            rows.append({
                **base,
                "line_sequence": line.line_sequence,
                "posting_side": line.posting_side.value,
                "account_code": line.account.account_code,
                "amount_source": line.amount_source.value,
            })
    return ReportResult(
        fieldnames=[
            "event_type", "version", "classification", "effective_from", "effective_to",
            "is_active", "is_demo", "line_sequence", "posting_side", "account_code",
            "amount_source", "change_reason",
        ],
        rows=rows,
        title="Event Mapping Register",
    )


# --------------------------------------------------------------------------- #
# C. General Ledger by Account — running balance respects normal_balance.
# --------------------------------------------------------------------------- #
def general_ledger_report(
    db: Session,
    *,
    account_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    journal_status: JournalStatus | None = None,
    event_type: AccountingEventType | None = None,
    contract_id: int | None = None,
) -> ReportResult:
    account = coa_service.get_account(db, account_id)

    stmt = (
        select(GLJournalLine, GLJournal, AccountingEvent)
        .join(GLJournal, GLJournalLine.journal_id == GLJournal.id)
        .join(AccountingEvent, GLJournal.accounting_event_id == AccountingEvent.id)
        .where(
            GLJournalLine.account_id == account_id,
            GLJournal.journal_status.in_([JournalStatus.ready, JournalStatus.posted]),
        )
        .order_by(GLJournal.event_date, GLJournal.id, GLJournalLine.line_sequence)
    )
    if date_from is not None:
        stmt = stmt.where(GLJournal.event_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(GLJournal.event_date < date_to)
    if journal_status is not None:
        stmt = stmt.where(GLJournal.journal_status == journal_status)
    if event_type is not None:
        stmt = stmt.where(AccountingEvent.event_type == event_type)
    if contract_id is not None:
        stmt = stmt.where(GLJournalLine.contract_id == contract_id)

    running = _ZERO
    debit_normal = account.normal_balance == NormalBalance.debit
    rows: list[dict] = []
    for line, journal, event in db.execute(stmt).all():
        signed = line.amount if line.posting_side.value == "DEBIT" else -line.amount
        running += signed if debit_normal else -signed
        rows.append({
            "posting_date": (journal.posting_date or journal.event_date).isoformat(),
            "journal_reference": journal.journal_reference,
            "event_reference": event.event_reference,
            "event_type": event.event_type.value,
            "contract_id": line.contract_id or "",
            "customer_id": line.customer_id or "",
            "description": line.description or "",
            "debit": _f(line.amount) if line.posting_side.value == "DEBIT" else 0.0,
            "credit": _f(line.amount) if line.posting_side.value == "CREDIT" else 0.0,
            "running_balance": _f(running),
            "journal_status": journal.journal_status.value,
            "mapping_version": journal.mapping_version_id,
            "external_gl_reference": journal.external_gl_reference or "",
        })

    return ReportResult(
        fieldnames=[
            "posting_date", "journal_reference", "event_reference", "event_type",
            "contract_id", "customer_id", "description", "debit", "credit",
            "running_balance", "journal_status", "mapping_version", "external_gl_reference",
        ],
        rows=rows,
        title=f"General Ledger — {account.account_code} {account.account_name}",
        extra={
            "account_code": account.account_code,
            "account_name": account.account_name,
            "normal_balance": account.normal_balance.value,
            "closing_balance": _f(running),
        },
        sum_fields=["debit", "credit"],
    )


# --------------------------------------------------------------------------- #
# D. Trial Balance
# --------------------------------------------------------------------------- #
def trial_balance_report(
    db: Session, *, date_from: date | None = None, date_to: date | None = None
) -> ReportResult:
    accounts = coa_service.list_accounts(db)

    stmt = (
        select(GLJournalLine.account_id, GLJournalLine.posting_side, func.sum(GLJournalLine.amount))
        .join(GLJournal, GLJournalLine.journal_id == GLJournal.id)
        .where(GLJournal.journal_status.in_([JournalStatus.ready, JournalStatus.posted]))
        .group_by(GLJournalLine.account_id, GLJournalLine.posting_side)
    )
    if date_from is not None:
        stmt = stmt.where(GLJournal.event_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(GLJournal.event_date < date_to)

    sums: dict[int, dict[str, Decimal]] = {}
    for account_id, side, total in db.execute(stmt).all():
        sums.setdefault(account_id, {"DEBIT": _ZERO, "CREDIT": _ZERO})[side.value] = Decimal(str(total or 0))

    rows: list[dict] = []
    total_debits = _ZERO
    total_credits = _ZERO
    for a in accounts:
        s = sums.get(a.id)
        if s is None:
            continue
        debit, credit = s["DEBIT"], s["CREDIT"]
        total_debits += debit
        total_credits += credit
        net = (debit - credit) if a.normal_balance == NormalBalance.debit else (credit - debit)
        rows.append({
            "account_code": a.account_code,
            "account_name": a.account_name,
            "account_type": a.account_type.value,
            "opening_balance": 0.0,  # not supported in this checkpoint — see FSD.md
            "period_debits": _f(debit),
            "period_credits": _f(credit),
            "net_closing_balance": _f(net),
        })

    unmapped_stmt = select(func.count(GLJournal.id), func.coalesce(func.sum(AccountingEvent.amount), 0)).join(
        AccountingEvent, GLJournal.accounting_event_id == AccountingEvent.id
    ).where(GLJournal.journal_status == JournalStatus.unmapped)
    failed_stmt = select(func.count(GLJournal.id)).where(GLJournal.journal_status == JournalStatus.failed)
    summary_only_types = [
        m.account_event_type
        for m in coa_service.list_active_mappings(db)
        if m.classification.value == "SUMMARY_ONLY"
    ]
    if date_from is not None:
        unmapped_stmt = unmapped_stmt.where(AccountingEvent.event_date >= date_from)
    if date_to is not None:
        unmapped_stmt = unmapped_stmt.where(AccountingEvent.event_date < date_to)

    unmapped_count, unmapped_total = db.execute(unmapped_stmt).one()
    failed_count = db.execute(failed_stmt).scalar_one()

    if summary_only_types:
        summary_only_stmt = select(func.count(AccountingEvent.id)).where(
            AccountingEvent.event_type.in_(summary_only_types)
        )
        if date_from is not None:
            summary_only_stmt = summary_only_stmt.where(AccountingEvent.event_date >= date_from)
        if date_to is not None:
            summary_only_stmt = summary_only_stmt.where(AccountingEvent.event_date < date_to)
        summary_only_count = db.execute(summary_only_stmt).scalar_one()
    else:
        summary_only_count = 0

    return ReportResult(
        fieldnames=[
            "account_code", "account_name", "account_type",
            "opening_balance", "period_debits", "period_credits", "net_closing_balance",
        ],
        rows=rows,
        title="Trial Balance",
        extra={
            "total_debits": _f(total_debits),
            "total_credits": _f(total_credits),
            "difference": _f(total_debits - total_credits),
            "unmapped_event_count": int(unmapped_count),
            "unmapped_event_total": _f(unmapped_total),
            "failed_journal_count": int(failed_count),
            "summary_only_event_count": int(summary_only_count),
        },
        sum_fields=["period_debits", "period_credits"],
    )


# --------------------------------------------------------------------------- #
# E. Unmapped / Failed events
# --------------------------------------------------------------------------- #
def unmapped_and_failed_report(db: Session) -> ReportResult:
    rows_data = db.execute(
        select(GLJournal, AccountingEvent)
        .join(AccountingEvent, GLJournal.accounting_event_id == AccountingEvent.id)
        .where(GLJournal.journal_status.in_([JournalStatus.unmapped, JournalStatus.failed]))
        .order_by(GLJournal.id.desc())
    ).all()

    rows: list[dict] = []
    for journal, event in rows_data:
        mapping_now_available = coa_service.get_active_mapping(db, event.event_type) is not None
        retry_eligible = journal.journal_status == JournalStatus.failed and journal.is_balanced
        rows.append({
            "event_reference": event.event_reference,
            "event_type": event.event_type.value,
            "event_date": event.event_date.isoformat(),
            "amount": _f(event.amount),
            "currency": event.currency,
            "contract_id": event.contract_id or "",
            "customer_id": event.customer_id or "",
            "journal_status": journal.journal_status.value,
            "reason": journal.error_message or "",
            "retry_eligible": retry_eligible,
            "mapping_now_available": mapping_now_available,
        })

    return ReportResult(
        fieldnames=[
            "event_reference", "event_type", "event_date", "amount", "currency",
            "contract_id", "customer_id", "journal_status", "reason",
            "retry_eligible", "mapping_now_available",
        ],
        rows=rows,
        title="Unmapped & Failed Events",
    )
