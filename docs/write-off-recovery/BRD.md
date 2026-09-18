# Business Requirements Document — Write-off & Recovery Lifecycle

**Status:** Implemented (code-verified — every section below traces to the
actual running code, not a design intention). Several figures are marked
**BUSINESS DECISION REQUIRED** / **FINANCE DECISION REQUIRED** where this
implementation had to pick a default to keep moving; those defaults are
configurable, not hard-coded, and are called out explicitly rather than
presented as confirmed policy.

---

## 1. Business Objective & Problem Statement

The platform (a Kuwait-style retail installment-sale business — see the
repo root [README](../../README.md)) had no way to formally recognise that a
severely delinquent receivable will not be collected. A Stage-3,
long-overdue contract just sat in Collections indefinitely: fully provisioned
by the ECL engine, but never actually removed from the books, and with no
record of who decided to give up on it, why, or under what authority.

This feature adds a controlled **write-off & recovery** lifecycle so that:

- writing off a receivable (in full or in part) is always a deliberate,
  justified, maker-checker-approved decision — never an automatic side
  effect of reaching Stage 3, of a missed payment, or of any other engine
  run;
- the system's own eligibility assessment (delinquency, ECL stage,
  collections history, active promises) is always shown to the requester,
  but a human can still request an **exception** write-off against a
  not-yet-eligible or not-evaluable account, with a mandatory justification
  that is preserved permanently alongside the system's verdict;
- once approved, execution is a separate, explicit, auditable step that
  actually zeroes the written-off balances via the same ledger and
  accounting-event mechanisms every other financial action in this platform
  uses — never a parallel, one-off balance model;
- money that later comes back against an already-written-off account (a
  recovery) is tracked precisely, against a traceable external reference,
  without ever reopening the case, reactivating the contract, or disturbing
  the ECL/provision figures a write-off already settled.

## 2. Stakeholders

| Stakeholder | Interest |
|---|---|
| Collections | A clear, auditable point at which a hopeless case leaves the active portfolio — with the eligibility evidence (DPD, stage, activity history) that justified it preserved on the record. |
| Finance / Accounting | Every write-off and every recovery is a postable accounting event (reusing the existing accounting-event boundary); the ECL provision tied to a written-off contract is released exactly once, through the same mechanism the ECL engine itself uses. |
| Credit / Risk | Eligibility is computed from the SAME ECL assessment (DPD, stage, exposure) the ECL engine already produces — no second, drifting definition of "how delinquent is this account." |
| Compliance / Audit | Every request, approval, rejection, cancellation, execution, and recovery is attributed, timestamped, and preserved — including exception requests made against a system verdict of NOT_ELIGIBLE or UNAVAILABLE, with the human rationale kept side by side with the system's own verdict, never overwriting it. |
| Engineering | The feature reuses the existing maker-checker framework, ledger, accounting-event boundary, and receivable-calculation properties rather than inventing parallel machinery — so exposure, ECL, and reporting automatically reflect a write-off with zero code changes in those modules. |

## 3. Current State & Pain Points

Before this feature:

- There was no way to remove a receivable from active collection short of
  the two existing `ContractClosure` reasons (`settlement`,
  `cancellation`) — neither of which fits "we do not expect to collect
  this."
- A Stage-3 contract's ECL provision kept accumulating indefinitely with no
  release mechanism tied to a business decision to stop pursuing it.
- Money recovered on a delinquent account after the fact (a settlement
  payment from a defaulted customer, a legal recovery) had nowhere
  structured to go — it would have had to be forced through the ordinary
  payment/allocation engine against installments that, in a write-off
  scenario, are meant to be closed out.
- Collections cases had no structured "why was this closed" reason beyond
  the existing free-text `opened_reason` — closing a case because its
  contract was written off looked identical, in the data, to closing it
  because the customer paid in full.

## 4. Business Requirements

BR-1. Write-off eligibility must be computed, explainable, and
**informational only** — it never blocks or forces a decision by itself. A
human maker always decides whether to request a write-off; a human checker
(a different user) always decides whether to approve it.

BR-2. A write-off request against an account the system considers
NOT_ELIGIBLE or UNAVAILABLE (data missing) must still be possible, but only
as a controlled **exception**, gated on a mandatory `exception_justification`
that is preserved permanently, side by side with the system's own verdict —
the system is never silently overruled.

BR-3. Both FULL and PARTIAL write-offs must be supported. PARTIAL write-off
availability is **configurable** policy
(`writeoff_partial_allowed`, default `true`). A PARTIAL write-off leaves the
contract active with a genuinely reduced remaining balance; a FULL write-off
closes the contract.

BR-4. Approval and execution must be two separate, explicit steps — approval
is a pure status transition (mirrors the existing ECL-override precedent);
execution is a distinct later action, callable by the same role set, with no
second maker-checker cycle (mirrors `ECLRun`'s own `COMPLETED → POSTED`
split). This gives Finance a checkpoint between "approved in principle" and
"balances actually moved."

BR-5. Execution must be idempotent — executing an already-executed request
must return the existing result unchanged, never write off a balance twice.

BR-6. Execution must never overwrite or repurpose the existing
`principal_paid` / `profit_paid` / `amount_paid` columns — it moves money
through dedicated `*_written_off` columns and the existing immutable ledger
(`LedgerEntry`), exactly like every other balance-affecting action in this
platform.

BR-7. A FULL write-off's contract must close (reusing the existing
`ContractClosure` mechanism with a new `write_off` reason) and its
collections case must close with a structured `written_off` reason,
distinguishable from an ordinary cleared case.

BR-8. The ECL provision associated with a written-off contract must be
released through the SAME accounting-event type and sign convention the ECL
engine's own provision-release logic already uses — no new ECL policy
invented for this feature. A FULL write-off's now-`closed` contract is
automatically excluded from all future ECL runs via the engine's existing
active-contract filter.

BR-9. A recovery (money received against an already-written-off execution)
must be a **direct**, role-gated action (Finance / Admin only) — not
maker-checker, since it records a cash-receipt fact rather than a
discretionary decision — but must require a mandatory, traceable
`external_reference` and must never exceed the remaining recoverable
balance.

BR-10. Multiple partial recoveries against the same execution must be
supported, with a running recovered-to-date total, without ever mutating the
original `WriteOffExecution`'s own executed amounts. "Original write-off",
"total recovered", and "remaining recoverable" must always be three
independently reconstructable figures.

BR-11. A recovery must never reactivate the contract, reopen the collections
case, or create/adjust an ECL assessment or provision figure — it is pure
recovery-income tracking against a receivable this platform has already,
formally, written off.

BR-12. Recovery allocation across written-off components (principal /
profit / late fee) must be **configurable**, not a fixed hard-coded policy
— see `recovery_allocation_order`.

## 5. Success Criteria / KPIs

These are the acceptance criteria this implementation was actually held to
(see [FSD.md](FSD.md) §1.1 for the BRD→FSD→test traceability table):

- Eligibility is never used to silently block anything — a NOT_ELIGIBLE or
  UNAVAILABLE contract can still be write-off-requested via the exception
  path, and this is test-verified.
- Approving a write-off request produces zero balance change — only
  `status: APPROVED` moves (test-verified:
  `test_checker_approval_moves_status_to_approved_without_touching_balances`).
- A FULL execution zeroes every outstanding component, closes the contract,
  and closes the collections case with `written_off` (test-verified:
  `test_full_execution_zeroes_balances_closes_contract_and_case`).
- Execution never touches `principal_paid`/`profit_paid` (test-verified:
  `test_execution_writes_proper_ledger_entries_never_touches_paid_columns`).
- Re-executing an already-executed request is a safe no-op
  (`test_execution_is_idempotent_on_replay`).
- A written-off contract is excluded from future ECL runs
  (`test_written_off_contract_is_excluded_from_future_ecl_runs`).
- Multiple partial recoveries track a correct running total without
  mutating the original execution
  (`test_multiple_partial_recoveries_track_running_total_without_mutating_original`).
- A recovery never reactivates the contract, reopens the case, or touches
  ECL (`test_recovery_never_reactivates_contract_reopens_case_or_touches_ecl`).
- 362 backend tests and 116 frontend tests pass with zero regressions to any
  pre-existing feature.

## 6. Assumptions & Constraints

- **Write-off is never automatic.** No engine run, scheduled job, or status
  transition anywhere in this platform creates a `WriteOffRequest` — only an
  explicit staff action does.
- **No reversal in this implementation.** Once a `WriteOffExecution` exists,
  there is no "undo write-off" action — see BDR-WO-06 below.
- **Single currency (KWD)**, matching the rest of this platform.
- **"Other charges" is modelled but always zero** — this platform has no
  standalone "other charges" balance anywhere; the column exists for
  completeness against the original brief's four-component shape, never
  fabricated.
- **No chart-of-accounts / GL posting split** — every accounting event
  carries one signed amount, matching the pre-existing accounting-event
  boundary's own scope (not a gap introduced by this feature).

### Open Business Decisions (BDR-WO-*)

| ID | Decision needed | Current default | Where it lives |
|---|---|---|---|
| BDR-WO-01 | Minimum DPD for the delinquency eligibility indicator | 180 days | `config_parameter` `writeoff_eligibility_dpd_threshold` |
| BDR-WO-02 | Minimum ECL stage for the credit-risk eligibility indicator | Stage 3 | `config_parameter` `writeoff_eligibility_minimum_ecl_stage` |
| BDR-WO-03 | Minimum logged collection activities before "collections exhausted" is satisfied | 1 | `config_parameter` `writeoff_minimum_collection_activities` |
| BDR-WO-04 | Whether an active (pending) Promise-to-Pay blocks ordinary eligibility | Blocks (`true`) | `config_parameter` `writeoff_block_if_active_promise_to_pay` |
| BDR-WO-05 | Whether PARTIAL write-off is permitted at all | Permitted (`true`) | `config_parameter` `writeoff_partial_allowed` |
| BDR-WO-06 | Whether/how a write-off can be reversed or corrected once executed (§15 of the original brief) | **Not built** — deliberately deferred; see [FSD.md](FSD.md) §8 "Remaining Gaps" | Not yet implemented anywhere |
| BDR-WO-07 | Recovery allocation order across principal/profit/late fee | `["late_fee", "profit", "principal"]` | `config_parameter` `recovery_allocation_order` |
| BDR-WO-08 | Legal status / dispute status / restructuring status as eligibility inputs | Not modelled — shown as `UNAVAILABLE`, informational only, since no such data exists anywhere in this platform | `services/write_off.py::evaluate_eligibility` |

## 7. Out of Scope

- Write-off reversal / correction (§15 of the original brief) — see
  BDR-WO-06.
- Any automatic/scheduled write-off (e.g. auto-writing-off every contract
  past N days DPD) — every write-off starts from an explicit human request.
- A dedicated chart-of-accounts / GL posting split for write-off or recovery
  events (inherited limitation of the pre-existing accounting-event
  boundary, not introduced by this feature).
- Legal-status, dispute-status, and restructuring-status-driven eligibility
  — no such data is captured anywhere in this platform yet (BDR-WO-08).
- A second maker-checker cycle for execution or for recovery — both are
  deliberately single-step actions by design (see BR-4, BR-9).
