# Business Requirements Document — Chart of Accounts + Versioned Posting Rules + Double-Entry Journal

**Status:** Implemented (code-verified — every section below traces to the
actual running code, not a design intention). Every seeded account and
posting rule is marked **DEMO / ILLUSTRATIVE PLACEHOLDER — FINANCE DECISION
REQUIRED**; nothing here is this company's approved chart of accounts,
GL code convention, or debit/credit treatment.

---

## 1. Business Objective & Problem Statement

The platform's existing accounting-event boundary (`AccountingEvent`, Gap
G-07 — see the repo root [README](../../README.md) and
[business-rules-catalogue.md](../business-rules-catalogue.md)) has, since
its introduction, stored only a single signed `amount` per financial event.
It could answer "what happened and for how much" but never "which accounts
moved, by how much each, and does it balance" — the single most repeated
open item in this project's own decision register (**BDR-31**, echoed at
**BDR-PG-07** for the payment-gateway feature and again, unnamed, in the
Write-off & Recovery BRD/FSD for `write_off_executed`/`recovery_received`).

This feature builds the **architecture** a real chart of accounts and
Finance-approved posting policy will eventually plug into:

- a controlled, maker-checker-gated **Chart of Accounts** (`ChartOfAccount`)
  — the account master, never hard-deleted, only deactivated;
- a **versioned posting rule** per accounting event type
  (`EventAccountMapping`/`EventAccountMappingLine`) that can express a
  compound, multi-line journal — not just one debit and one credit;
- a **double-entry journal** (`GLJournal`/`GLJournalLine`) generated from
  each `AccountingEvent`, validated to balance (`Σdebits == Σcredits`)
  before it is ever considered postable;
- a **mock outbound posting boundary** (`MockGlProvider.post_journal`)
  that can carry the full journal — header, lines, account codes, mapping
  version — not just the flat event, ready for a real ERP client to
  replace it later.

**This is a training/portfolio implementation.** The seeded chart of
accounts, its illustrative codes, and every posting rule are explicitly
demo data for development and testing — never presented as approved
company policy, a regulatory chart of accounts, or an IFRS/CBK/GCC
requirement.

## 2. Stakeholders

| Stakeholder | Interest |
|---|---|
| Finance / Accounting | A postable event is either a correct, balanced, explainable journal or a visibly UNMAPPED/FAILED one — never a silent gap and never a fabricated number. A mapping change is proposed, reviewed by a different user, and takes effect prospectively — a past journal never changes retroactively. |
| Credit / Risk | The existing ECL provision-movement double-posting risk (per-contract events vs. the portfolio roll-up) is resolved architecturally, not just documented — confirmed live: contract-level ECL journals for a run sum to exactly that run's own `total_provision_movement`, once. |
| Engineering | The journal engine reuses the existing `AccountingEvent` boundary, the existing generic maker-checker framework, and the existing CSV/XLSX/PDF export infrastructure — no parallel approval, audit, configuration, or reporting mechanism. |
| Compliance / Audit | For any postable event: which journal was generated, which accounts moved, which mapping version and who approved it, whether it balanced, and whether it reached the (mock) external GL — all independently reconstructable after a later mapping change, because a version is never edited in place and a journal keeps the exact version reference it was built with. |
| Future ERP integration owner | The posting boundary (`GlProvider.post_journal`) already carries a full journal (header + debit/credit lines + account codes + mapping version), not just a flat amount — a real ERP client is a drop-in replacement for `MockGlProvider`. |

## 3. Current State & Pain Points

Before this feature:

- `AccountingEvent.amount` was the only figure recorded per event — no
  debit/credit split, no account codes, no way to answer "which accounts
  moved."
- The ECL slice's portfolio-level `ecl_provision_movement` event and the
  per-contract `ecl_provision_created`/`_increased`/`_released`/
  `_override_adjustment` events both represented the same underlying
  provision movement — nothing in the code prevented both from being
  treated as postable, which would have double-counted the provision if a
  GL integration had been added naively.
- `settle_contract()` wrote a granular principal/late-fee/profit/rebate
  ledger breakdown behind its payoff figure; `return_contract()`, despite
  computing the identical `SettlementQuote` shape, only ever wrote one net
  `refund_issued` entry — a pre-existing gap (found and fixed in this
  feature's Checkpoint 1, as its own standalone commit) that would have
  made a compound `return` journal unresolvable.
- The mock ERP adapter (`erp_adapter.py::MockGlProvider`) could only ever
  receive one flat `AccountingEvent` — structurally incapable of carrying
  a debit/credit split even once one existed.

## 4. Business Requirements

BR-1. Every accounting event type must have a versioned posting-rule
classification — `POSTABLE`, `SUMMARY_ONLY`, or `RESERVED` — itself a
maker-checker-approved, auditable fact, not a hardcoded Python constant.

BR-2. A `POSTABLE` mapping must support one or more debit lines and one or
more credit lines (a compound journal), each line resolving its amount
from a controlled, closed vocabulary of amount sources — never an
arbitrary formula or user-entered code.

BR-3. Every resolved journal line amount must be a positive magnitude;
direction lives entirely in `posting_side`. A source amount that is
genuinely signed (confirmed for ECL release/override-adjustment, gateway
settlement differences) may only flip sides on a line explicitly marked
`reverse_on_negative` — never produce a negative debit or credit.

BR-4. A journal must satisfy `Σdebits == Σcredits` before it is ever
marked `READY`; an unbalanced or unresolvable journal is `FAILED` with an
explainable error and is never sent to the (mock) external GL.

BR-5. An event with no effective mapping is `UNMAPPED`, not dropped,
skipped, or silently posted — the underlying `AccountingEvent` is always
preserved regardless of journal outcome.

BR-6. A mapping change must never edit an approved version in place — it
always proposes the next version, activated (and the prior version closed
off, prospectively) only on a different user's approval. A journal already
generated under an older version keeps that version's reference and line
amounts forever, even after a newer version is approved.

BR-7. The `ecl_provision_movement` portfolio roll-up must never be
independently postable alongside the per-contract ECL movement events it
summarises — confirmed, not just designed: a posted ECL run's
contract-level journals sum to exactly that run's own
`total_provision_movement`, once.

BR-8. Chart of Accounts create/edit/deactivate and mapping
create/change/deactivate all reuse the existing generic maker-checker
framework (`ApprovalRequest`/`services/approvals.py`) — no new approval
mechanism, and the maker can never approve their own proposal.

BR-9. General Ledger, Trial Balance, Chart of Accounts, Event Mapping
Register, and Unmapped/Failed Events reporting must reuse the existing
CSV/XLSX/PDF export infrastructure (`reports.py`) — no parallel renderer.

BR-10. The Trial Balance must surface unmapped-event count/total and
failed-journal count as their own explicit fields — never folded silently
into the debit/credit totals as if they had posted.

## 5. Success Criteria / KPIs

These are the acceptance criteria this implementation was actually held to
(see [FSD.md](FSD.md) §1.1 for the full BRD→FSD→test traceability table):

- A simple mapped event (e.g. `payment_received`) produces a 2-line,
  balanced journal with both lines positive (test-verified).
- A compound mapped event (e.g. `contract_activated`, 4 lines;
  `early_settlement`, 6 lines; a full write-off, 5 lines) produces multiple
  balanced lines, verified to sum debits == credits algebraically, not just
  by construction.
- A confirmed-negative source (ECL release) correctly reverses sides on a
  `reverse_on_negative` line — never a negative debit/credit
  (test-verified).
- An unsupported negative amount, or an amount that cannot be resolved at
  all, produces a `FAILED` journal with an explainable `error_message` —
  never a fabricated zero (test-verified).
- An event dated outside any mapping's effective window is `UNMAPPED`, the
  underlying event preserved, `accounting_status` untouched
  (test-verified).
- `ecl_provision_movement` never gets its own journal (SUMMARY_ONLY, zero
  lines) — confirmed live against a real posted ECL run
  (test-verified: `test_contract_level_ecl_events_post_and_portfolio_
  rollup_never_duplicates_them`).
- Retrying the same business action (idempotent `emit()` replay) never
  creates a duplicate journal; retrying the posting job never
  double-posts an already-`POSTED` journal (test-verified).
- A later mapping version's approval never changes an already-generated
  journal's `mapping_version_id` or line amounts; an account rename never
  changes an existing journal line's account-code/name snapshot
  (test-verified — historical integrity).
- General Ledger running balance respects the account's own
  `normal_balance`; Trial Balance's `total_debits - total_credits == 0`
  when every postable event in the period resolved; unmapped/failed
  counts are surfaced as separate fields (test-verified).
- 415 backend tests and 125 frontend tests pass with zero regressions to
  any pre-existing feature (checkpoint-by-checkpoint baseline: 362 → 363 →
  384 → 396 → 415; 116 → 125 frontend).

## 6. Assumptions & Constraints

- **No real chart of accounts, no real ERP.** Every account code, account
  name, and posting rule is an illustrative placeholder for development
  and testing — see §17 below for the full list of `FINANCE DECISION
  REQUIRED` items this implementation deliberately leaves open.
- **No inventory cost / COGS data exists anywhere in this platform** —
  `Product` has only `cash_price`. `contract_activated`'s compound mapping
  and `return`'s compound mapping deliberately do NOT include
  Inventory/COGS lines; those accounts are seeded but unused by any
  mapping, and the gap is documented rather than fabricated.
- **`return`'s compound mapping does not reverse Sales Revenue** — doing so
  correctly would require the same missing cost data plus an unconfirmed
  gross/net presentation policy; the principal/late-fee write-down routes
  through the seeded "Settlement Difference / Suspense" account instead,
  explicitly flagged as pending a Finance decision, not silently resolved.
- **`cancellation`'s compound mapping does not reverse Sales
  Revenue/Receivable either** — cancellation only happens pre-delivery,
  and this platform's `contract_activated`/`down_payment_received` events
  only fire at delivery, so nothing has been booked yet to reverse (a
  pre-existing platform lifecycle fact, not something this feature
  changed).
- **Historical unmapped-event backfill is out of scope.** A mapping
  approved today is never applied retroactively to an event that predates
  its effective window — journal generation runs only when an
  `AccountingEvent` is newly created, never on an idempotent replay.
- **No period-close control.** `accounting_period` is a display/reporting
  field only; nothing in this platform prevents posting into a "closed"
  period because no period-close concept exists yet anywhere in this
  platform.

### Open Business Decisions (BDR-COA-*)

See §17 for the full, exact list mapped to this document's own numbering;
the headline items:

| ID | Decision needed | Current default |
|---|---|---|
| BDR-COA-01 | The company's real chart of accounts, account codes, numbering convention | Illustrative only — 18 demo accounts, simple 4-digit codes |
| BDR-COA-02 | Real debit/credit treatment for every event type | 23 illustrative demo mappings, one per confirmed `AccountingEventType` |
| BDR-COA-03 | Gross vs. net installment-receivable presentation | Gross (principal + unearned profit + late fees blended into one "Installment Receivable" account) |
| BDR-COA-04 | Inventory/COGS posting timing and account structure | Not modelled — no cost data exists anywhere in this platform |
| BDR-COA-05 | Return's full reversal treatment (Sales Revenue, Inventory) | Partial — receivable/profit reversal only, routed through Settlement Suspense |
| BDR-COA-06 | Real-time vs. batch GL posting | On-demand job only (matches every other mock adapter in this platform) |
| BDR-COA-07 | Real ERP posting interface / journal format | `GlProvider.post_journal(event, journal, lines)` — a mock boundary, not a real integration |

## 7. Out of Scope

- Any real ERP/GL connection, real company account codes, or statutory
  financial statements.
- A tax engine, multi-currency/FX revaluation, or consolidation across
  legal entities.
- Accounting-period closing/reopening and any period-lock enforcement.
- A journal reversal/correction workflow beyond this platform's existing
  business reversals (payment reversal, write-off recovery) — a
  discovered-in-error journal has no "correct and repost" flow yet.
- Historical unmapped-event backfill (regenerating a journal for an event
  that predates the mapping that would now cover it) — deliberately not
  implemented; would need to be its own explicit, auditable, role-gated
  operation (see [FSD.md](FSD.md) §8).
- Real inventory valuation methodology, product depreciation, or a real
  payment-gateway settlement accounting policy.
- Regulatory returns and any CBK/GCC/tax/audit-specific chart-of-accounts
  requirement — this platform does not assume none exists; it simply does
  not have visibility into what one would require.
