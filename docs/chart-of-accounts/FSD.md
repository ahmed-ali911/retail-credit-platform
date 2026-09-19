# Functional Specification Document — Chart of Accounts + Versioned Posting Rules + Double-Entry Journal

**Status:** Implemented. Every entity, endpoint, and rule below is
code-verified against the current `app/` and `frontend/src/` trees —
file/function references are exact, not paraphrased.

---

## 1. Overview & BRD Traceability

This feature converts the flat `AccountingEvent` stream into explainable,
balanced double-entry journals through five new tables
(`app/models/gl.py`): `ChartOfAccount`, `EventAccountMapping`,
`EventAccountMappingLine`, `GLJournal`, `GLJournalLine`. It reuses, and
never duplicates, four existing platform mechanisms: the accounting-event
boundary itself (`AccountingEvent`), the generic maker-checker framework
(`ApprovalRequest`/`services/approvals.py`), the CSV/XLSX/PDF export
infrastructure (`services/reports.py`), and the mock ERP adapter boundary
(`services/erp_adapter.py`, extended rather than replaced).

### 1.1 BRD → FSD → Test traceability

| BRD | FSD section | Requirement | Test case(s) |
|---|---|---|---|
| BR-1 (versioned classification) | §2.1, §4.1 | POSTABLE/SUMMARY_ONLY/RESERVED is itself approved, versioned data | `test_seed_classifies_ecl_provision_movement_and_contract_closed_as_summary_only`, `test_seed_classifies_recovery_adjustment_as_reserved` |
| BR-2 (compound mappings, controlled amount sources) | §4.2, §4.3 | Multi-line mappings; `AmountSource` closed enum | `test_compound_mapped_event_creates_multiple_balanced_lines`, `test_every_postable_seeded_mapping_has_at_least_one_debit_and_one_credit_line_and_balances_by_construction` |
| BR-3 (positive lines, controlled sign reversal) | §5 | Never a negative debit/credit; `reverse_on_negative` swaps sides | `test_ecl_provision_release_reverses_sides_on_a_negative_source_amount`, `test_unsupported_negative_amount_produces_an_explainable_failed_journal` |
| BR-4 (balance invariant) | §4.4 | `Σdebits == Σcredits` before READY; else FAILED | `test_simple_mapped_event_creates_a_balanced_journal`, `test_unsupported_negative_amount_produces_an_explainable_failed_journal` |
| BR-5 (unmapped preserved) | §6 | Event preserved, journal UNMAPPED, `accounting_status` untouched | `test_unmapped_event_is_preserved_and_visible_as_unmapped` |
| BR-6 (no in-place mapping edits, historical integrity) | §2.2, §9 | New version only, prospective, old journals unaffected | `test_mapping_change_creates_a_new_version_and_deactivates_the_old_one_without_editing_it`, `test_posted_journal_keeps_its_mapping_version_after_a_later_mapping_change`, `test_account_snapshot_on_a_journal_line_survives_a_later_account_rename` |
| BR-7 (ECL double-posting prevented) | §4.1 | Portfolio roll-up never independently postable | `test_contract_level_ecl_events_post_and_portfolio_rollup_never_duplicates_them` |
| BR-8 (maker-checker reuse) | §8 | Existing `ApprovalRequest`, maker != checker | `test_maker_cannot_approve_their_own_account_proposal`, `test_maker_cannot_approve_their_own_mapping_proposal` |
| BR-9 (export reuse) | §11 | Existing `reports.py` CSV/XLSX/PDF | `test_report_export_row_count_matches_on_screen_totals` |
| BR-10 (unmapped/failed surfaced separately) | §11.D | Never folded into posted totals | `test_trial_balance_surfaces_unmapped_and_failed_counts_separately` |

---

## 2. Functional Flow & Statuses

### 2.1 `JournalStatus` graph (`app/models/gl.py`)

```
UNMAPPED  — no EventAccountMapping is effective for this event's event_date (terminal until a
            future mapping's effective window covers it AND the event is regenerated — not
            automatic; see §8's note on historical journal generation)
READY     — mapped, resolved, and balanced; eligible for posting
POSTED    — accepted by the (mock) ERP adapter; external_gl_reference set
FAILED    — either (a) a generation-time failure: unresolvable amount source, an unsupported
            negative value, or a genuine debit != credit imbalance (no lines, is_balanced=False),
            or (b) a posting-time failure: was READY and balanced, but the provider rejected it
            (is_balanced=True) — POST /gl/journals/{id}/retry only accepts case (b)
```

There is exactly one `GLJournal` per `AccountingEvent`
(`accounting_event_id` unique) for every `POSTABLE`-classified event —
`SUMMARY_ONLY`/`RESERVED` events get **no** `GLJournal` row at all (not an
inert row with a special status — genuinely absent, per the confirmed
design decision in Checkpoint 1).

### 2.2 End-to-end sequence

```
1. A business action calls accounting.emit()/emit_unscoped() (unchanged call sites, now also
   passing source_table/source_id).
2. emit() creates the AccountingEvent (idempotent on event_reference) — ONLY on the branch that
   creates a genuinely new row does it call gl_journal.generate_journal(db, event).
3. generate_journal() resolves the EventAccountMapping effective at the event's own event_date
   (by effective_from/effective_to, not "whichever is currently active" — historically correct
   even if generation somehow ran later than the event itself).
4. No mapping found             -> GLJournal(journal_status=UNMAPPED, error_message=...)
   Mapping is SUMMARY_ONLY/RESERVED -> no GLJournal row at all
   Mapping is POSTABLE            -> resolve every line's amount_source, apply sign handling,
                                      validate balance -> READY (with lines) or FAILED (no lines)
5. POST /jobs/post-accounting-events (existing endpoint, unchanged surface) now walks READY
   GLJournal rows (not raw AccountingEvent rows), calls the extended MockGlProvider.post_journal,
   and syncs AccountingEvent.accounting_status/external_gl_reference to the outcome.
6. POST /gl/journals/{id}/retry re-attempts posting for a single journal that reached READY,
   was genuinely balanced, and whose posting attempt failed.
```

---

## 3. Screen / UI Behaviour

### 3.1 Chart of Accounts & GL (`frontend/src/pages/ChartOfAccountsPage.tsx`, `/gl`, Risk & Finance nav group)

Five tabs, all reusing `ReportsPage.tsx`'s existing `GenericTableReport`/
`ExportGroup` components (now exported for this reuse) rather than a
second table/export renderer:

- **Chart of Accounts** — the 18 seeded accounts (code, name, type, normal
  balance, active/demo flags, usage count, created/approved info). A
  `finance_officer`/`admin` sees a "Propose a new account" form below;
  submitting creates an `ApprovalRequest` — the account itself is only
  materialised on approval (reviewed on the existing Approvals screen, by
  a different user).
- **Event Mappings** — the Event Mapping Register (one row per mapping
  line; a SUMMARY_ONLY/RESERVED mapping shows one row with blank
  posting-side/account/source columns so it stays visible). A propose
  form below lets a `finance_officer`/`admin` build a compound mapping
  (add/remove debit or credit lines, pick an account and amount source
  per line, toggle `reverse_on_negative`) and submit it as the next
  version for the selected event type.
- **General Ledger** — requires picking an account first (no
  running-balance report makes sense without one); shows posting date,
  journal reference, event reference/type, contract/customer, debit,
  credit, running balance (respecting the account's own normal balance),
  journal status, mapping version, external GL reference. A "journal
  reference" lookup field parses the deterministic `GLJ-{event_id}` suffix
  and opens the full journal (header, every line, link back to the source
  event/contract) — the Ledger → Journal → Event → Contract drill-down.
- **Trial Balance** — date-range filterable; the on-screen summary line
  (reused verbatim from `ReportsPage.tsx`'s `summaryKeys` mechanism) shows
  total debits, total credits, difference, unmapped event count/total,
  failed journal count, and summary-only event count as separate,
  never-folded-in figures.
- **Unmapped / Failed Events** — event reference, type, date, amount,
  contract/customer, journal status, the explainable reason, whether a
  posting retry is eligible, and whether a mapping is now available for
  that event type (informational only — never auto-applied).

Every account/mapping change proposal is reviewed on the **existing**
Approvals screen — this page deliberately does not re-host a second
approve/reject UI. Demo accounts/mappings are visually badged via the
existing `StatusBadge`/is_demo columns, not a bespoke component.

### 3.2 No browser-automation tool was available in this environment

Frontend verification for this feature is Vitest (9 new tests, 125 total),
`tsc --noEmit`, and a production `vite build` — all clean. The UI was not
manually clicked through in a live browser; this is stated explicitly
rather than implied.

---

## 4. Business Rules & Calculations

### 4.1 Classification (`app/models/gl.py::EventClassification`, seeded in `app/services/coa.py`)

`ecl_provision_movement` and `contract_closed` are `SUMMARY_ONLY`;
`recovery_adjustment` is `RESERVED` (nothing writes this event anywhere in
this platform); every other confirmed `AccountingEventType` (20 of 23) is
`POSTABLE`. The `ecl_provision_movement` classification is the direct
resolution of the Checkpoint 0 audit's single riskiest finding: the
portfolio roll-up and the per-contract ECL events represent the exact same
movement, so only the per-contract events may ever post.

### 4.2 Amount-source resolution (`app/services/gl_journal.py::_RESOLVERS`)

Each `AmountSource` resolves from the authoritative domain record the
event's `source_table`/`source_id` points at (or, for contract-derived
figures, `contract_id` directly) — never a re-derived duplicate of a
business calculation:

| Source | Resolves from |
|---|---|
| `event_amount` / `absolute_event_amount` | `AccountingEvent.amount` (direct / abs) |
| `cash_price` / `down_payment` / `financed_principal` / `total_contractual_profit` / `gross_installment_receivable` | `SalesOrder`/`Contract`, arithmetically derived (`sale_price - total_profit` = cash price, etc.) |
| `payment_principal` / `_profit` / `_late_fee` | `PaymentAllocation` rows summed by `payment_id` (reserved for a future finer-grained mapping; the seeded v1 doesn't use these) |
| `closure_ledger_*` | `LedgerEntry` rows summed by `entry_type`, keyed to the `ContractClosure` via `reference_type="contract_closure"` |
| `closure_ledger_return_*` | The dedicated `return_*` `LedgerEntry` types `return_contract()` now writes (Checkpoint 1's own fix) |
| `written_off_principal` / `_profit` / `_late_fee` / `provision_used` / `write_off_expense_excess` | `WriteOffExecution`'s own stored columns, directly |

A resolver returning `None` (source row genuinely missing) fails the
journal explainably; a resolver returning `Decimal("0.00")` (a legitimate
zero — e.g. no profit was rebated) simply drops that line from the
journal, never fabricates or force-includes it.

### 4.3 Sign handling (`app/services/gl_journal.py::_build_lines`)

A resolved negative amount is only accepted on a line with
`reverse_on_negative=True`: `abs()` is applied and `posting_side` swaps for
that line only. Confirmed-signed sources from the original audit
(`ecl_provision_released`, `ecl_provision_override_adjustment`,
`settlement_difference`) are seeded with this flag; every other seeded
line is not, and a resolver returning negative for one of those is a
`FAILED` journal, not a silent misdirection.

### 4.4 Balance validation

`total_debit`/`total_credit` are summed from the resolved, sign-corrected
lines; a journal is `READY` only if both a debit and a credit line
survive (post zero-drop) and the two totals are equal to the currency's
cent precision. No suspense/rounding line is ever auto-inserted to force
a balance — an imbalance is a `FAILED` journal with the exact figures in
`error_message`.

---

## 5. Data & Integration Requirements

### 5.1 Domain model (`app/models/gl.py`)

| Table | Purpose | Key columns |
|---|---|---|
| `chart_of_accounts` | Account master | `account_code` (unique), `account_type`, `normal_balance`, `is_active`, `is_demo` |
| `event_account_mappings` | One versioned posting rule per event type | `account_event_type` (reuses `AccountingEventType` — no parallel enum), `version`, `classification`, `effective_from`/`effective_to`, `is_active` |
| `event_account_mapping_lines` | One debit/credit line of a mapping version | `posting_side`, `account_id`, `amount_source`, `reverse_on_negative` |
| `gl_journals` | The double-entry result for one `AccountingEvent` | `accounting_event_id` (unique FK), `mapping_version_id`, `journal_status`, `total_debit`/`total_credit`, `is_balanced` |
| `gl_journal_lines` | One journal line | `account_code_snapshot`/`account_name_snapshot` (survive a later account rename), `amount` (always positive), `posting_side` |

Extension to the existing `accounting_events` table (additive, nullable,
populated going forward only): `source_table`, `source_id` — the exact
originating domain row for amount-source resolution.

Migration: `alembic/versions/0017_chart_of_accounts_gl.py` — all five
tables plus the two new columns, additive only, upgrade/downgrade/
re-upgrade verified. No second migration was needed for Checkpoints 2 or
3 — `GLJournal`/`GLJournalLine` were defined in this one migration from
the start (the same reasoning migration `0016` used for
`WriteOffExecution`/`Recovery`).

### 5.2 Endpoints (`app/api/gl.py`, prefix `/gl`)

```
GET  /gl/accounts                              (filter: is_active)
POST /gl/accounts/propose-create
POST /gl/accounts/{id}/propose-update
POST /gl/accounts/{id}/propose-deactivate
GET  /gl/mappings                              (active mappings, all event types)
GET  /gl/mappings/{event_type}/versions
POST /gl/mappings/propose-change               (create v1 or the next version — one function)
POST /gl/mappings/{event_type}/propose-deactivate
GET  /gl/journals                              (filters: journal_status, event_type, contract_id)
GET  /gl/journals/{id}
GET  /gl/journals/by-event/{accounting_event_id}   (drill-down: journal_reference's GLJ-{id} suffix)
POST /gl/journals/{id}/retry
GET  /gl/reports/chart-of-accounts             (?format=csv|xlsx|pdf)
GET  /gl/reports/mappings                      (?format=csv|xlsx|pdf)
GET  /gl/reports/ledger                        (account_id required; ?format=csv|xlsx|pdf)
GET  /gl/reports/trial-balance                 (?format=csv|xlsx|pdf)
GET  /gl/reports/unmapped-failed               (?format=csv|xlsx|pdf)
```

Approval/rejection uses the EXISTING generic `POST /approvals/{id}/approve`
/ `/reject` — no new endpoint. Six new `ACTION_COA_*` action types
(`accounting.account_create`/`_update`/`_deactivate`,
`accounting.mapping_create`/`_update`/`_deactivate`) are wired into
`services/approvals.py`'s existing `_execute` dispatch.

### 5.3 ERP adapter extension (`app/services/erp_adapter.py`)

`GlProvider.post_journal(event, journal, lines)` is the new primary method
`post_pending()` calls — carries the full journal, not just the flat
event, satisfying design rule #12 (business transaction stays separate
from the external posting result). `post_event(event)` is kept on the
interface for backward compatibility but is no longer on the path
`post_pending` exercises. `MockGlProvider` accepts both and always
returns a fake `MOCK-GL-{uuid}` reference.

---

## 6. Validations, Errors & Edge Cases

| Case | Behaviour | Status |
|---|---|---|
| Duplicate account code | Rejected at propose time and again at approval time (race-safe) | 409 |
| Account already referenced by a mapping line, code/type change proposed | Rejected — code/type immutable once used | 409 |
| Deactivating an account referenced by the currently active mapping | Rejected | 409 |
| Overlapping mapping effective periods | Structurally prevented — approval closes the prior version's `effective_to` at the new version's `effective_from` | — |
| Mapping to an inactive account | Rejected at propose time | 422 |
| Missing amount source (resolver returns `None`) | `FAILED` journal, explainable `error_message`, `AccountingEvent` preserved | — |
| Unsupported negative amount | `FAILED` journal, explainable `error_message` | — |
| Zero-value resolved line | Dropped, never posted; if it leaves no valid debit+credit pair, `FAILED` | — |
| Unbalanced journal | `FAILED`, never sent to the mock GL | — |
| Duplicate journal generation | `generate_journal()` returns the existing row — enforced by `accounting_event_id` UNIQUE | — |
| Duplicate posting | `post_pending()` only walks `READY` journals — a `POSTED` one is never reconsidered | — |
| Mapping changed after a journal already exists | The existing journal's `mapping_version_id` and lines are untouched | — |
| Account renamed/deactivated after a journal line references it | The line's `account_code_snapshot`/`account_name_snapshot` are untouched | — |
| Portfolio-level unscoped event (`ecl_provision_movement`) | SUMMARY_ONLY — no journal generated | — |
| SUMMARY_ONLY/RESERVED event's journal requested via retry | `retry_failed_posting` only accepts `FAILED` + `is_balanced=True` (a genuine posting failure) — rejected otherwise | 409 |
| Mapping approval by its own maker | Rejected — the existing generic `decided_by != requested_by` rule, reused unchanged | 409 |
| General Ledger report without `account_id` | Rejected — a running balance is meaningless without one account | 422 |
| Closed/invalid accounting period | Not enforced — no period-close concept exists anywhere in this platform yet (documented gap) | — |

---

## 7. Security, Audit & Performance

### 7.1 Roles

- View (accounts, mappings, journals, reports): `finance_officer`,
  `credit_manager`, `admin` — matches the existing Risk & Finance nav
  group's role set exactly.
- Propose (account/mapping create/update/deactivate): `finance_officer`,
  `admin`.
- Approve/reject: the existing generic decide roles
  (`finance_officer`, `credit_manager`, `admin`), `decided_by !=
  requested_by` enforced.
- Retry posting: `admin` only.

### 7.2 Audit trail

Every state-changing action writes an `AuditEvent`
(`services/audit.py::record_event`): `accounting.account_created`/
`_updated`/`_deactivated`, `accounting.mapping_activated`/`_deactivated`.
Read-only endpoints are never audit-logged, matching this codebase's
existing convention.

### 7.3 Reliability

Journal generation's idempotency (`generate_journal` returns an existing
row rather than regenerating) and `post_pending`'s READY-only walk
together mean a retried business action, a re-run posting job, or a
duplicate webhook can never double-journal or double-post. The one
narrower retry surface (`POST /gl/journals/{id}/retry`) is scoped to a
genuine posting-level failure only.

### 7.4 Performance

No caching, batching, or async fan-out — journal generation runs
synchronously inside `emit()`, matching every other write path in this
codebase. Appropriate for this platform's traffic profile.

---

## 8. Remaining Gaps

- **Historical journal generation for an UNMAPPED event is not
  implemented.** Once a mapping is approved covering an event type, any
  event that predates it (or was created before any mapping existed)
  stays `UNMAPPED` forever unless explicitly regenerated — and no
  regeneration mechanism exists. Per the brief's own instruction, this
  is a deliberate, documented gap rather than an automatic
  reinterpretation of history (which the brief explicitly forbids
  anyway).
- **`return`'s compound mapping is a partial reversal** — see BRD.md §6;
  Sales Revenue and Inventory/COGS are not reversed, routed instead
  through a Settlement/Suspense account pending a Finance decision.
- **No chart-of-accounts / GL posting split for a real ERP** — the mock
  boundary now carries a full journal, but no real ERP client has been
  built or connected.
- **No accounting-period close control** — documented as a future
  requirement, not attempted here.
- All 26 `FINANCE DECISION REQUIRED` items from BRD.md §6 remain fully
  open; nothing in this implementation assumes an answer to any of them.
