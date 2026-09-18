# Functional Specification Document — Write-off & Recovery Lifecycle

**Status:** Implemented. Every endpoint, status, and rule below is
code-verified against the current `app/` and `frontend/src/` trees —
file/function references are exact, not paraphrased.

---

## 1. Overview & BRD Traceability

This feature adds a controlled write-off & recovery lifecycle: an
explainable eligibility engine, a maker-checker-gated write-off **request**,
a separate, explicit **execution** step that actually moves balances, and a
directly role-gated **recovery** path for money received afterward. It
reuses the existing generic maker-checker framework, accounting-event
boundary, ledger, and audit trail rather than building parallel machinery —
see `app/models/write_off.py`'s module docstring for the full reuse list.

### 1.1 BRD → FSD → Test traceability

| BRD | FSD section | Requirement | Test case(s) |
|---|---|---|---|
| BR-1 (eligibility is informational only) | §2.1, §4.1 | Eligibility never blocks by itself; a human always decides | `test_fresh_contract_is_not_eligible`, `test_stage_3_alone_does_not_make_a_contract_eligible`, `test_fully_qualifying_contract_is_eligible` |
| BR-2 (exception path) | §4.1 | NOT_ELIGIBLE/UNAVAILABLE requires mandatory `exception_justification` | `test_not_eligible_blocks_a_normal_request`, `test_not_eligible_allows_a_controlled_exception_request`, `test_exception_without_justification_is_still_blocked`, `test_unavailable_eligibility_also_requires_the_exception_path` |
| BR-3 (FULL/PARTIAL, configurable) | §4.2 | Both types supported; requested amounts validated against snapshot | `test_requested_amount_above_outstanding_component_is_rejected`, `test_partial_execution_preserves_remaining_collectible_balance` |
| BR-4 (approval/execution split) | §2.2, §4.3 | Approval is a pure status transition; execution is a separate step | `test_checker_approval_moves_status_to_approved_without_touching_balances`, `test_cannot_execute_a_request_that_is_not_approved` |
| BR-5 (idempotent execution) | §4.3 | Re-executing an executed request is a safe no-op | `test_execution_is_idempotent_on_replay` |
| BR-6 (ledger, never overwrites *_paid) | §4.3, §5.1 | Dedicated `*_written_off` columns + `LedgerEntry`, `*_paid` untouched | `test_execution_writes_proper_ledger_entries_never_touches_paid_columns` |
| BR-7 (contract/case closure) | §4.3 | FULL execution closes contract + case with `write_off`/`written_off` reasons | `test_full_execution_zeroes_balances_closes_contract_and_case` |
| BR-8 (ECL provision release, ECL-run exclusion) | §4.4 | Reuses `ecl_provision_released`; closed contract excluded from future runs | `test_execution_emits_write_off_and_ecl_provision_released_events`, `test_written_off_contract_is_excluded_from_future_ecl_runs` |
| BR-9 (direct, role-gated recovery, mandatory reference) | §4.5 | Finance/Admin only, no maker-checker, mandatory `external_reference` | `test_recovery_requires_no_maker_checker_but_needs_finance_role`, `test_recovery_requires_mandatory_external_reference` |
| BR-10 (multiple partial recoveries, running total) | §4.5 | Running recovered-to-date total, original execution never mutated | `test_multiple_partial_recoveries_track_running_total_without_mutating_original` |
| BR-11 (recovery never reactivates/reopens/touches ECL) | §4.5 | Pure income tracking | `test_recovery_never_reactivates_contract_reopens_case_or_touches_ecl` |
| BR-12 (configurable recovery allocation) | §4.5 | Reads `recovery_allocation_order`, capped per-component | `test_recovery_cannot_exceed_remaining_recoverable_balance`, `test_recovery_emits_accounting_event_and_audit_event` |

---

## 2. Functional Flow & Statuses

### 2.1 `WriteOffRequestStatus` graph

Source of truth: `app/models/write_off.py::WriteOffRequestStatus` +
`app/services/write_off.py` / `app/services/approvals.py`.

```
PENDING   -> APPROVED   (checker approval; services/approvals.py::_execute, ACTION_WRITE_OFF_REQUEST)
PENDING   -> REJECTED   (checker rejection; services/approvals.py::_on_reject)
PENDING   -> CANCELLED  (maker withdrawal; services/write_off.py::cancel_request)
APPROVED  -> EXECUTED   (POST /write-offs/requests/{id}/execute; separate, explicit step — never automatic on approval)
```

`APPROVED` never auto-transitions to `EXECUTED` — this mirrors `ECLRun`'s
own `COMPLETED → POSTED` split, deliberately giving Finance a checkpoint
between "approved in principle" and "balances actually moved." Maker and
checker are drawn from the SAME role set
(`collections_officer` / `finance_officer` / `credit_manager` / `admin`);
`decided_by != requested_by` (enforced generically in
`services/approvals.py`) is what actually prevents self-approval, not a
role split.

### 2.2 End-to-end sequence

```
1. GET  /write-offs/eligibility/{contract_id}      — explainable, never blocks
2. POST /write-offs/contracts/{id}/requests        — creates WriteOffRequest (PENDING) + ApprovalRequest
3. POST /approvals/{id}/approve  (existing generic endpoint, different user) — WriteOffRequest -> APPROVED
     — or —
   POST /approvals/{id}/reject                                              — WriteOffRequest -> REJECTED
4. POST /write-offs/requests/{id}/execute          — WriteOffExecution created; balances move; WriteOffRequest -> EXECUTED
5. POST /write-offs/executions/{id}/recoveries     — zero or more, over time, direct action (Finance/Admin)
```

A `WriteOffExecution` is one-to-one with its `WriteOffRequest`
(`write_off_request_id` unique FK) — there is exactly one execution per
approved request, ever.

---

## 3. Screen / UI Behaviour

### 3.1 Write-off card (`frontend/src/components/WriteOffCard.tsx`, mounted on the Contract page)

Loads request history, eligibility (only for an `active` contract, only for
a role permitted to request), and any execution for the contract. Renders
nothing at all for an ordinary contract with no write-off history — the
card only appears once relevant.

- **Eligibility panel** — the system's verdict (`ELIGIBLE` / `NOT_ELIGIBLE`
  / `UNAVAILABLE`) as a `StatusBadge`, plus an indicator table (each row:
  name, category, result, explanatory detail).
- **Request form** — write-off type (FULL/PARTIAL), conditional per-
  component amount fields for PARTIAL, reason code, justification, optional
  evidence reference/comments. When eligibility is not `ELIGIBLE`, an
  exception alert appears and `exception_justification` becomes a required
  field before submission is allowed.
- **Request history** — a table of past requests for this contract with an
  Execute action (once `APPROVED`) and a Cancel action (while `PENDING`).
- **Execution & recovery section** — once an execution exists: an execution
  summary (type, executed amounts, remaining amounts, ECL/provision
  snapshot), a recoveries table, running total recovered, and remaining
  recoverable balance. A "record recovery" form (amount, mandatory external
  reference, optional payment link/channel/date) is shown only to
  `finance_officer`/`admin` and only while `remaining_recoverable > 0`.

### 3.2 Checker review (Approvals page, `frontend/src/pages/ApprovalsPage.tsx`)

No new screen — a write-off request is reviewed and approved/rejected on
the EXISTING generic Approvals screen, exactly like every other
maker-checker action type in this codebase. `payloadSummary()` gained a
`"writeoff.request"` branch rendering `{type} write-off on {contract}:
{total} — {reason_code}` (with an `[exception]` tag when applicable), so a
checker sees the same structured summary any other approval type gets —
never a bespoke review UI.

### 3.3 Write-offs & Recoveries list (`frontend/src/pages/WriteOffRecoveryListPage.tsx`, `/write-offs`)

A simple, filterable, cross-contract list of every write-off request
(status filter: PENDING/APPROVED/REJECTED/EXECUTED/CANCELLED), with summary
tiles (pending, approved-awaiting-execution, executed, total written off).
Explicitly does not re-host approve/execute/recovery actions — those stay
on the contract's own page and the Approvals screen; this list exists to
find an account, matching the reuse decision already made for
`PaymentOperationsPage.tsx`.

### 3.4 Navigation (`frontend/src/components/Shell.tsx`)

A "Write-offs & Recoveries" nav item (icon `FileMinus2`) in the Collections
group, visible to `collections_officer` / `finance_officer` /
`credit_manager` / `admin`.

---

## 4. Business Rules & Calculations

### 4.1 Eligibility (`app/services/write_off.py::evaluate_eligibility`)

Reads the contract's latest `ECLAssessment`
(`services/ecl_override.py::latest_assessment`) for DPD, stage, ECL amount,
and provision — never a second, independent delinquency calculation.
Gating indicators: `contract_active`, `dpd_threshold` (config, default 180),
`ecl_stage` (config, default 3), `collections_exhausted` (≥ N logged
`CollectionActivity` rows, config, default 1), `no_active_promise_to_pay`
(config-controlled whether this gates at all, default blocking). Purely
informational indicators (never gate): `default_status`,
`broken_promise_to_pay`, `legal_status`/`dispute_status`/
`restructuring_status` (always `unavailable` — no such data exists
anywhere in this platform).

Overall verdict: `UNAVAILABLE` if any gating indicator is itself
`unavailable` (e.g. no ECL assessment exists yet); `ELIGIBLE` if every
gating indicator is `satisfied`; otherwise `NOT_ELIGIBLE`.

### 4.2 Write-off request (`request_write_off`)

Hard guards (409): contract must be `active`; no other `PENDING` request
may already exist for the contract. A `PARTIAL` request is rejected (409)
if `writeoff_partial_allowed` is off. When eligibility is not `ELIGIBLE`,
`exception_justification` is mandatory (422 otherwise). `FULL` amounts are
computed as the request-time outstanding snapshot (never typed in); each
`PARTIAL` component is validated ≤ its snapshot counterpart (422 if
exceeded), and at least one component must be positive. The full
eligibility snapshot (every indicator, at that moment) is stored on the
request permanently.

### 4.3 Execution (`execute_write_off`)

Idempotent: an existing `WriteOffExecution` for the request is returned
unchanged (`replayed: true`) before anything else runs. Requires
`status == APPROVED` (409 otherwise) and the contract still `active` (409
otherwise). Re-validates each requested component against the contract's
**current** outstanding balances (not the request-time snapshot, which may
be stale) — 409 if balances moved downward since approval.

Applies two independent oldest-first sweeps
(`_write_off_installment_component` for principal/profit,
`_write_off_late_fees` for late fees), each writing the corresponding
`*_written_off` column and a `LedgerEntry`
(`entry_type` ∈ `principal_written_off`/`profit_written_off`/
`late_fee_written_off`, `related_action = write_off`) — never touching
`principal_paid`/`profit_paid`/`amount_paid`. An installment/charge that
reaches zero outstanding is stamped `written_off`.

For `FULL`: creates a `ContractClosure(reason=write_off)`, sets
`contract.status = closed`, and calls
`collections_service.close_case_for_write_off` (stamps
`CollectionCase.closed_reason = written_off`, unconditionally — unlike the
pre-existing `close_case_if_cleared`, which only closes when no overdue
installments remain).

### 4.4 ECL / accounting integration (§8 of BR list)

The contract's latest `ECLAssessment` is re-read at execution time for the
`ecl_stage_snapshot`/`ecl_amount_snapshot`/`provision_amount_snapshot`
figures stored on the execution (no new `ECLAssessment` row is fabricated).
Two accounting events are emitted through the existing
`accounting.emit()` boundary:

1. `write_off_executed` (FULL) or `partial_write_off_executed` (PARTIAL),
   `amount = executed_principal + executed_profit + executed_late_fee`,
   `event_reference = "writeoff-executed-{execution.id}"`.
2. If `provision_amount_snapshot > 0`: `ecl_provision_released`,
   `amount = -provision_amount_snapshot` — the SAME event type and negative-
   sign convention `ecl.py::post_run()` already uses for a downward
   provision movement, `event_reference =
   "ecl-writeoff-release-{execution.id}"`.

No special-casing is needed to exclude a written-off contract from future
ECL runs: a FULL write-off's contract is `closed`, and `run_ecl()`'s
existing `ContractStatus.active` filter already excludes it. A PARTIAL
write-off's contract stays `active` and is re-assessed normally on the next
run, from its now-lower balance.

### 4.5 Recovery (`record_recovery`)

Direct action (Finance/Admin — `_RECOVERY_ROLES` in `api/write_off.py`),
never maker-checker — a recovery records a cash-receipt fact, not a
discretionary decision (mirrors this codebase's existing precedent that
bank-line ingestion is direct while the waiver/write-off decisions those
facts get matched against are not).

Validates `amount > 0` and a non-blank `external_reference` (422
otherwise; a duplicate `external_reference` on the same execution is
rejected 409 via the DB `uq_recovery_execution_reference` constraint).
Computes `remaining_recoverable = total_written_off − recovered_to_date`
and rejects (422) any amount exceeding it — "this platform has no
overpayment process for recoveries." Allocates the amount across
principal/profit/late-fee components in the order given by
`recovery_allocation_order` (default `["late_fee", "profit", "principal"]`),
each component capped at its own remaining capacity
(`executed_{component} − already-recovered-for-that-component`). Emits one
`recovery_received` accounting event (`amount` positive,
`event_reference = "recovery-received-{recovery.id}"`) and one
`recovery.recorded` audit event.

Deliberately touches nothing else: the contract's status, the collections
case, and any `ECLAssessment`/provision figure are left exactly as the
write-off execution left them. `WriteOffExecution`'s own `executed_*`
columns are never mutated by a recovery — "original write-off," "total
recovered" (the sum of `Recovery` rows), and "remaining" are always three
independently reconstructable figures.

---

## 5. Data & Integration Requirements

### 5.1 Domain model (`app/models/write_off.py`)

| Table | Purpose | Key columns |
|---|---|---|
| `write_off_requests` | The maker-checker-gated request, with an immutable snapshot of the account and the system's eligibility verdict at request time | `status`, `eligibility_status`, `eligibility_snapshot` (JSON), `is_exception`, `exception_justification`, `snapshot_*`, `requested_*`, `approval_request_id` |
| `write_off_executions` | The immutable financial record of an executed write-off — one-to-one with an approved request | `write_off_request_id` (unique FK), `executed_*`, `remaining_*`, `ecl_*_snapshot`, `contract_closure_id`, `collection_case_id`, `accounting_event_id` |
| `write_off_recoveries` | One append-only row per recovery payment against an execution | `write_off_execution_id`, `external_reference` (unique per execution — `uq_recovery_execution_reference`), `amount`, `allocated_*`, `accounting_event_id` |

Extensions to existing tables (all additive):

| Table | New column(s) | Purpose |
|---|---|---|
| `installments` | `principal_written_off`, `profit_written_off` (Numeric 14,2, default 0) | Nets out of `principal_outstanding`/`profit_outstanding` — every downstream calculation (receivable, ECL EAD, allocation) automatically stops counting written-off amounts with zero code changes elsewhere |
| `late_fee_charges` | `amount_written_off` (Numeric 14,2, default 0) | Same pattern, nets out of `LateFeeCharge.outstanding` |
| `collection_cases` | `closed_reason` (`cleared` \| `written_off`) | Distinguishes an ordinary cleared closure from a write-off-driven one |

New enum values on existing types: `InstallmentStatus.written_off`,
`LateFeeStatus.written_off`, `ClosureReason.write_off`,
`LedgerEntryType.principal_written_off`/`profit_written_off`/
`late_fee_written_off`, `LedgerRelatedAction.write_off`,
`AccountingEventType.write_off_executed`/`partial_write_off_executed`/
`recovery_received`/`recovery_adjustment` (reserved for a future reversal),
`ACTION_WRITE_OFF_REQUEST` maker-checker action type.

Migration: `alembic/versions/0016_write_off_recovery.py` — additive only
(new columns + 3 new tables), upgrade/downgrade/re-upgrade round-trip
verified.

### 5.2 Endpoints (`app/api/write_off.py`, prefix `/write-offs`)

```
GET  /write-offs/eligibility/{contract_id}
POST /write-offs/contracts/{contract_id}/requests
GET  /write-offs/requests                         (filters: status, contract_id)
GET  /write-offs/requests/{request_id}
POST /write-offs/requests/{request_id}/execute
POST /write-offs/requests/{request_id}/cancel
GET  /write-offs/executions                        (filter: contract_id)
GET  /write-offs/executions/{execution_id}          (includes recoveries, total_recovered, remaining_recoverable)
POST /write-offs/executions/{execution_id}/recoveries
GET  /write-offs/executions/{execution_id}/recoveries
```

Approval/rejection uses the EXISTING generic
`POST /approvals/{id}/approve` / `/reject` — no new endpoint.

### 5.3 Configuration (`config/business_rules.yaml`)

```
writeoff_eligibility_dpd_threshold        (int,  default 180)
writeoff_eligibility_minimum_ecl_stage    (int,  default 3)
writeoff_minimum_collection_activities    (int,  default 1)
writeoff_block_if_active_promise_to_pay   (bool, default true)
writeoff_partial_allowed                  (bool, default true)
recovery_allocation_order                 (json, default ["late_fee", "profit", "principal"])
```

---

## 6. Validations, Errors & Edge Cases

| Case | Behaviour | Status |
|---|---|---|
| Request against a non-active contract | Rejected | 409 |
| Duplicate pending request for the same contract | Rejected | 409 |
| PARTIAL request while `writeoff_partial_allowed` is off | Rejected | 409 |
| A requested component exceeds the snapshot outstanding | Rejected | 422 |
| NOT_ELIGIBLE/UNAVAILABLE request without `exception_justification` | Rejected | 422 |
| Maker attempts to approve their own request | Rejected (generic maker-checker rule, reused unchanged) | 409 |
| Execute a request not yet `APPROVED` | Rejected | 409 |
| Execute against balances that moved (shrank) since approval | Rejected | 409 |
| Execute an already-executed request | Returns the existing execution unchanged, `replayed: true` | 200 |
| Cancel a non-`PENDING` request | Rejected | 409 |
| Recovery amount ≤ 0 | Rejected | 422 |
| Recovery without `external_reference` | Rejected | 422 |
| Recovery amount exceeds remaining recoverable balance | Rejected | 422 |
| Duplicate `external_reference` on the same execution | Rejected | 409 |
| Recovery against a nonexistent execution | Rejected | 404 |
| `sales_employee` / `credit_officer` attempting to view or request | Rejected (not in `_VIEW_ROLES`) | 403 |
| `collections_officer` attempting to record a recovery | Rejected (not in `_RECOVERY_ROLES`) | 403 |

---

## 7. Security, Audit & Performance

### 7.1 Roles

- View/request/cancel/execute: `collections_officer`, `finance_officer`,
  `credit_manager`, `admin` (`_VIEW_ROLES` == `_MAKER_ROLES` in
  `api/write_off.py`) — never `sales_employee` or `credit_officer`, since
  this is a Collections/Finance decision, not an origination one.
- Approve/reject: the existing generic approvals endpoint, same role set,
  enforced `decided_by != requested_by`.
- Record recovery: `finance_officer`, `admin` only (`_RECOVERY_ROLES`) —
  narrower than the general write-off maker set.

### 7.2 Audit trail

Every state-changing action writes an `AuditEvent`
(`services/audit.py::record_event`, the same mechanism every other module
uses): `writeoff.eligibility_evaluated`, `writeoff.requested`,
`writeoff.approved` / `writeoff.rejected` (from `services/approvals.py`),
`writeoff.cancelled`, `writeoff.executed` / `writeoff.partial_executed`,
`recovery.recorded`. Read-only eligibility lookups (`GET
/write-offs/eligibility/{id}`) are never audit-logged, matching this
codebase's convention that audit records mutations, not views —
`writeoff.eligibility_evaluated` fires from inside request creation
instead, when the verdict actually becomes consequential.

### 7.3 Reliability

Execution's idempotency check runs before any financial mutation — a
redelivered/retried execute call is safe by construction. Recovery's
uniqueness constraint on `(write_off_execution_id, external_reference)` is
enforced at the database level, not just in application code, so a race
between two simultaneous recovery submissions with the same reference
cannot double-record.

### 7.4 Performance

No caching, batching, or async work was added — every write-off/recovery
endpoint is a single synchronous request/response, matching every other
write endpoint in this codebase. Appropriate for this platform's traffic
profile (a single retail business, not a payment aggregator).

---

## 8. Remaining Gaps

- **Write-off reversal / correction (§15 of the original brief,
  BDR-WO-06).** Deliberately not built in this checkpoint. There is
  currently no way to undo an executed write-off or correct a mistaken one
  — `AccountingEventType.recovery_adjustment` is reserved in the model as a
  placeholder for this, but no service function, endpoint, or UI exists yet.
  A future implementation would need to decide: does a reversal restore the
  installment/late-fee balances (compensating records, mirroring
  `payment_reversal.py`'s own pattern), does it re-open the collections
  case, and does it re-trigger an ECL assessment — none of which is decided
  or built today.
- **Legal/dispute/restructuring-status eligibility inputs (BDR-WO-08).**
  Shown as `UNAVAILABLE`, informational only — no such data is captured
  anywhere in this platform.
- **No chart-of-accounts / GL posting split** for write-off or recovery
  events — inherited from the pre-existing accounting-event boundary's own
  scope, not introduced by this feature.
