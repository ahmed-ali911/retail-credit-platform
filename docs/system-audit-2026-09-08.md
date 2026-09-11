# Retail Credit & Installment Sales Platform — Full System Audit & Lifecycle Walkthrough

**Date:** 2026-09-08
**Method:** direct read of the current repository — every model, migration, service, API router, schema, `config/business_rules.yaml`, all 267 backend + 103 frontend tests, the frontend screens, and the two prior audit docs (`docs/enterprise-assessment.md`, `docs/business-rules-catalogue.md`). Nothing below is carried over from a report without re-verifying it against source.
**Status of this phase:** AUDIT ONLY. No code was changed. No feature was built.

> This is the **Alghanim Retail Credit & Installment Sales Platform** — a retail *installment sale* (the company sells a physical product; **no cash is ever disbursed to the customer**). It is a different project from the earlier CCFMP / bank-lending work.

---

## 1. Executive Summary

### The one-line answer

**The platform can execute the "happy path" of a retail installment sale end-to-end today** — customer → application → automated credit decision → priced offer → acceptance → contract → delivery → installment payments → allocation → declining-balance profit recognition → auto-close on full repayment — **with a real, tested pricing engine, a real allocation waterfall, a real overdue/late-fee engine, real collections cases, real early-settlement/cancellation/return flows, a real (thin) accounting-event boundary, a real bank-reconciliation matcher, and a real first slice of ECL.**

**It cannot yet be operated as a real business** because several things that a live installment lender *must* have are missing, stubbed, or structurally thin:

| # | What breaks the operation | State |
|---|---|---|
| 1 | **The down payment is never actually collected.** `down_payment_reference` is free text; no `Payment` row, no reconciliation, no money movement. The contract's receivable is created *net of* a down payment that was never verified. | STUB |
| 2 | **A recorded payment reduces the receivable immediately — before the money is confirmed in the bank.** Allocation happens at record time; reconciliation happens later and only sets a status flag. A "payment" that never settles has still cured the delinquency and recognised profit. | ARCHITECTURAL RISK |
| 3 | **No payment gateway / channel at all.** There is no `PaymentProvider`, no Easy/gateway adapter, not even a mock class. Payments are typed in by staff. | MISSING |
| 4 | **No KYC, no identity verification, no document capture, no T&C / consent record.** You cannot prove who the customer is or which terms they accepted. | MISSING |
| 5 | **Inventory can be over-sold beyond the last unit.** The stock gate is at *offer generation*, not *acceptance*; N offers generated against a 1-unit product can all be accepted → negative stock. No row lock, no reservation. | GAP / RISK |
| 6 | **No write-off / recovery.** A customer who defaults permanently has no lifecycle end other than staying `active` forever, or a `return`. | MISSING |
| 7 | **No scheduled jobs.** DPD, late fees, broken-promise detection, maturity closure, offer/quote expiry, ECL close — all manual (`POST /jobs/assess-overdue`, `POST /ecl/run`, `POST /jobs/post-accounting-events`). A contract that reaches maturity by paying exactly to zero auto-closes; one that stops one cent short stays `active` forever. | MISSING |
| 8 | **The accounting-event stream carries a single signed amount per event — no debit/credit, no account codes.** It is a *journal-ready integration boundary*, not a GL. The chart-of-accounts mapping (BDR-31) is unmade. | BY DESIGN (deferred) |
| 9 | **The immutable ledger exists but nothing reads it.** It is a Phase-1 dual-write shadow. The authoritative figures are still the mutated `installment` / `contract` rows, which `_close_out_schedule` overwrites in place. | PARTIAL |
| 10 | **Pricing has exactly one global dimension: tenor.** No per-product, per-category, per-segment, per-campaign, per-risk pricing; no effective-dated pricing rules; no `pricing_rule_id` on the offer. | GAP (BDR-02/11) |

### What's genuinely strong and must NOT be rebuilt

- **Pricing engine** (`pricing.py::build_plan`) — pure, deterministic, exact-reconciling schedule; declining-balance profit split is real and unit-tested to sum exactly to totals.
- **Allocation waterfall** (`allocation.py::allocate`) — pure, "oldest installment fully settled including principal before any newer installment's profit"; correctly handles partial payments and spanning payments.
- **Entity separation** — `Customer` / `CustomerProfile` / `CreditApplication` / `InstallmentOffer` / `SalesOrder` / `InstallmentContract` / `PaymentSchedule` / `Installment` / `Payment` / `PaymentAllocation` / `LateFeeCharge` / `ContractClosure` are all distinct, correctly related, never merged.
- **Config externalisation** — every policy number is a DB-backed `config_parameter` seeded from YAML, editable at runtime through maker-checker.
- **Maker-checker** — generic `ApprovalRequest`; `decided_by != requested_by` enforced in the service layer (not convention); 4 action types wired.
- **RBAC + audit** — every route token-gated, sensitive routes role-gated, ownership checks on customer-facing reads, `AuditEvent` on state changes.
- **Idempotency** — `Payment.external_reference` unique per contract; `AccountingEvent.event_reference` unique; reconciliation naturally idempotent.
- **The declining-balance amortization SHAPE is correct** (verified below).
- **ECL is completely separated from customer pricing** — ECL is computed post-activation from the receivable and DPD; nothing in it feeds back into the price the customer pays.

### Overall readiness

| Dimension | Grade |
|---|---|
| Demo / walkthrough of the core installment sale | 🟢 works end-to-end |
| Financial-calculation correctness (pricing, allocation, amortization split) | 🟢 real and tested |
| Financial-record integrity (immutable history, no overwrite) | 🟡 ledger exists, not authoritative; rows still overwritten |
| Real-money operation (gateway, DP collection, reconcile-before-allocate) | 🔴 not there |
| Compliance / legal (KYC, T&C, consent, write-off) | 🔴 not there |
| Collections beyond "open a case + log a call + track a promise" | 🔴 not there |
| Accounting / GL | 🟡 boundary only, no CoA |
| ECL / provisioning | 🟡 real first slice (Path C computed; Path B structure-only) |
| Operational automation (schedulers) | 🔴 all manual |

---

## 2. What Is Actually Implemented — capability register

Classification key: **A** = implemented + tested + end-to-end · **B** = backend only · **C** = frontend only · **D** = partial · **E** = mock/simulation · **F** = placeholder · **G** = documented only · **H** = missing · **I** = implemented but business logic incorrect/insufficient.

| Capability | Class | Model | Service | Endpoint(s) | UI screen | Tests | Notes |
|---|---|---|---|---|---|---|---|
| Customer + Profile creation | **A** | `Customer`, `CustomerProfile` | — | `POST /customers`, `GET /customers`, `GET /customers/{id}` | Create Customer, Customer Directory, Customer detail | `test_customers.py` | Two separate entities. `national_id` unique. No KYC. `risk_score` is a manual int stub. Product/customer creation **not** audited (`create_product` has no `record_event`). |
| Product (cash price + flat stock) | **D** | `Product` | — | `POST /products` (credit desk/admin), `GET /products`, `POST /products/{id}/stock-adjustment` | Create Product, Product Directory, Inventory | `test_gap_fixes.py` (role gate), `test_inventory.py` | Only `cash_price`, `category`, `installment_eligible`, `stock_quantity`, `reserved_quantity` (always 0). No SKU, serial, warehouse, branch. |
| Credit application (online + branch) | **A** | `CreditApplication` (`channel` enum) | `assessment.py` | `POST /applications`, `/submit`, `/review`, `GET /applications?status=`, `GET /applications/{id}` | New Application, Review Queue, Review detail | `test_applications.py`, `test_assessment.py`, `test_manual_review.py` | `channel` stored but **no behaviour branches on it**. `created_by` free-text string (not a user FK). `requested_amount` is **not** validated against `product.cash_price`. |
| Automated credit assessment (4 rules) | **A** | `AssessmentResult` | `assessment.py::assess_application` | inside `POST /applications/{id}/submit` (synchronous) | AssessmentPanel | `test_assessment.py`, `test_affordability.py`, `test_exposure.py` | Rules: min income → reject; DBR → refer; risk band → approve/refer/reject; exposure → refer. Precedence rejected > referred > approved. Config snapshot persisted per result. |
| DBR / affordability basis (S-2 fix) | **D** | — | `assessment.py::estimate_installment` + `offers.py::_affordability_recheck` | submit + `/offer` | — | `test_affordability.py` | Uses the pricing rate table + assumed min down payment; re-checks the real peak installment at offer time (`block` / `warn_only`). Policy (obligation basis) is **BDR-22, unconfirmed**. |
| Customer exposure aggregation (S-3 fix) | **A** | — | `exposure.py::compute_exposure` | `GET /customers/{id}/exposure`; used in assessment Rule 4 | Customer detail (Exposure card) | `test_exposure.py` | Company-wide only (any other `exposure_aggregation_level` raises). Sums `principal+profit+late_fees` across non-closed contracts. Number (`max_customer_exposure_kwd`=8000) is a placeholder. |
| Manual review of `referred` (S-1 fix) | **A** | 2nd `AssessmentResult` (`source=manual`) | — | `POST /applications/{id}/review` | Review detail | `test_manual_review.py` | `approved→approved`, `rejected→rejected`, `return_for_info→draft`. **No maker-checker on the review itself, no amount-band approval authority** (BDR-25). |
| Pricing / profit engine | **A** | `InstallmentOffer` (frozen snapshot) | `pricing.py` | `POST /applications/{id}/offer` | Offer | `test_pricing.py`, `test_offer_flow.py` | Pure deterministic. `total_profit = round(principal_financed × rate, 2)`. Single global `tenor_profit_rate_table`. See §7. |
| Installment schedule generation | **A** | `PaymentSchedule`, `Installment` | `pricing.py::build_plan`, `offers.py::accept_offer` | `POST /offers/{id}/accept` | ScheduleTable | `test_pricing.py`, `test_offer_flow.py` | Declining-balance profit split, straight-line principal, exact cumulative rounding. Due dates anchored to acceptance date (`add_months`). |
| Offer acceptance → Contract + Sales Order | **A** | `SalesOrder`, `InstallmentContract` | `offers.py::accept_offer` | `POST /offers/{id}/accept` | Offer | `test_offer_flow.py` | Expiry check, down-payment-confirmed gate, optional DP cross-check. Frozen schedule copied verbatim. Stock −1 here. |
| Down-payment collection | **E / F** | — (only `SalesOrder.down_payment_amount` + `Offer.down_payment_reference`) | — | inside `/accept` | — | — | **Stubbed.** No `Payment`, no ledger entry, no reconciliation. `down_payment_received` accounting event fires at delivery for the stored amount. |
| Contract delivery | **D** | `InstallmentContract.activated_at` | `offers.py::confirm_delivery` | `POST /contracts/{id}/confirm-delivery` | Contract (Confirm delivery) | `test_offer_flow.py`, `test_accounting_events.py` | Bare status flip `created→active`. Emits `contract_activated` + `down_payment_received` events + ECL day-one assessment. **No serial/IMEI, no delivery record, no signature.** |
| Inventory guard | **I** | `Product.stock_quantity` | `offers.py`, `closure.py::_release_stock` | offer generation gate; accept −1; cancel/return +1 | Inventory | `test_inventory.py` | Gate is at **generation** not **acceptance**; no floor at acceptance; no lock. See §11 — **over-sell beyond the last unit is possible.** |
| Receivable view | **A** | derived | `receivable.py::build_receivable` | `GET /contracts/{id}/receivable` | Contract (Receivable card) | `test_payments_flow.py` | `outstanding_receivable = principal + profit`; **late fees a separate line**. Computed from installment rows, **not** the ledger. |
| Payment recording + allocation | **A** | `Payment`, `PaymentAllocation` | `payments.py::record_payment`, `allocation.py::allocate` | `POST /contracts/{id}/payments` | Contract (Record payment) | `test_payments_flow.py`, `test_allocation.py` | Idempotent on `external_reference`. **Partial payments fully supported.** Overpayment rejected (422). Oldest-first + Late Fee→Profit→Principal. Ledger dual-write. |
| Profit recognition | **D / BDR-05** | `contract.unearned_profit_balance` (in-place) + `LedgerEntry(profit_recognized)` (write-only) | `payments.py` | on payment | Contract | `test_ledger.py` | **Cash-basis** — recognised when the profit portion of an installment is actually paid. Not time-based. Recognition method is **BDR-05 ("Finance/audit sign-off mandatory")**. |
| Auto-close on full repayment (S-10 fix) | **A** | `ContractClosure(reason=normal)` | `closure.py::close_if_fully_repaid` | inside `POST /contracts/{id}/payments` | Contract | `test_p0_fixes.py` | Fires **only** on the payment that lands the contract exactly on zero. No maturity job for a contract that stops one cent short. |
| Overdue / DPD / late fee | **A** | `LateFeeCharge`, `Installment.status=overdue` | `overdue.py::assess_overdue` | `POST /jobs/assess-overdue` (admin, manual) | Collections ("Run overdue assessment" — admin) | `test_overdue.py`, `test_gap_fixes.py` | `dpd = (as_of − due_date).days`; fee only when `dpd > grace` (strict); fee = `2% × (principal_component + profit_component)` of the installment's **scheduled total**. `late_fee_max_per_contract` now enforced. One fee per installment ever. **No recurring fees.** **No scheduler.** |
| Collections cases | **A** | `CollectionCase`, `CollectionActivity` | `collections.py` | `/collections/*` | Collections list, Collection case detail | `test_collections.py`, `test_gap_fixes.py` | Auto-open on first overdue, auto-close when no `overdue` installments remain. Activities logged manually. **No escalation, restructure, legal, write-off, recovery, DPD action bands.** |
| Promise-to-pay lifecycle (S-9 fix) | **A** | `CollectionActivity.promise_status` | `collections.py::evaluate_promises_after_payment` / `evaluate_overdue_promises` | on payment / on overdue run | Collection case detail | `test_gap_fixes.py` | `pending → kept` (payment ≥ promised since promise) / `broken` (past due, unpaid). Manual override with reason. Feeds ECL Path B. |
| Early settlement + rebate (BDR #7) | **A** | `ContractClosure(reason=early_settlement)` | `closure.py::build_settlement_quote` / `settle_contract` | `GET /settlement-quote`, `POST /settle` | Contract (Closure card) | `test_closure.py`, `test_settlement_rebate.py` | Quote always recomputed server-side; amount-mismatch → 422. Default rebate 0%; a **deviating** rebate → maker-checker (`contract.settlement_rebate`). Ledger `profit_rebated` separate entry. |
| Cancellation (pre-delivery) | **A** | `ContractClosure(reason=cancellation)` | `closure.py::cancel_contract` | `POST /contracts/{id}/cancel` | Contract | `test_closure.py` | `down_payment × down_payment_refund_pct_cancellation` (1.0 default). **Not maker-checker gated.** Stock +1. `Refund` is just a number. |
| Return (post-delivery) | **D / I** | `ContractClosure(reason=return_)` | `closure.py::return_contract` | `POST /contracts/{id}/return` | Contract | `test_closure.py`, `test_p0_fixes.py` | Single signed `net_adjustment`. **No profit reversal itemisation, no restocking fee, no condition/serial check, no `Refund` entity, no inventory-return record.** `ownership_transfers_on_delivery` echoed, no logic. Not maker-checker gated. |
| Bank reconciliation (S-5 fix) | **D** | `BankStatementLine`, `ReconciliationException`, `Payment.reconciliation_status` | `reconciliation.py` | `/reconciliation/*`, `POST /reconciliation/bank-lines/upload` (.xlsx) | Reconciliation | `test_reconciliation.py` | Real matcher (ref → amount+date → exception). Manual match via maker-checker. **Runs only on manually-imported lines — no bank feed.** **Allocates-then-reconciles** (see §12/§13). |
| Payment gateway (Easy) | **H** | — | — | — | — | — | **Does not exist.** No adapter, no mock class, no `gateway_reference` writer, no `channel` on `Payment`, no `PaymentInitiation`. |
| Accounting-event boundary (G-07) | **B** | `AccountingEvent` | `accounting.py::emit` / `emit_unscoped` / `post_pending`, `erp_adapter.py::MockGlProvider` | `GET /accounting/events`, `POST /jobs/post-accounting-events` | Snapshot (counts only) | `test_accounting_events.py` | 11 event types. **Single signed `amount` — no debit/credit, no account codes.** Mock GL always succeeds. On-demand posting, not scheduled. |
| Immutable ledger (S-4, Phase 1) | **D** | `LedgerEntry` (append-only) | `ledger.py::record_entry` | — (no read endpoint) | — | `test_ledger.py` | **Write-only dual-write.** No read path uses it. Authoritative figures are still the mutated rows. Tested to reconcile with the old calculations. |
| ECL & provision (first slice) | **D** | `ECLRun`, `ECLAssessment` | `ecl.py`, `ecl_engine.py` (3-path provider) | `/ecl/*` (`dashboard`, `assessments`, `contracts/{id}`, `runs`, `run`) | ECL & Provision | `test_ecl.py` | Path C (`dpd_banded`) fully computed (`EAD × band %`); Path A (`simplified_lifetime`) computed; **Path B (`three_stage`) is structure-only — PD/LGD/ECL = `null`**. One `ecl_provision_movement` event per run. **No scheduler, no PD/LGD source, no macro overlay.** See §10 (ECL audit) below. |
| Maker-checker approvals | **A** | `ApprovalRequest` | `approvals.py` | `/approvals/*`, per-action request endpoints | Approvals | `test_approvals.py`, `test_settlement_rebate.py` | `decided_by != requested_by` enforced. Actions: `late_fee.waive`, `config.update`, `reconciliation.manual_match`, `contract.settlement_rebate`. |
| Config management | **A** | `ConfigParameter` | `config_service.py` | `GET /config/parameters`, `PUT /config/parameters/{key}` (→202 pending) | Configuration | `test_config_externalization.py` | Two-step maker-checker. **No effective-dated config, no `rule_set_version` on decisions** (S-11). |
| Reporting / MIS | **B/D** | — | `reports.py` | `/reports/*` (contracts, profitability, 5 summaries, ~10 sub-reports, aging), CSV/XLSX/PDF | Reports Center, 5-tab Dashboard, Snapshot | `test_reports.py`, `test_report_subcategories.py` | Read-only over existing tables. **No charts of ECL/portfolio-at-risk driven metrics beyond the ECL page; no scheduled/emailed/saved reports.** DPD buckets display-only. |
| Auth / RBAC / audit | **A** | `User`, `AuditEvent` | `security.py`, `auth.py`, `audit.py` | `/auth/*`, cross-cutting | Login, user menu, Audit Log | `test_auth.py`, `test_rbac.py`, `test_audit.py` | HS256 JWT, 30-min, `localStorage`. **No refresh/revocation, no CORS allow-list, no rate limit, no `/health` DB check** (BDR-30 / G-30). |
| Reference codes | **A** | computed, not stored | `references.py` | serialisation | RefCode | `test_references.py` | `CU-/PR-/AP-/OF-/SO-/CN-/PY-/CC-` display identity. |
| KYC / documents / identity verification | **H** | — | — | — | — | — | **Missing entirely.** |
| T&C / consent / promissory note | **H** | — | — | — | — | — | **Missing entirely.** Offer acceptance has no T&C step. |
| Write-off / recovery | **H** | — | — | — | — | — | **Missing entirely.** |
| Restructuring / rescheduling | **H** | — | — | — | — | — | **Missing entirely.** |
| Notifications (SMS/email/push) | **H** | — | — | — | — | — | **Missing entirely.** No outbox, no provider, nothing sent. |
| Scheduled processing / job runner | **H** | — | — | manual `POST /jobs/*` only | — | — | **Missing.** Everything is a manual trigger. |
| Customer self-service portal | **H** (backend hooks exist) | `customers.user_id` | `authorize_owner_or_roles` | owner-checked reads exist | — | — | No customer-facing UI; no registration/OTP. |
| Standardised API errors (RFC 9457) | **H** | — | — | `{"detail": ...}` everywhere, `str` or `list` | — | — | Inconsistent error shape. |
| Generic `Idempotency-Key` on POSTs | **H** (only `Payment.external_reference`) | — | — | — | — | — | Only payments are idempotent by key. |

---

## 3. What Is Only Mocked / Placeholder / Stubbed

| Thing | What it actually is | Where |
|---|---|---|
| **Payment gateway / Easy** | Does not exist at all (not even a mock class). | — |
| **Down-payment collection** | `down_payment_reference` is free text stored on the offer; no money moves, no `Payment` row. | `offers.py::accept_offer` |
| **Bank feed** | `BankStatementLine` rows are created one-at-a-time via `POST /reconciliation/bank-lines` or an `.xlsx` upload. No connection to any bank. | `reconciliation.py::ingest_bank_line` |
| **ERP / GL** | `MockGlProvider.post_event()` always returns `ok=True` with `external_gl_reference = "MOCK-GL-{uuid}"`. No account codes, no double-entry. | `erp_adapter.py` |
| **Credit bureau** | No integration. `Customer.risk_score` is a nullable int set manually by whoever creates the customer. | `models/customer.py` |
| **KYC / income verification** | Self-reported `monthly_income` / `existing_monthly_obligations` on `CustomerProfile`, never verified. | `models/customer.py` |
| **ECL Path B (three_stage) PD/LGD/ECL** | Deliberately `null` with the string `"n/a — no PD/LGD source configured"` — never fabricated. Only stage 1/2/3 classification is real. | `ecl_engine.py::ThreeStageProvider` |
| **ECL provision % per DPD band** | `{current: 0.5%, 1-30: 3%, 31-60: 15%, 61-90: 40%, 91+: 75%}` — the yaml comment says **"FICTIONAL PLACEHOLDER percentages — NOT a real loss-rate curve"**. | `config/business_rules.yaml` |
| **`tenor_profit_rate_table`** | `{6: 0.04, 12: 0.09, 18: 0.135, 24: 0.18, 36: 0.30}` — "invented demo numbers, not real commercial installment rates". | `config/business_rules.yaml` |
| **Every closure %** | `down_payment_refund_pct_cancellation = 1.0`, `down_payment_refund_pct_return = 0.0`, `early_settlement_profit_rebate_pct = 0.0`, `ownership_transfers_on_delivery = true` — all "NOT CONFIRMED POLICY". | `config/business_rules.yaml` |
| **`minimum_monthly_income = 300`, `maximum_debt_burden_ratio = 0.40`, `risk_score_auto_approve_min = 650`, `risk_score_refer_min = 600`, `max_customer_exposure_kwd = 8000`, `late_fee_grace_period_days = 10`** | All "FICTIONAL PLACEHOLDER". | `config/business_rules.yaml` |
| **`late_fee_rate = 0.02`** | The yaml comment claims **"CONFIRMED BUSINESS RULE ... the agreed value is 0.02"**. Per the audit brief this should NOT be treated as confirmed until Alghanim signs it off — **flagged as a mismatch between the yaml claim and the brief's instruction**. | `config/business_rules.yaml` |
| **`late_fee_once_per_installment = true`** | Inert — read only to assert the supported mode; the flag's value has no effect. | `overdue.py` |
| **`dpd_report_buckets`** | Display grouping for the aging report only — explicitly "NOT a collections-action policy". | `config/business_rules.yaml` |
| **`settlement_quote_validity_days = 3`, `offer_validity_days = 7`** | `offer_validity_days` is enforced (offer expiry); `settlement_quote_validity_days` is informational only (`/settle` always recomputes). | `offers.py`, `closure.py` |
| **`reconciliation_date_tolerance_days = 0`** | Placeholder — same-day only until real T+N settlement timing is known. | `config/business_rules.yaml` |
| **`Product.reserved_quantity`** | Field exists, always `0`, never written by any code path. | `models/product.py` |
| **`Payment.gateway_reference`** | Field exists, always `null`, never written. | `models/payment.py` |
| **`created_by` on `CreditApplication`** | Free-text string, defaults `"system"` — not a user FK. | `models/credit_application.py` |
| **Immutable ledger reads** | Phase 1 = write-only; no endpoint or calculation reads `LedgerEntry`. | `ledger.py` |
| **`POST /jobs/post-accounting-events` scheduling** | On-demand only; there is no scheduler anywhere in the codebase. | `accounting.py` |

---

## 4. Business Lifecycle Walkthrough (architecture capability check)

Using **Sarah** buying a **Laptop** — cash price **300 KWD** — with illustrative (NOT policy) terms *12 mo → sale price 380*, *24 mo → sale price 450*.

**Can the architecture represent each concept?**

| Concept | Represented? | Where | Notes |
|---|---|---|---|
| Cash price | ✅ | `Product.cash_price`, `InstallmentOffer.cash_price` | Preserved separately, frozen on the offer. |
| Installment sale price | ✅ | `InstallmentOffer.installment_sale_price`, `SalesOrder.sale_price` | `= cash_price + total_profit`. |
| Tenor-dependent pricing | ⚠️ | `tenor_profit_rate_table` (config JSON, per tenor) | Only the **rate** varies by tenor. To get *380 @ 12mo* from a 300 cash / 15% min-DP (255 financed) laptop you'd need rate ≈ 80/255 = 31.4%; *450 @ 24mo* needs ≈ 58.8%. The table **can** encode that, but there is **no way to set an absolute sale price**, and **no per-product / per-category override** — the table is global. |
| Total profit / margin | ✅ | `InstallmentOffer.total_profit`, `InstallmentContract.total_profit` | `= round(principal_financed × rate, 2)`. |
| Down payment | ⚠️ | `SalesOrder.down_payment_amount`, `Offer.down_payment` | Amount is stored and frozen; **collection is stubbed** (no Payment). |
| Receivable | ✅ | derived (`build_receivable`) | `= Σ principal_outstanding + Σ profit_outstanding` (late fees separate). At activation = `amount_financed = principal_financed + total_profit`. |
| Installment amount | ✅ | `Installment.principal_component + profit_component` | Per-installment, frozen from the offer schedule. |
| Profit amortization | ✅ (shape) | `pricing.py::build_plan` | Declining-balance split across installments (verified §8). |
| Early settlement adjustment | ✅ | `closure.py::build_settlement_quote` | Configurable rebate; deviation → maker-checker. |
| Late fees | ✅ | `LateFeeCharge` | Separate ledger, never folded into profit/principal. |
| Accounting events | ✅ (boundary) | `AccountingEvent` | Single signed amount; no CoA. |
| ECL | ⚠️ | `ECLAssessment` / `ECLRun` | Real first slice; Path C computed, Path B structure-only; no scheduler. |

**Verdict:** the entity model can carry the shape of a tenor-varying retail installment sale. The **pricing rule dimensionality** and the **down-payment collection** are the two architectural weak points for this scenario.

---

## Scenario A — Online customer, normal full repayment

| # | Step | Exists? | Where | Data created / changed | Status change | Rule | Tested? | End-to-end? |
|---|---|---|---|---|---|---|---|---|
| 1 | Customer selects laptop | n/a (no catalogue browse) | — | — | — | — | — | UI: manual product id entry only |
| 2 | Chooses installment purchase | implicit | — | — | — | — | — | — |
| 3 | Application created | ✅ | `POST /applications` | `CreditApplication(status=draft)` | → `draft` | role gate; product must be installment-eligible | ✅ | ✅ |
| 4 | Customer info captured | ✅ (at customer creation, earlier) | `POST /customers` | `Customer` + `CustomerProfile` | — | national_id unique | ✅ | ✅ |
| 5 | Product + price retrieved | ✅ | `product` FK on application | — | — | — | ✅ | ✅ |
| 6 | Requested tenor selected | ✅ | `requested_tenor_months` | — | — | 1–120 | ✅ | ✅ |
| 7 | Credit assessment begins | ✅ (synchronous) | `POST /applications/{id}/submit` → `assess_application` | `AssessmentResult` | `draft→submitted→under_assessment→<decision>` | 4 rules, precedence | ✅ | ✅ |
| 8 | KYC / identity verification | ❌ **MISSING** | — | — | — | — | — | — |
| 9 | Credit bureau inquiry | ❌ **MISSING** (manual `risk_score`) | — | — | — | — | — | — |
| 10 | Income / obligation assessment | ⚠️ self-reported only | `assessment.py` Rules 1–2 | — | — | min income, DBR | ✅ | partial (not verified) |
| 11 | Existing exposure aggregation | ✅ | `exposure.py` (Rule 4) | — | — | company-wide sum ≤ 8000 (placeholder) | ✅ | ✅ |
| 12 | Credit policy evaluation | ✅ | `assessment.py` | `AssessmentResult.triggered_rules` | — | config-driven | ✅ | ✅ |
| 13 | Risk scoring | ⚠️ banding of a manual score | Rule 3 | — | — | 2 thresholds | ✅ | partial |
| 14 | Decision = APPROVED | ✅ | — | `application.status = approved` | → `approved` | — | ✅ | ✅ |
| 15 | Offer generated | ✅ | `POST /applications/{id}/offer` → `generate_offer` | `InstallmentOffer(status=presented)` + `AssessmentResult(source=offer_affordability_recheck)` | — | approved only; stock > 0; tenor rate exists; min DP; affordability re-check | ✅ | ✅ |
| 16 | Down payment calculated | ✅ | `min_down_payment = round(cash_price × 0.15, 2)` | — | — | placeholder pct | ✅ | ✅ |
| 17 | Installment sale price calculated | ✅ | `pricing.build_plan` | `offer.installment_sale_price` | — | `cash + total_profit` | ✅ | ✅ |
| 18 | Total receivable calculated | ✅ | `offer.amount_financed` | — | — | `principal_financed + total_profit` | ✅ | ✅ |
| 19 | Profit component calculated | ✅ | `offer.total_profit` | — | — | `principal_financed × rate` | ✅ | ✅ |
| 20 | Installment schedule generated | ✅ | `build_plan` | `offer.schedule_preview` (JSON, frozen) | — | declining-balance profit, straight-line principal | ✅ | ✅ |
| 21 | Customer accepts offer | ✅ | `POST /offers/{id}/accept` | `SalesOrder`, `InstallmentContract(status=created)`, `PaymentSchedule`, N × `Installment` | offer → `accepted` | not expired; DP confirmed | ✅ | ✅ |
| 22 | T&C acceptance | ❌ **MISSING** | — | — | — | — | — | — |
| 23 | Down payment collected | ❌ **STUB** | — | `offer.down_payment_reference` (free text) | `offer.down_payment_confirmed = true` | must be `true` to accept | ✅ (the flag) | **no** (no money, no Payment) |
| 24 | Payment recorded / confirmed | ❌ for the DP | — | — | — | — | — | — |
| 25 | Sales Order created | ✅ | `accept_offer` | `SalesOrder` | — | 1:1 with offer | ✅ | ✅ |
| 26 | Installment Contract created | ✅ | `accept_offer` | `InstallmentContract` | → `created` | `unearned_profit_balance = total_profit` | ✅ | ✅ |
| 27 | Receivable established | ✅ | derived | — | — | — | ✅ | ✅ |
| 28 | Inventory reserved / deducted | ⚠️ | `accept_offer` | `product.stock_quantity −= 1` | — | **no floor check, no lock** | ✅ | partial (over-sell risk) |
| 29 | Product delivered | ⚠️ bare flip | `POST /contracts/{id}/confirm-delivery` | `activated_at` | `created→active` | must be `created` | ✅ | partial (no delivery record) |
| 30 | Contract becomes ACTIVE | ✅ | — | — | → `active` | — | ✅ | ✅ |
| 31 | Accounting events at delivery | ✅ | `confirm_delivery` | `contract_activated`, `down_payment_received` (`AccountingEvent`, pending) | — | idempotent | ✅ | ✅ |
| 32 | ECL day-one assessment | ✅ | `ecl.initial_assessment` | `ECLAssessment` (run_id null) | — | `EAD × band %` | ✅ | ✅ |
| 33 | Installment 1 becomes due | ⚠️ passive | — | `due_date` was set at acceptance | — | no "becomes due" job | n/a | — |
| 34 | Customer pays | ✅ | `POST /contracts/{id}/payments` | `Payment(status=applied)`, `PaymentAllocation`, `LedgerEntry` × buckets | — | active; amount ≤ outstanding; idempotent | ✅ | ✅ |
| 35 | Payment allocated | ✅ | `allocation.allocate` | installment `principal_paid`/`profit_paid`, `contract.unearned_profit_balance` | installment → `paid` / `partially_paid` | oldest-first; LF→Profit→Principal | ✅ | ✅ |
| 36 | Principal + profit components updated | ✅ | `record_payment` | — | — | — | ✅ | ✅ |
| 37 | Accounting events per payment | ✅ | `record_payment` | `payment_received`, `profit_recognized` | — | idempotent | ✅ | ✅ |
| 38 | Repeat to final installment | ✅ | — | — | — | — | ✅ | ✅ |
| 39 | Final payment received | ✅ | `POST .../payments` | last `Payment` | — | amount == outstanding accepted | ✅ | ✅ |
| 40 | Outstanding → zero | ✅ | derived | — | — | — | ✅ | ✅ |
| 41 | Contract CLOSED | ✅ | `close_if_fully_repaid` | `ContractClosure(reason=normal, adjustment=0)` | `active→closed` | fires only on the zeroing payment | ✅ | ✅ |
| 42 | Accounting events complete | ✅ | — | `contract_closed` event | — | — | ✅ | ✅ |
| 43 | Audit trail complete | ✅ | `record_event` throughout | `AuditEvent` rows | — | — | ✅ | ✅ |
| 44 | Bank reconciliation of the payments | ⚠️ separate manual step | `POST /reconciliation/run` | `Payment.reconciliation_status` | — | ref → amount+date → exception | ✅ | partial (manual, post-hoc) |

**Scenario A verdict:** ✅ **works end-to-end** for the money that flows through `POST /contracts/{id}/payments`. Two holes: the **down payment** (step 23) never becomes a real payment, and **KYC / T&C** (steps 8, 22) don't exist. Reconciliation is a manual afterthought that doesn't gate anything.

---

## Scenario B — Partial payment / missed installment

Contract active; installments 1–3 paid on time; installment 4 = 100 KWD due; customer pays **60**.

| # | Step | Behaviour in code | Correct? |
|---|---|---|---|
| 1 | Installment 4 becomes due | passive (`due_date` set at acceptance) | ⚠️ no event |
| 2 | Due date passes | nothing happens until `POST /jobs/assess-overdue` is manually run | ⚠️ manual |
| 3 | Grace period | `late_fee_grace_period_days = 10` (placeholder). A fee is created only when `dpd > 10` **strictly**. | ✅ mechanism, ⚠️ number |
| 4 | DPD calculation | `dpd = (as_of − due_date).days`, computed on the overdue run and re-derivable in reports/ECL. **Not stored on the installment or contract.** | ✅ (transient) |
| 5 | Partial payment received (60) | `POST /contracts/{id}/payments {amount: 60}` — **accepted** (60 ≤ outstanding). | ✅ **partial payments fully supported** |
| 6 | Payment allocation | `allocate(60, [installment 4])` → within installment 4: Late Fee (0 if not yet assessed) → Profit → Principal. If installment 4 = 25 profit + 75 principal, 60 pays 25 profit + 35 principal. | ✅ |
| 7 | Remaining installment balance | `installment_4.principal_outstanding = 40`, `profit_outstanding = 0`. | ✅ |
| 8 | Remaining principal / profit | tracked per component. | ✅ |
| 9 | Whether late fee applies | Only if `POST /jobs/assess-overdue` is run with `dpd > 10`. If the installment is *already* `overdue` and a fee was *already* assessed, **no second fee** (one per installment ever). | ✅ mechanism |
| 10 | How the late fee is calculated | `2% × (installment_4.principal_component + installment_4.profit_component)` = `2% × 100` = **2.00** — i.e. **2% of the installment's ORIGINAL scheduled total, NOT the 40 still outstanding, NOT 2% of 60.** | ⚠️ **This is a business-policy choice (BDR-10). Confirm: is the late fee on the scheduled installment or on the overdue amount?** |
| 11 | Late fee on original vs overdue amount | **On the original scheduled installment total.** Documented, config-exposed rate, but the *base* is hard-coded to `principal_component + profit_component`. | ⚠️ BDR-10 |
| 12 | Collection case creation | On the overdue run, when installment 4 is first marked `overdue` and the contract has no open case → `CollectionCase(status=open)`. | ✅ |
| 13 | Collection activity | Manual (`POST /collections/cases/{id}/activities`). | ✅ |
| 14 | Customer pays remaining 40 | `POST .../payments {amount: 40}` → pays installment 4 principal to zero. | ✅ |
| 15 | Payment allocation | installment 4 → `principal_paid` full → status... | ⚠️ **see below** |
| 16 | Delinquency cured | `_update_installment_status`: a partial payment on an `overdue` installment **stays `overdue`**; only a fully-paying payment sets it to `paid`. The 40 payment fully pays it → `paid`. | ✅ |
| 17 | Collection case closure | `close_case_if_cleared` runs after every payment: if **no installment has status `overdue`**, the open case → `closed`. | ✅ |

**Scenario B verdict:** ✅ **partial payments are correctly supported** — the code never assumes payment == installment amount. Two things to confirm as policy:
- **BDR-10:** the late fee base is `2% × scheduled installment total`, not `2% × overdue amount`. This is deliberate and documented, but it is a policy decision that has not been signed off by Alghanim.
- The **case only closes when every overdue installment is fully cleared** — a partially-paid overdue installment keeps the case open. Correct, but worth confirming it matches the collections operating model.

---

## Scenario C — Customer stops paying / delinquency

Contract active; several months paid; customer stops.

| Stage | Exists? | Where | Notes |
|---|---|---|---|
| Installment due → missed | ⚠️ passive | — | Nothing detects a miss until `POST /jobs/assess-overdue` is manually run. |
| Grace period | ✅ | `late_fee_grace_period_days` | Fee only after `dpd > 10`. |
| DPD | ✅ (transient) | `overdue.py`, `reports.py`, `ecl.py` | Recomputed each run; never stored. **No contract-level DPD, no contract "days delinquent" field.** |
| Late fee | ✅ once | `overdue.py` | **One fee per overdue installment, forever.** A customer who misses installments 5, 6, 7 gets one fee per installment; a customer who misses installment 5 for 6 months gets **exactly one fee**. **No recurring / compounding late fee.** (BDR-10 — "repeatability" unbuilt.) |
| Overdue status | ✅ | `Installment.status = overdue` | Installment-level only. **Contract stays `active`** no matter how delinquent. There is no `suspended` / `delinquent` / `default` contract status. |
| Collection case | ✅ | auto-open | One open case per contract. |
| Communication activity | ✅ manual | `CollectionActivity` | call/sms/email/visit/other — **free text, nothing is actually sent** (no notification system). No contact-attempt counter, no outcome codes. |
| Promise to Pay | ✅ | `promise_to_pay` activity | `promised_amount` + `promised_date` required. |
| Promise broken | ✅ | `evaluate_overdue_promises` (on overdue run) | `promised_date < as_of` and payments-since < promised → `broken` + logged activity. |
| Escalation | ❌ **MISSING** | — | No `escalation_level`, no supervisor queue, no aging into legal. |
| Additional delinquency | ⚠️ | — | Each newly-overdue installment can open... nothing new (case already open). More late fees accrue (one per new installment). |
| Potential default | ❌ **MISSING** | — | **No "default" concept.** No default definition, no default flag, no default event. ECL Path B has a `default_dpd_threshold` (90, placeholder) that drives a *stage-3 review presumption* but there is no default *state*. |
| Potential settlement arrangement | ⚠️ partial | — | Early settlement exists, but there's no "settlement plan" / "arrangement" / restructure. |
| Write-off | ❌ **MISSING** | — | No `WriteOff` entity, no eligibility rule, no approval, no accounting event, no partial vs full. |
| Recovery after write-off | ❌ **MISSING** | — | — |

Verify list:
- **DPD** — ✅ computed, ❌ not stored.
- **Delinquency status** — ⚠️ installment-level `overdue` only.
- **Late fee** — ✅ once per installment, ❌ not recurring.
- **Collection case / activities / PtP / broken promise / escalation** — ✅ / ✅ / ✅ / ✅ / ❌.
- **Exposure** — ✅ includes overdue amounts (principal + profit + late fees across non-closed contracts).
- **Risk classification** — ❌ no re-grading on delinquency; `risk_score` is static.
- **ECL implications** — ✅ Path C moves the contract into a higher provision band as DPD rises; Path B raises the stage *if* corroborated by an open case / broken promise. **But ECL only re-runs when someone manually calls `POST /ecl/run`.**
- **Write-off readiness** — ❌ zero.

**Scenario C verdict:** the platform can **detect** delinquency, **open a case**, **track promises**, and **provision for it** (ECL Path C) — but it **cannot resolve** a permanent default. There is no escalation, no restructure, no write-off, no recovery, no default state, and no scheduler to drive any of it. **`default definition` and `write-off policy` are BUSINESS DECISIONS REQUIRED** (BDR-12, BDR-32); the *modules* are SYSTEM GAPS.

---

## Scenario D — Early settlement

Contract active; several installments paid; customer wants to settle the rest.

| Step | Behaviour | Correct? |
|---|---|---|
| Retrieve current contract state | `GET /contracts/{id}` + `GET /contracts/{id}/settlement-quote` | ✅ |
| Calculate remaining principal | `Σ installment.principal_outstanding` | ✅ |
| Calculate unearned profit | `contract.unearned_profit_balance` | ✅ |
| Apply configured rebate rule | `early_settlement_profit_rebate_pct` (default **0.0** — full profit still charged) OR a staff `?requested_rebate_pct` / `?requested_rebate_amount` | ✅ **configurable rule** (BDR-09 for the number) |
| Calculate final payoff | `outstanding_principal + outstanding_late_fees + profit_still_charged` | ✅ |
| Recalculate server-side | `/settle` **always** rebuilds the quote and rejects `amount != final_payoff_amount` → 422 | ✅ (strong safeguard) |
| Require payment confirmation | `external_reference` required; a `Payment(status=applied)` is created for the payoff | ✅ (but no gateway — staff-entered) |
| Deviation → maker-checker | if the effective rebate ≠ config default → `ApprovalRequest(contract.settlement_rebate)`; a **different** approver runs it | ✅ (BDR #7, confirmed) |
| Record settlement | `ContractClosure(reason=early_settlement, financial_adjustment=NULL)` | ✅ |
| Update receivable | `_close_out_schedule`: every installment → `paid`, `unearned_profit_balance = 0` **in place** | ⚠️ **overwrites rows** (ledger dual-writes the detail) |
| Recognise / adjust profit | ledger: `profit_recognized` (still-charged) + `profit_rebated` (waived) — **separately reconstructable** | ✅ (S-4 addressed via ledger) |
| Close contract | `active→closed` | ✅ |
| Accounting events | one `early_settlement` event, **amount 0.00** (the real money is on the settlement `Payment` + ledger) | ⚠️ the event stream alone doesn't show the payoff was collected |
| Audit trail | `contract.settled` audit event (+ `approval_request_id` on the approval path) | ✅ |

**Scenario D verdict:** ✅ **works**, with a **configurable rebate rule** and a **maker-checker gate on deviations**. The two soft spots: `_close_out_schedule` **overwrites** `installment.profit_paid` to the full component even though the cash collected was less (the ledger has the truth, but the installment rows lie — the profitability report already had to special-case this); and the `early_settlement` accounting event carries `0.00`, so a downstream GL that consumes only the event stream would not see the payoff.

---

## Scenario E — Payment + bank reconciliation

| Step | Exists? | Behaviour |
|---|---|---|
| Customer → Easy / payment channel | ❌ | No channel, no gateway. |
| Payment reference | ⚠️ | `external_reference` is the merchant-side idempotency key, supplied by the caller. |
| Alghanim / payment system | ❌ | — |
| Bank account / settlement | ❌ | — |
| Bank statement | ⚠️ | `BankStatementLine` — created manually (single POST) or bulk (`.xlsx` upload). |
| Reconciliation engine | ✅ | `reconciliation.py::run_matching` |
| Matching by reference | ✅ | Rule 1: `line.bank_reference ∈ {payment.external_reference, payment.gateway_reference}`; 1 hit + equal amount → match; 1 hit + wrong amount → `amount_mismatch` exception + payment flagged `exception`; >1 → `duplicate_candidate`. |
| Matching by amount / date | ✅ | Rule 2 (only if no ref hit): same amount + `abs(received_at.date − value_date) ≤ tolerance` (tolerance **0** = same day). |
| Exception queue | ✅ | `ReconciliationException` (`no_match` / `amount_mismatch` / `duplicate_candidate`), `open` / `resolved`. |
| Manual matching | ✅ | `POST /reconciliation/exceptions/{id}/request-match` → **maker-checker** (`reconciliation.manual_match`) → a **different** approver runs `apply_manual_match`. |
| Maker-checker for manual resolution | ✅ | Yes. |
| Duplicate detection | ✅ | `duplicate_candidate` when 2+ payments could match. |
| Reconciliation status | ✅ | `Payment.reconciliation_status` = `unreconciled` / `reconciled` / `exception`. |
| Payment allocation after reconciliation | ❌ **WRONG ORDER** | **Allocation happens at `record_payment` time — BEFORE reconciliation.** Reconciliation only *observes* (sets the status flag). It never triggers allocation. |
| Accounting event | ⚠️ | `payment_received` / `profit_recognized` fire at **record** time, not reconciliation time. `payment.reconciled` is an **audit event**, not an `AccountingEvent`. |
| Audit trail | ✅ | `reconciliation.bank_line_ingested`, `payment.reconciled`, `reconciliation.exception_opened`, `reconciliation.manual_matched`. |

**Scenario E verdict:** the matcher, exception queue, and maker-checker manual-match are **real and well-tested**. But:
- **There is no bank feed** — lines are hand-entered / uploaded.
- **The platform allocates a payment to installments (reducing the receivable, recognising profit, curing delinquency, potentially auto-closing the contract) the moment it is *recorded*, and reconciles against the bank only afterwards.** For a manual, staff-entered payment this is defensible. For a real gateway/Easy flow where "customer paid" ≠ "money settled", **this is an architectural risk**: an unsettled or reversed payment would already have moved the whole downstream state.

---

## 7. Pricing Engine Audit

**Location:** `app/services/pricing.py` — `build_plan` (pure) and `price_offer` / `resolve_profit_rate` (config-backed). Called from `offers.py::generate_offer`.

**Inputs:** `cash_price` (from `Product`), `down_payment` (from the offer request), `tenor_months`, `profit_rate` (looked up in `tenor_profit_rate_table` config by the tenor's string key; a tenor with no entry → `PricingError` → 422).

**The actual formula (verbatim from code):**

```
principal_financed      = cash_price − down_payment
total_profit            = round(principal_financed × profit_rate, 2)        # whole-of-term, flat
installment_sale_price  = cash_price + total_profit
amount_financed         = principal_financed + total_profit
                        = installment_sale_price − down_payment
```

**Schedule (per installment i of N):**
```
principal_component:  straight-line — principal_financed split into N equal parts
                      (cumulative rounding; installment N absorbs the residual so Σ = principal_financed exactly)
profit_component:     declining-balance — installment i carries profit weight (N − i + 1)
                      of total_weight = N(N+1)/2; cumulative rounding; installment N absorbs residual
                      so Σ = total_profit exactly
```

| Question | Answer |
|---|---|
| Where is pricing calculated? | `pricing.py::build_plan` (pure), driven by `resolve_profit_rate` (config). |
| What inputs are used? | cash_price, down_payment, tenor_months, profit_rate. |
| Can pricing vary by tenor? | **Yes** — `tenor_profit_rate_table` is keyed by tenor. |
| …by product? | **No.** One global rate table. |
| …by customer segment? | **No.** |
| …by campaign / promo? | **No.** |
| …by down payment? | **Indirectly** — `total_profit = principal_financed × rate` and `principal_financed = cash_price − down_payment`, so a bigger down payment lowers the profit. Whether that's the intended commercial behaviour is a **business decision**. |
| …by risk category? | **No** (and this is correct — risk must not change the price the customer pays; but there is also no risk-based pricing capability if the business wanted one). |
| Configurable without code changes? | **Yes** — `PUT /config/parameters/tenor_profit_rate_table` (JSON object body) through maker-checker. |
| Is the rate stored? | **Yes** — `InstallmentOffer.profit_rate` (frozen). |
| Is the resulting profit amount stored? | **Yes** — `InstallmentOffer.total_profit`, `SalesOrder.sale_price`, `InstallmentContract.total_profit`, per-installment `profit_component`. |
| Is the installment sale price stored? | **Yes** — `InstallmentOffer.installment_sale_price`, `SalesOrder.sale_price`. |
| Is the cash price preserved separately? | **Yes** — `Product.cash_price`, `InstallmentOffer.cash_price` (frozen). |
| Is the calculation reproducible / auditable? | **Partially.** The `schedule_preview` JSON is frozen on the offer and `profit_rate` is stored, so you can recompute. **But there is no `pricing_rule_id`, no rule version, no effective date, and the config snapshot is NOT stored on the offer** (it is on `AssessmentResult`, not on `InstallmentOffer`). You cannot say "this offer was priced under config generation X". |
| Can pricing rules have effective dates? | **No.** |
| Can different products have different pricing matrices? | **No.** |
| Deterministic? | **Yes** — pure function, unit-tested to reconcile exactly. |
| Is the price locked when the offer is accepted? | **Yes** — `accept_offer` copies `offer.schedule_preview` verbatim into `Installment` rows; `contract.total_profit` / `unearned_profit_balance` set from the offer. |
| Can the system prevent changing pricing after activation? | **Yes, by absence** — no endpoint modifies installment components or contract profit after creation. (`_close_out_schedule` zeroes balances at closure but does not re-price.) |

**Does the architecture assume `installment_sale_price == cash_price`?** **No.** `installment_sale_price = cash_price + total_profit` and `total_profit` is a real, separately-stored figure.

**Does it assume `profit = fixed % × tenor`?** **No** — profit is `rate(tenor) × principal_financed`, a whole-of-term rate looked up per tenor, not multiplied by tenor.

**Gaps (BDR-02 / BDR-11 / G-11):**
- Single dimension (tenor). No product / category / segment / campaign / risk pricing.
- No `PricingRule` entity, no effective dates, no rule versioning.
- Config snapshot not attached to the offer → limited price auditability.
- No absolute-sale-price option (only `% × financed principal`).
- The illustrative scenario (*300 cash → 380 @ 12mo, 450 @ 24mo*) is representable **only** by choosing rates so that `255 × rate ≈ 80` and `≈ 150` — and then **every** 300-cash product would get the same numbers.

---

## 8. Profit Amortization Audit

**Claim under test:** "Declining Balance — principal is constant each month, profit is higher initially and decreases over time."

**VERIFIED FROM CODE** (`pricing.py::build_plan`, lines 111–138; `test_pricing.py::test_profit_recognition_declines_over_time`, `test_schedule_reconciles_exactly`):

| Question | Answer |
|---|---|
| Is principal stored separately? | ✅ `Installment.principal_component`, `principal_paid`, `principal_outstanding` |
| Is profit stored separately? | ✅ `Installment.profit_component`, `profit_paid`, `profit_outstanding` |
| Is total profit stored? | ✅ `InstallmentContract.total_profit` |
| Is unearned profit tracked? | ✅ `InstallmentContract.unearned_profit_balance` (starts = `total_profit`, decremented by the profit portion of each payment) |
| Is earned / recognised profit tracked? | ⚠️ **Derived only** — `total_profit − unearned_profit_balance`, or `Σ installment.profit_paid`, or `Σ LedgerEntry(profit_recognized)`. **No dedicated stored field.** |
| Schedule stores principal component per installment? | ✅ |
| Schedule stores profit component per installment? | ✅ |
| Is outstanding profit tracked? | ✅ per installment (`profit_component − profit_paid`) |
| Is profit recognised according to the schedule? | ❌ **No — recognised cash-basis, on payment.** When a payment's allocation reaches an installment's profit bucket, that profit is recognised then. If a customer pays late, profit is recognised late; if they never pay, it's never recognised. The **schedule shape** is declining-balance; the **recognition timing** is "when the cash for that installment's profit arrives". |
| Is profit amortised? | ✅ across installments in the schedule (declining-balance split). |
| What amortization method is implemented? | **Declining-balance split** across the schedule: installment `i` of `N` carries profit weight `N − i + 1`. Principal is **straight-line** (equal per installment). Cumulative rounding; the final installment absorbs all residual rounding so the columns sum **exactly** to `principal_financed` and `total_profit`. |
| Straight-line? | Principal: yes. Profit: no. |
| Declining balance? | Profit: yes (front-loaded, never increases installment-to-installment). |
| Something else? | The *recognition method* (cash vs accrual, effective-rate) is **BDR-05 — "Finance/audit sign-off mandatory. Do not pick."** |

**Worked example** — cash 1200, down payment 300, 12 months @ 9% (from `test_pricing.py`):
```
principal_financed = 900.00
total_profit       = round(900 × 0.09, 2) = 81.00
installment_sale_price = 1200 + 81 = 1281.00
amount_financed    = 900 + 81 = 981.00

Installment 1:  principal 75.00,  profit 12.46,  total 87.46
Installment 2:  principal 75.00,  profit 11.42,  total 86.42     ← profit ↓
Installment 3:  principal 75.00,  profit 10.39,  total 85.39
...
Installment 12: principal 75.00,  profit  1.09,  total 76.09     ← smallest profit

Σ principal = 900.00  ==  principal_financed          ✅ (test asserts ==, not ≈)
Σ profit    =  81.00  ==  total_profit                ✅ (test asserts ==, not ≈)
Σ total     = 981.00  ==  amount_financed             ✅
```

**Invariants — all verified in `test_pricing.py`:**
- `Σ(installment principal) == contract principal_financed` ✅ exact
- `Σ(installment profit) == contract total_profit` ✅ exact
- `installment_sale_price == cash_price + total_profit` ✅
- profit never increases installment-to-installment ✅
- `profit[0] > profit[-1]` ✅

**Verdict:** the amortization *shape* is **implemented correctly and reconciles exactly**. The **recognition method** is cash-basis and is an open finance decision (BDR-05).

---

## 9. Unearned Profit Audit

| Figure | Represented? | How |
|---|---|---|
| Total contractual profit | ✅ stored | `InstallmentContract.total_profit` |
| Earned / recognised profit | ⚠️ derived, not stored | `total_profit − unearned_profit_balance` OR `Σ installment.profit_paid` OR `Σ LedgerEntry(profit_recognized)` |
| Unearned profit | ✅ stored | `InstallmentContract.unearned_profit_balance` |
| Outstanding principal | ✅ derived | `Σ Installment.principal_outstanding` (`build_receivable`) |
| Outstanding profit | ✅ derived | `Σ Installment.profit_outstanding` (`build_receivable`) |
| Total outstanding receivable | ✅ derived | `outstanding_principal + outstanding_profit` (late fees a **separate** line) |

**These are genuinely distinct in the model.** But there is a **latent consistency risk**:

- `unearned_profit_balance` is decremented **in place** by `payments.py` (`-= line.profit`) and **hard-set to 0 in place** by `_close_out_schedule` / `cancel_contract` / `return_contract`.
- `Σ profit_outstanding` is `Σ(profit_component − profit_paid)` — decremented by the same `line.profit` on payment, and hard-set to 0 by `_close_out_schedule` setting `profit_paid = profit_component`.
- **They are kept equal by two separate mutation paths, not by a single source of truth.** The `LedgerEntry` stream is the intended immutable single source — but **no read path uses it** (Phase 1). So today, if any future change updated one path and not the other, unearned-profit and outstanding-profit would silently diverge with nothing to catch it except `test_ledger.py`'s reconciliation assertions.
- For a **returned** contract, `_close_out_schedule` sets `profit_paid = profit_component` for every installment even though **no cash was collected** for the unpaid ones. `unearned_profit_balance` is separately set to 0. The profitability report had to add a special case (`_recognized_profit_at_return`, reading the **ledger** instead of `profit_paid`) precisely because `profit_paid` lies after a return. **This is a concrete example of "values overwritten instead of represented as auditable events."**

**Can the DB and accounting events represent these separately?**
- DB: contractual (`total_profit`) and unearned (`unearned_profit_balance`) — yes, as fields. Earned — only derived.
- Accounting events: `profit_recognized` events capture recognition on payment and on settlement (still-charged portion). `profit_rebated` is a **ledger** entry, not an accounting event. There is **no** `unearned_profit` event and no accrual/deferral event. The `early_settlement` event is `0.00`.

**Gap classification:** the split exists in the model; the **immutability / single-source-of-truth** is a **P0 technical gap** (S-4 — ledger read-cutover not done); a **dedicated stored "recognised profit" field** would remove the derive-3-ways ambiguity.

---

## 10. ECL Audit

**Status: D — PARTIALLY IMPLEMENTED (a real first slice; not a mock, not complete).**

> **Note:** `docs/enterprise-assessment.md` still lists ECL as class **G (none)** and `docs/business-rules-catalogue.md` does not cover it — both predate the ECL slice (commit `f0bd2b3`, 2026-09-06). This audit supersedes them for ECL.

### Where it lives

| Layer | File |
|---|---|
| Model | `app/models/ecl.py` — `ECLMethodology` enum, `ECLRun`, `ECLAssessment` |
| Migration | `alembic/versions/0012_ecl_provision.py` (adds `ecl_runs`, `ecl_assessments`; makes `accounting_events.contract_id` nullable for the portfolio-level event) |
| Engine | `app/services/ecl_engine.py` — `EclContext` → `EclProvider.assess()` → `EclOutcome`; 3 providers (`DpdBandedProvider`, `SimplifiedLifetimeProvider`, `ThreeStageProvider`); `get_provider(methodology)` dispatch |
| Orchestration | `app/services/ecl.py` — `initial_assessment` (day-one), `run_ecl` (portfolio), read models (`dashboard`, `portfolio`, `contract_detail`, `list_runs`) |
| API | `app/api/ecl.py` — `GET /ecl/dashboard`, `GET /ecl/assessments`, `GET /ecl/contracts/{id}`, `GET /ecl/runs`, `POST /ecl/run` |
| UI | `frontend/src/pages/EclProvisionPage.tsx` — nav group "Finance / Risk → ECL & Provision" |
| Config | `ecl_methodology`, `ecl_provision_pct_by_bucket`, `ecl_lifetime_loss_rate`, `ecl_sicr_dpd_threshold`, `ecl_default_dpd_threshold` |
| Tests | `tests/test_ecl.py` (9), `frontend/src/test/ecl.test.tsx` (6) |

### Entity / model

`ECLAssessment` (one per contract per `as_of_date`, unique constraint → upsert): `run_id` (nullable — null = day-one assessment), `contract_id`, `customer_id`, `as_of_date`, `methodology`, `ead`, `dpd`, `dpd_bucket`, `stage`, `stage_reason`, `pd`, `lgd`, `loss_rate`, `ecl_amount`, `provision_before`, `provision_movement`, `config_snapshot` (JSON), `created_at`.

`ECLRun` (portfolio recalculation header): `as_of_date`, `methodology`, `contracts_assessed`, `total_ead`, `total_ecl` (nullable), `total_provision_movement` (nullable), `accounting_event_id`, `created_by`, `created_at`.

### Inputs

| Input | Source |
|---|---|
| EAD | `build_receivable(contract).outstanding_receivable + outstanding_late_fees` — the same Receivable calc used everywhere. Per **contract**, not per customer. |
| DPD | max days-past-due over unpaid past-due installments as of `as_of` (same logic as reports). Not stored on the contract. |
| Corroborating signals (Path B) | open `CollectionCase` for the contract; `broken` promise-to-pay activity for the contract. |
| Config | `ecl_methodology`, band %s, lifetime rate, SICR / default DPD thresholds. |

### Calculation (3 paths, chosen by `ecl_methodology`)

| Path | `ecl_methodology` | Formula | State |
|---|---|---|---|
| **C — DPD-banded** | `dpd_banded` (**default**) | `ecl_amount = round(EAD × ecl_provision_pct_by_bucket[dpd_band], 2)`. Bands from `dpd_report_buckets` + `current`. Placeholder %s: current 0.5, 1-30 3, 31-60 15, 61-90 40, 91+ 75. | **Fully computed.** Not a formal IFRS 9 model. |
| **A — Simplified lifetime** | `simplified_lifetime` | `ecl_amount = round(EAD × ecl_lifetime_loss_rate, 2)` (0.10 placeholder), from day one, no staging. | **Fully computed.** |
| **B — IFRS 9 3-stage** | `three_stage` | Stage classification: DPD ≥ `default_dpd_threshold` (90) **+ a corroborating signal** → Stage 3; DPD ≥ `sicr_dpd_threshold` (30) **+ a signal** → Stage 2; DPD alone (no signal) → **stays Stage 1, presumption "rebutted"**; a non-DPD signal → Stage 2. **`pd = lgd = loss_rate = ecl_amount = NULL`** with the note `"n/a — no PD/LGD source configured"` — never fabricated, never shown as 0. | **Structure only.** |

### Persistence / accounting / reporting

- Every assessment persists EAD, DPD, band/stage, the formula inputs, and the **full config snapshot** (same "config snapshot per decision" principle as Credit Assessment).
- `provision_movement` per contract = `ecl_amount − prior-period ecl_amount`.
- Each `ECLRun` emits **exactly one** `ecl_provision_movement` `AccountingEvent` (portfolio-level, `contract_id = NULL`), `amount = (this run's total ECL) − (previous run's total ECL)` — so a same-day re-run posts ~0, not a double-count. Under Path B (no computable total) **no event is emitted** rather than posting a fabricated zero.
- Posting is via the same `POST /jobs/post-accounting-events` mock GL. **No debit/credit, no provision account code.**
- Reporting: dashboard tiles (Total EAD, ECL balance, provision balance, coverage %, contracts assessed, stage 1/2/3 exposure — "n/a" unless Path B), a filterable portfolio table with CSV/XLSX/PDF export, and a per-contract drill-down showing the exact inputs + config snapshot + history.

### Is customer pricing completely separated from ECL?

✅ **Yes, completely.** ECL is computed **after** activation from the receivable and DPD. Nothing in `ecl.py` / `ecl_engine.py` touches `InstallmentOffer`, `pricing.py`, or `InstallmentContract.total_profit`. The installment price the customer pays is frozen at offer acceptance and no ECL output feeds back into it. **This separation is correct and is a strength.**

### Relationship to delinquency / DPD / exposure

- DPD drives the provision band (Path C) and the stage-review presumption (Path B).
- EAD = the same per-contract receivable figure used by the Receivable view and exposure.
- **But:** ECL is **per contract**; there is no customer-level or portfolio-segment ECL rollup beyond the flat dashboard totals. Customer exposure aggregation (`exposure.py`) and ECL do not share a customer-level view.

### What's missing architecturally

| Gap | Class |
|---|---|
| Real PD / LGD / EAD-model source for Path B | SYSTEM GAP + BDR-14 (ECL ownership: in-platform vs external risk engine vs ERP-Finance) |
| Which methodology is the approved accounting policy | **BUSINESS DECISION REQUIRED** (yaml: "Which path is the approved accounting policy is a BUSINESS DECISION still open") |
| The provision band %s / lifetime loss rate / DPD thresholds | **BUSINESS DECISION REQUIRED** (all FICTIONAL PLACEHOLDER) |
| Macroeconomic / forward-looking overlay | SYSTEM GAP (IFRS 9 requires it for a real 3-stage model) |
| Scheduled monthly/period-end ECL close run | SYSTEM GAP (only on-demand `POST /ecl/run`) |
| Write-off / recovery integration (feeds Stage 3 / provision release) | SYSTEM GAP (modules don't exist) |
| Separate provision ledger / GL provision-account movement | SYSTEM GAP (provision == ECL; the movement event has no account mapping — BDR-31) |
| `initial_assessment` swallows **all** exceptions silently (`except Exception: return None`) | TECHNICAL — a config error at activation means no day-one assessment and nobody is told |
| Customer-level / cohort ECL aggregation | SYSTEM GAP |
| Coverage % / stage exposure are only fully meaningful when **every** contract is computable | KNOWN LIMITATION (mixed states show partial) |

### ECL classification

**B — Partially implemented.** Path C and Path A are real and computed; Path B is architecturally reserved with honest `null`s; the whole thing is config-switchable, persisted, snapshotted, and wired to the accounting boundary — but it depends on placeholder loss rates, has no PD/LGD source, no scheduler, and no macro overlay.

---

## 10.1 ECL & Provision Engine — implemented 2026-09-10 (supersedes the "Path B structure-only" state above)

The ECL module was built out from the first slice. This section is the delta; §10 above describes the pre-build state and remains accurate as history.

### What changed

| Area | Before (first slice, `f0bd2b3`) | After (this build) |
|---|---|---|
| Methodology | config **key** `ecl_methodology`, default `dpd_banded`; Path B = structure-only, PD/LGD/ECL `null` | **three_stage is primary and computed** (12-month ECL for Stage 1, lifetime ECL for Stages 2 & 3, `ECL = EAD × PD × LGD`); `simplified_lifetime` and `dpd_banded` retained as alternatives. Methodology now lives in the versioned `ecl_configurations` table. |
| Stage determination | DPD ladder + "corroborating signal" heuristic in code | **configurable Stage Determination Engine** (`ecl_stage.py`) — Stage-3 rules evaluated first, then SICR rules, else Stage 1. Rule types: `dpd_gte`, `flag` (open collections case / broken PtP / forbearance / credit-impaired), `pd_ratio_gte` (PD deterioration vs origination), `rating_notches_gte` (rating deterioration). Every rule list is config, not code. |
| Risk inputs | manual `Customer.risk_score` only | `risk_score → internal rating → risk segment → PD term structure / LGD model`, all from config (`ecl_risk.py`). Origination rating/PD stamped at day one. |
| Automated vs override | single mutable result | **`automated_*`, `override_*`, `final_*` stored separately** on `ECLAssessment`. The automated result is never mutated. |
| Manual overrides | none | **Stage Override** and **Parameter Override** (`ecl_override.py`), both MANDATORY maker-checker (`ACTION_ECL_STAGE_OVERRIDE`, `ACTION_ECL_PARAMETER_OVERRIDE`; `decided_by != requested_by` enforced). Effective window (from / to / review date), lifecycle `PENDING → APPROVED/ACTIVE → EXPIRED/CANCELLED/REJECTED`. An ACTIVE override survives future automatic runs. Expiry job: `POST /ecl/jobs/expire-overrides`. |
| Stage curing | none | configurable anti-flip-flop (`cure_rules`) — a downgrade below a contract's peak automated stage requires min qualifying payments + max DPD + min observation days; otherwise the higher stage is held with an explanatory `cure_note`. |
| Provision movement | `total_ecl − prior run total_ecl` | per-contract **Opening / Calculated ECL / Override adjustment / Closing** on every assessment; `movement_type ∈ {created, increased, released, unchanged, override_adjustment}`. |
| Run lifecycle | one-shot, emits event immediately | `DRAFT/CALCULATING → COMPLETED → POSTED`. A run is immutable. `POST /ecl/runs/{id}/post` (or `POST /ecl/run {post:true}`) emits the accounting events and locks provisions. A POSTED run cannot be re-posted. |
| Accounting events | one `ecl_provision_movement` per run | day-one `ecl_provision_created` per contract at activation; on run post: `ecl_provision_created/increased/released` per contract movement, `ecl_provision_override_adjustment` per override delta, plus one portfolio `ecl_provision_movement` roll-up. Still **no GL account codes** (BDR-31). |
| Config | 5 flat yaml keys | immutable **versioned** `ecl_configurations` table (`ecl_config.py`); a change activates a new version (maker-checker, `ACTION_ECL_CONFIG_UPDATE`); every `ECLAssessment`/`ECLRun` stamps `ecl_config_version` + `stage_rule_version` + `pd_model_version` + `lgd_model_version` + `calculation_version` for reproducibility. The 5 legacy keys stay seeded as reference only. |
| Explainability | `stage_reason` string | `stage_reason` **plus** `stage_triggers` (per-rule `{id, name, category, triggered, detail}`) — a user can always answer "why is this contract Stage 2/3?". Mandatory for Stage 2 and Stage 3. |
| UI | one page (tiles + run + portfolio + drill-down) | **ECL & Provision workstation** (KPI tiles, Stage 1/2/3 cards + bar chart, indicator strip, run/post panel, filterable + paginated portfolio with automated/override/final columns), **ECL assessment detail page** (`/ecl/contracts/:id` — summary, "why this stage?" checklist, automated·override·final matrix, provision-movement panel, override request forms, versions, history, accounting events, config snapshot), **ECL Model & Rules page** (`/ecl/config` — active config, version history, propose-a-change maker-checker). |
| Pricing separation | separated | **still completely separated** — nothing in the engine reads or writes `InstallmentOffer` / `pricing.py` / contract profit; the payment/allocation/late-fee/profit-recognition code was not touched. |

### Still open after this build (unchanged from §10)

| Gap | Classification |
|---|---|
| Real PD / LGD / rating-model source (every value in `ecl_configurations` v1 is a placeholder) | BDR-45/46 + SYSTEM GAP (model source) |
| Scheduled period-end ECL close run | SYSTEM GAP (still on-demand `POST /ecl/run`) |
| Discounting ECL to present value at the effective profit rate; marginal-PD term structure; probability-weighted macro scenarios | SYSTEM GAP — `calculation_version` is bumped when introduced |
| Separate provision GL ledger / account mapping per ECL event type | BDR-31 |
| Customer-level / cohort ECL aggregation | SYSTEM GAP |

Tests: `tests/test_ecl.py` (17), `tests/test_ecl_overrides.py` (11) — new contract → Stage 1; default → Stage 3; SICR via qualitative signal at DPD 0; rating deterioration → Stage 2; provision increase / release; run immutability + history; curing; config versioning + maker-checker; stage & parameter overrides; maker ≠ checker; rejection; expiry; active override survives a run; audit trail; per-contract + roll-up accounting events. Frontend: `frontend/src/test/ecl.test.tsx` (9).

---

## 11. Inventory / Fulfilment Audit

**Can the platform prevent selling the same physical unit twice?** **Only partially — and there is a real over-sell hole.**

| Product model field | Present? |
|---|---|
| SKU | ❌ |
| stock_quantity | ✅ |
| available_quantity | ✅ (computed `= stock − reserved`) |
| reserved_quantity | ⚠️ field exists, **always 0, never written** |
| sold_quantity | ❌ |
| serial / IMEI | ❌ |
| warehouse / branch dimension | ❌ |

**Lifecycle:**
- Offer **generation**: `generate_offer` → 422 if `product.available_quantity <= 0`.
- Offer **acceptance** (`accept_offer`): `product.stock_quantity = (stock_quantity or 0) − 1` — **no floor check, no `SELECT ... FOR UPDATE`, no availability re-check.**
- **Delivery**: bare status flip — **no stock effect** (the deduction already happened at acceptance).
- **Cancellation** (pre-delivery): `_release_stock` → `stock_quantity += 1`.
- **Return** (post-delivery): `_release_stock` → `stock_quantity += 1` — regardless of the returned item's condition.

**The over-sell hole:** the stock gate is checked at **generation**, not **acceptance**. If a product has `stock_quantity = 1`:
1. Application A → offer A generated (passes: `available = 1 > 0`).
2. Application B → offer B generated (passes: `available = 1 > 0` — offer A hasn't been accepted yet).
3. Offer A accepted → `stock_quantity = 0`.
4. Offer B accepted → `stock_quantity = −1`. **No check stops this.**

There is also **no transaction isolation / row lock**, so even two *simultaneous* acceptances of offers generated when `stock = 1` would both succeed.

**Can the architecture support proper inventory?** The fields for a naive count exist, but there is **no `Reservation`, no `Delivery`, no `InventoryItem` with a branch/warehouse, no serial capture, no `InventoryProvider` boundary**. `reserved_quantity` is dead. (G-08 / BDR-18 — "reservation & deduction point" is an open business decision; the *reservation model* is a system gap.)

---

## 12. Accounting / GL Audit

**It is: B — an internal accounting-event / journal-ready integration boundary. It is NOT a General Ledger.**

**Per financial event:**

| Event | `event_type` | Emitted from | `amount` | Debit/credit? | Account codes? |
|---|---|---|---|---|---|
| Contract activation (= sale recognition) | `contract_activated` | `confirm_delivery` | `sales_order.sale_price` (cash + profit) | ❌ | ❌ |
| Down payment | `down_payment_received` | `confirm_delivery` | `sales_order.down_payment_amount` | ❌ | ❌ |
| Installment payment | `payment_received` | `record_payment` | full `payment.amount` | ❌ | ❌ |
| Profit recognition | `profit_recognized` | `record_payment` (and via ledger at settlement) | the profit portion this allocation recognised | ❌ | ❌ |
| Late fee | `late_fee_charged` | `assess_overdue` | fee amount | ❌ | ❌ |
| Late fee waiver | `late_fee_waived` | approval executor | waived amount | ❌ | ❌ |
| Early settlement | `early_settlement` | `settle_contract` | **0.00** (money is on the settlement `Payment` + ledger) | ❌ | ❌ |
| Cancellation | `cancellation` | `cancel_contract` | signed `financial_adjustment` (= +refund) | ❌ | ❌ |
| Return | `return` | `return_contract` | signed `net_adjustment` | ❌ | ❌ |
| Normal maturity closure | `contract_closed` | `close_if_fully_repaid` | 0.00 | ❌ | ❌ |
| ECL provision movement | `ecl_provision_movement` | `run_ecl` | portfolio ECL delta (nullable) | ❌ | ❌ |

**Each `AccountingEvent` carries:** `event_type`, `event_reference` (idempotency key), `contract_id` (nullable), `customer_id`, one **signed `amount`**, `currency` (`"KWD"` hard-coded), `event_date`, `accounting_status` (`pending`/`posted`/`failed`), `external_gl_reference`, `error_message`, `retry_count`.

**Idempotency / reprocessing:** unique `event_reference` → firing a hook twice is a no-op. `post_pending` re-attempts `failed` events; `posted` events are skipped. Posting is **on-demand** (`POST /jobs/post-accounting-events`), not scheduled.

**Sale / down payment / activation / receivable / installment payment / principal collection / profit recognition / late fee / waiver / settlement / cancellation / return** — all have an event.
**Write-off / recovery / refund-as-distinct-event / interest accrual / VAT / provision-account movement with a code** — **no event or no mapping.**

**Missing / thin:**
- **No chart-of-accounts, no debit/credit split** (BDR-31 — explicitly deferred to a real `GlProvider`).
- **Receivable creation** is implied by `contract_activated`, not a distinct event.
- **Principal collection** is not a distinct event (`payment_received` is the whole payment; `profit_recognized` splits out the profit; principal is `payment_received − profit_recognized − late_fee`, i.e. derived).
- **The down payment** emits an event but has **no `Payment` row** behind it — so the event references money that was never recorded as a transaction.
- **Early settlement** event = 0.00 → a GL fed only by the event stream would not see the payoff.

---

## 13. Collections Audit

**Model:** `CollectionCase` (open/closed, one open per contract — partial unique index), `CollectionActivity` (call/sms/email/visit/promise_to_pay/other), `promise_status` (pending/kept/broken).

**Automatic lifecycle:**
- **Open:** `assess_overdue` marks an installment `overdue` on a contract with no open case → `open_case_if_needed` opens one (`opened_reason` = the first overdue installment).
- **Close:** after every payment allocation, if no installment is in `overdue` status → the open case → `closed`.

**Promise-to-pay (S-9 fix — now real):**
- `pending` on creation (requires `promised_amount` + `promised_date`).
- → `kept` by `evaluate_promises_after_payment` (payments since the promise ≥ promised amount), run on every payment.
- → `broken` by `evaluate_overdue_promises` (promised_date passed, amount not received), run on every `assess_overdue`.
- Staff manual override with a required reason (`POST /collections/activities/{id}/promise-status`).
- Both transitions log a `CollectionActivity` for the audit trail.
- Broken promise feeds ECL Path B as a corroborating signal.

**What's there:** case open/close, activity log, promise lifecycle, RBAC (`collections_officer` / `credit_manager` / `admin` view; `collections_officer` / `admin` log), owning-customer read.

**What's missing:**
- **Escalation** — no `escalation_level`, no supervisor queue, no aging-into-legal.
- **Restructure / reschedule** — none (G-16 / BDR-11).
- **Legal referral** — none.
- **Write-off / recovery** — none (G-17 / BDR-12 / BDR-13).
- **DPD action bands** — `dpd_report_buckets` is display-only; there is **no policy** on when DPD triggers a call / SMS / visit / legal (BDR-32).
- **Notifications** — no SMS/email is actually sent; activities are free text.
- **Contact-attempt tracking / outcome codes / next-action date** — none beyond a free-text `notes`.
- **Contract-level delinquency state** — the contract stays `active`; there is no `delinquent` / `suspended` / `default` status and no stored DPD.

---

## 14. Write-off / Recovery Audit

**H — MISSING ENTIRELY.**

No `WriteOff` model, no `Recovery` model, no service, no endpoint, no UI, no accounting event, no `written_off` contract status, no `write_off` closure reason. There is no write-off request, no approval flow, no partial-vs-full distinction, no financial-impact calculation, no post-write-off recovery allocation, no collection-case behaviour on write-off.

- **Write-off policy** (DPD threshold, approval levels, partial vs full) → **BUSINESS DECISION REQUIRED** (BDR-12).
- **Recovery policy** (allocation of post-write-off receipts, incentives, legal) → **BUSINESS DECISION REQUIRED** (BDR-13).
- **The `WriteOff` / `Recovery` modules** → **SYSTEM GAP** (G-17).

---

## 15. KYC / Documents / T&C / Consent Audit

**All H — MISSING.**

| Item | State |
|---|---|
| Identity verification | ❌ `national_id` is a free-text `String(50)`, unique. No verification. |
| Credit bureau | ❌ `risk_score` is a nullable int, set by hand. |
| Income verification | ❌ `monthly_income` / `existing_monthly_obligations` self-reported on `CustomerProfile`. |
| Document upload | ❌ no `CustomerDocument` entity, no file storage. |
| Document status / verification status | ❌ |
| Mobile / OTP verification | ❌ |
| Customer consent | ❌ no `ConsentRecord`. |
| T&C version | ❌ no `TermsVersion` entity, no content hash, no effective dates. |
| T&C acceptance timestamp / accepted_by / evidence | ❌ **The offer-acceptance flow has NO T&C step.** `accept_offer` takes only `down_payment_confirmed` + `down_payment_reference`. |
| Promissory note | ❌ |

Nothing proves who the customer is, that their income was checked, or which contract terms they agreed to. (S-12 / G-09 / G-10 / BDR-16 / BDR-17 — all systems gaps; the *document requirement set* is a business/compliance decision.)

---

## 16. Online vs Branch Audit

**`ApplicationChannel` enum (`online` / `branch`) is stored on `CreditApplication`. There is NO behavioural difference.**

| Aspect | online | branch |
|---|---|---|
| Origination endpoint | `POST /applications` (role `customer` / `sales_employee` / `admin`) | same |
| Credit assessment | identical (`assess_application`) | identical |
| KYC requirement | none | none |
| Pricing | identical | identical |
| Limits / routing | identical | identical |
| Fraud / device signals | ❌ none | ❌ none |
| Actor link | `created_by` free-text string (`"system"` default) | `created_by` free-text string |
| Branch entity | ❌ no `Branch` model, no `origination_branch_id` | — |
| Customer self-registration | ❌ customers are created by `sales_employee` / `admin`; `customers.user_id` link is set manually | n/a |

The **only** consumer of `channel` is `reports.py::contracts_by_channel` (a reporting breakdown).

**Verdict:** architecturally this **is** "two origination channels, one credit process" — which matches the target architecture — but the channel is currently cosmetic. There is no branch entity, no online fraud/device capture, and no online customer self-service. (G-25 / G-35 — light BDRs.)

---

## 17. Customer / Contract / Exposure Audit

**Entity separation — verified correct:**

```
Customer (identity)  1─┬─  CustomerProfile (financial/KYC picture)
                       └─  CreditApplication (request + its assessment)  ─1─  InstallmentOffer (priced proposal, frozen)
                                                                              │
                                                                              1
                                                                              │
                                            SalesOrder (what was sold, for how much)  ─1─  InstallmentContract (how it's financed)
                                                                                              │
                                                                    ┌─────────────────────────┼──────────────────────┐
                                                             PaymentSchedule ─ N Installment   Payment ─ N PaymentAllocation
                                                                                              │
                                                                                    ContractClosure (0 or 1)
                                                                                    LateFeeCharge (N, per installment)
                                                                                    CollectionCase (N over time, ≤1 open)
                                                                                    ECLAssessment (N over time)
```

`Customer ≠ Application ≠ Sales Order ≠ Installment Contract ≠ Payment ≠ Receivable ≠ Installment` — **all distinct, correctly FK'd, never merged.** `Receivable` is a computed view (`ReceivableView`), not a table.

**Multiple active contracts per customer:** structurally supported. Each `InstallmentContract` independently maintains its product (via `SalesOrder`), schedule, installments, payments, closure, status, and delinquency. There is **no `max_active_contracts` cap** (G-28).

**Exposure aggregation (`exposure.py::compute_exposure`):**

| Question | Answer |
|---|---|
| How is exposure calculated? | `Σ (outstanding_principal + outstanding_profit + outstanding_late_fees)` across the customer's contracts, via `build_receivable` per contract (reused, not re-implemented). |
| All active contracts included? | Yes — **all non-`closed` contracts**, including `created` (pre-delivery). |
| Overdue amounts included? | Yes — outstanding principal/profit of overdue installments + outstanding late fees. |
| Pending applications included? | Not in the aggregate; but the assessment's Rule 4 **adds** `new_financed_estimate` (financed principal of the request being assessed) on top of `current_exposure`. |
| Cancelled contracts excluded? | Yes (cancelled → `closed`). |
| Closed contracts excluded? | Yes. |
| Configurable limits? | `max_customer_exposure_kwd` (placeholder 8000). `exposure_aggregation_level` (only `company_wide`; any other value **raises**). No per-count cap. |

**Known basis mismatch (BDR-08):** Rule 4 compares `current_exposure` (principal + profit + late fees) against the limit but **adds `new_financed_estimate`** (financed principal only, pre-profit). The two addends are on slightly different bases — consistent with the code, flagged for policy review.

---

## 18. UI Coverage

**Screens that exist** (`frontend/src/pages/`, routed in `App.tsx`, nav in `Shell.tsx`):

| Screen | Route | Roles (nav) | Backend | What it does | Notable gaps |
|---|---|---|---|---|---|
| Login | `/login` | all | `/auth/login`, `/auth/me` | JWT sign-in, token in `localStorage` | no refresh/remember-me |
| Dashboard (5-tab) | `/` | finance_officer/credit_manager/admin see tabs; others see a stub | `/reports/summary/*` | read-only Executive / Operations / Portfolio / Collections / Credit-Risk tiles + charts | no ECL tab here (separate page) |
| New Application | `/applications/new` | all | `POST /applications`, `/submit` | create + submit, shows `AssessmentPanel` (decision, DBR 4dp, triggered rules) | manual customer/product id or search-select; no T&C |
| Review Queue + detail | `/review`, `/review/:id` | credit_officer/credit_manager/admin | `GET /applications?status=referred`, `POST .../review` | list referred, decide (approve/reject/return) | no maker-checker on the decision |
| Offer | `/applications/:id/offer`, `/offers/:id` | all | `POST .../offer`, `GET /offers/:id`, `POST /offers/:id/accept` | generate priced offer, `ScheduleTable`, accept + DP reference | DP is free text; no T&C checkbox |
| Contract detail | `/contracts/:id` | all (owner-checked reads) | `GET /contracts/:id`, `/receivable`, `/applications/:id`; `POST .../confirm-delivery`, `/payments`, `/settlement-quote`, `/settle`, `/cancel`, `/return` | sales order, origination, delivery, receivable, record payment, closure (settle/cancel/return), installments table, **Print/PDF view** | no payment history list, no allocation breakdown per past payment, no late-fee waiver UI here |
| Customer Directory | `/customers` | directory roles | `GET /customers?search=&status=` | search, status filter, CSV export | full-list on load |
| Customer detail | `/customers/:id` | directory roles | `GET /customers/:id`, `/exposure`, `/reports/contracts?customer_id=` | Personal/Employment/Financial, Exposure card, Contract history, **Print/PDF view** | no edit customer, no KYC |
| Product Directory | `/products` | directory roles | `GET /products?search=` | search, CSV export | read-only |
| Create Customer / Create Product | `/customers/new`, `/products/new` | sales/admin ; credit-desk/admin | `POST /customers` / `POST /products` | grouped form sections | product create not audited server-side |
| Contracts Directory | `/contracts` | directory roles | `GET /reports/contracts?contract_id=&customer_id=` | lookup by ref code / customer | not a full paginated browse |
| Collections list + case detail | `/collections`, `/collections/:id` | collections_officer/credit_manager/admin | `/collections/cases*`, `POST .../activities`; admin-only "Run overdue assessment" | list/filter, case detail, log activity (conditional PtP fields) | no promise-status override UI, no escalation |
| Reconciliation | `/reconciliation` | finance_officer/admin | `/reconciliation/*`, `/bank-lines/upload` | status counts, add bank line, .xlsx upload, run matching, exceptions table, request-match | |
| Approvals | `/approvals` | finance_officer/credit_manager/admin | `/approvals?status=pending`, `/approvals/:id/approve\|reject` | pending list, approve/reject, own-request buttons disabled | |
| ECL & Provision | `/ecl` | finance_officer/credit_manager/admin | `/ecl/*` | tiles, run panel, portfolio table + filters + export, per-contract drill-down | run is on-demand |
| Snapshot | `/snapshot` | finance_officer/credit_manager/admin | recon status + accounting-event counts + approvals + open cases | counts only, "not a KPI platform" | |
| Reports Center | `/reports` | finance_officer/credit_manager/admin | `/reports/*` | 6 categories, sub-reports, CSV/XLSX/PDF, Aging drill-down | no charts, no scheduled/saved reports |
| Inventory | `/inventory` | finance_officer/admin | `GET /products`, `POST .../stock-adjustment`, `/audit/events` | table + per-row adjust + recent adjustments | no reservation view |
| Configuration | `/config` | admin | `/config/parameters`, `PUT .../{key}` (→202); Appearance sub-tab (browser-local theme) | list + edit → pending-approval message | |
| Audit Log | `/audit` | admin/credit_manager | `/audit/events` | filterable event list | |

**UI gaps (do NOT build yet):**
- No **Applications** list/browse screen (only the `referred` review queue).
- No **Payments** screen — no payment history, no allocation breakdown per payment, no per-payment reconciliation view.
- No **Accounting Events** screen (`GET /accounting/events` exists; only surfaced as counts on Snapshot).
- No **Settlements / Closures** portfolio screen.
- No **KYC / documents / T&C** screens (no backend).
- No **Write-off / Recovery** screens (no backend).
- No **Notifications** screen (no backend).
- No **User Management** screen (`POST /auth/register` is admin-only, API-only).
- No **customer self-service portal**.
- No **late-fee waiver** request UI (only `POST /late-fees/{id}/request-waiver` API).
- No **promise-status override** UI (`POST /collections/activities/{id}/promise-status` API only).
- No **portfolio-level exposure** screen (per-customer only).
- Global cross-entity search and notifications are deferred (`docs/frontend-redesign-audit.md`).

---

## 19. Financial Integrity / Accounting Invariants

| Invariant | Holds? | Evidence |
|---|---|---|
| `Total Contractual Receivable == Principal financed + Total Profit` | ✅ | `amount_financed = principal_financed + total_profit`; `test_payments_flow.py` (900 + 81 = 981) |
| `Σ(Installment Principal) == Contract principal_financed` | ✅ exact | `test_pricing.py::test_schedule_reconciles_exactly` (`==`, not `≈`) |
| `Σ(Installment Profit) == Contract total_profit` | ✅ exact | same |
| `installment_sale_price == cash_price + total_profit` | ✅ | `test_pricing.py` |
| `Outstanding Receivable == Outstanding Principal + Outstanding Profit` | ✅ by construction | `receivable.py::build_receivable` |
| `Outstanding Late Fees` kept **separate** from receivable | ✅ | `build_receivable` returns it as its own line; never merged |
| `Payment.amount == allocated_amount + unallocated_amount` | ✅ per payment | `allocation.py`; overpayment rejected so `unallocated` is always 0 in practice |
| `Σ ledger(profit_recognized) + Σ ledger(profit_rebated) == Σ Installment.profit_paid` (settled contract) | ✅ | `test_ledger.py` |
| Bank-reconciled ≠ allocated (no silent state jump) | ✅ | reconciliation only sets `reconciliation_status`; allocation is a separate, earlier step |
| `unearned_profit_balance == Σ profit_outstanding` (in normal flow) | ⚠️ | kept equal by **two separate mutation paths**, not one source of truth; **diverges after a return** (`profit_paid` overwritten to full component) |
| Contract-level `Σ payments.amount == Σ allocations.total + refunds` | ⚠️ **not asserted anywhere** | no test or calculation checks this end-to-end |
| Immutable financial history (no overwrite) | ❌ | `_close_out_schedule`, `cancel_contract`, `return_contract`, and every payment **overwrite** `profit_paid` / `principal_paid` / `unearned_profit_balance` in place; the `LedgerEntry` shadow is **not read** |

**Places where values are overwritten instead of represented as auditable events:**
1. `payments.py::record_payment` — `installment.profit_paid += line.profit`, `installment.principal_paid += line.principal`, `contract.unearned_profit_balance -= line.profit` (ledger dual-writes, but the row is the authoritative figure).
2. `closure.py::_close_out_schedule` (settlement, return) — every installment's `principal_paid` / `profit_paid` set to the full component; `unearned_profit_balance = 0`. **After a return, `profit_paid` shows profit that was never collected** (the profitability report reads the ledger to work around this).
3. `closure.py::cancel_contract` / `return_contract` — `unearned_profit_balance = 0` in place.
4. `overdue.py` / `payments.py::_apply_late_fee` — `LateFeeCharge.amount_paid` and `status` mutated in place.
5. Reconciliation — `Payment.reconciliation_status` flipped in place (acceptable — it's a status, and there's an audit event).

**The fix already scoped (S-4, P0-1):** cut the read paths over to the `LedgerEntry` ledger so the ledger is authoritative and the rows become a cache. **Not done** — Phase 1 is dual-write only.

---

## 20. Status Model Audit

| Entity | Statuses in code | Missing / notes |
|---|---|---|
| **Application** | `draft`, `submitted`, `under_assessment`, `approved`, `rejected`, `referred` | `submitted` / `under_assessment` are transient (set + advanced in one request). No `expired`, `cancelled`, `conditionally_approved`. |
| **Offer** | `presented`, `accepted`, `expired` | No `generated` (created as `presented`), no `declined` (a customer declining isn't modelled — only expiry or acceptance). |
| **Payment** | `applied`, `overpaid` (unreachable) + reconciliation: `unreconciled`, `reconciled`, `exception` | **No `initiated`, `pending`, `received`, `confirmed`, `failed`, `reversed`.** A payment is born `applied` + `unreconciled`. |
| **Contract** | `created`, `active`, `closed` | **No `pending_activation` (`created` serves it), no `overdue` (installment-level only), no `suspended`, no `settled`/`cancelled`/`returned`/`written_off`** — all collapsed into `closed` + `ContractClosure.reason`. |
| **Collection case** | `open`, `closed` | No `in_progress`, `promise_to_pay`, `escalated`, `resolved`. The PtP state lives on the **activity**, not the case. |
| **Closure reason** | `normal`, `early_settlement`, `cancellation`, `return_` | No `write_off`. |
| **Late fee** | `assessed`, `waived`, `paid` | No `reversed`. |
| **Installment** | `pending`, `partially_paid`, `overdue`, `paid` | (`overdue` + `partially_paid` are mutually exclusive — a partially-paid overdue installment stays `overdue`.) |
| **Approval request** | `pending`, `approved`, `rejected` | — |
| **Reconciliation exception** | `open`, `resolved` | — |
| **Accounting event** | `pending`, `posted`, `failed` | — |
| **ECL** | `ECLMethodology`: `dpd_banded` / `simplified_lifetime` / `three_stage`; stage 1/2/3 (Path B) | no explicit "assessment status" |

---

## 21. Business Decisions Required vs System Gaps vs Technical Improvements

### A. BUSINESS DECISION REQUIRED (do NOT invent)

| # | Decision | Current placeholder | BDR ref |
|---|---|---|---|
| 1 | **Pricing matrix** (tenor → rate, by category/segment/campaign) and **whether the price varies by down payment** | `tenor_profit_rate_table` (fictitious) | BDR-02/03 |
| 2 | **Profit recognition method** (cash vs accrual, effective-rate) — *distinct from the amortization shape, which is fixed* | cash-basis | **BDR-05 — "Finance/audit sign-off mandatory"** |
| 3 | **Late fee policy** — base (scheduled installment vs overdue amount vs principal), fixed vs %, grace, cap value, **repeatability/compounding** | `2%` of scheduled total, grace 10d, once-only, cap wired but 0 | BDR-10 |
| 4 | **Is `late_fee_rate = 0.02` actually confirmed?** The yaml says "CONFIRMED"; the audit brief says do not treat it as policy | 0.02 | — (mismatch to resolve) |
| 5 | **Early settlement rebate formula** | `early_settlement_profit_rebate_pct = 0.0` + staff override + maker-checker on deviation | BDR-09 |
| 6 | **Down-payment refund policy** on cancellation / return; **restocking fee**; **DP forfeiture** | 1.0 / 0.0 flat | BDR-19 |
| 7 | **Return financial treatment** — profit reversal itemisation, condition/serial check | single signed number | BDR-19 |
| 8 | **Product ownership transfer point** (delivery vs full payment) — drives return/repossession/accounting | `ownership_transfers_on_delivery = true`, echoed only | BDR-01 |
| 9 | **ECL methodology** (Path A/B/C) that is the approved accounting policy; the **loss rates / band %s / DPD thresholds** | `dpd_banded` + fictitious %s | BDR-14 + yaml |
| 10 | **ECL ownership** — in-platform calc vs external risk engine vs ERP-Finance | in-platform Path C | BDR-14 |
| 11 | **Exposure aggregation level** (company-wide vs per BU/brand/category) and **the threshold** + whether to cap contract *count* | company-wide, 8000, no count cap | BDR-07/08 |
| 12 | **Default definition** (DPD, other triggers) | none — Path B has a 90-DPD *review presumption*, no default *state* | BDR-32 |
| 13 | **Write-off policy** (DPD threshold, approval levels, partial vs full) | none | BDR-12 |
| 14 | **Recovery policy** (post-write-off receipt allocation) | none | BDR-13 |
| 15 | **DPD action thresholds** (when each band triggers call / SMS / visit / legal / write-off recommendation) | `dpd_report_buckets` is display-only | BDR-32 |
| 16 | **Restructuring / rescheduling policy** (eligibility, re-pricing, fee) | none | BDR-11 |
| 17 | **Chart-of-accounts / debit-credit mapping** per `event_type`; real-time vs batched posting | single signed amount, on-demand | BDR-31 |
| 18 | **KYC document requirement set** (per channel / amount / segment) | none | BDR-17 |
| 19 | **T&C content ownership / promissory-note requirements** | none | BDR-16 |
| 20 | **Manual-verification SLA + who may override an auto-decision + maker-checker on approvals by amount band** | any single reviewer, no bands | BDR-25 |
| 21 | **Affordability obligation basis** + assumed DP for the initial estimate + failed-recheck action (block vs referral vs warn) | rate-table estimate, `block` | BDR-22 |
| 22 | **Job cadence** for every scheduled process | all manual | BDR-28 |
| 23 | **Inventory reservation & deduction point** (offer vs contract vs delivery) | at contract creation | BDR-18 |
| 24 | **Token strategy** (refresh, session length, revocation, storage) | 30-min HS256, `localStorage` | BDR-30 |
| 25 | **Tax / VAT applicability** in Kuwait | none | BDR-42 |

#### ECL & Provision engine — new BDRs (all values in `ecl_configurations` v1 are placeholders)

| # | Decision | Current placeholder | BDR ref |
|---|---|---|---|
| 26 | **`risk_score` → internal rating grade** band boundaries (A–E) | `750+/700-749/650-699/600-649/<600` | **BDR-43** |
| 27 | **Rating grade → risk segment** mapping | A/B→prime, C→standard, D/E→subprime, unrated→unrated | **BDR-44** |
| 28 | **PD term structure** per segment (12-month PD, lifetime PD, elevated Stage-3 lifetime PD) | prime 2%/12%/45% … subprime 9%/30%/70% | **BDR-45** |
| 29 | **LGD model** per segment (base LGD, elevated Stage-3 LGD) | prime 40%/50% … subprime 55%/65% | **BDR-46** |
| 30 | **SICR trigger set + thresholds** (DPD, PD-deterioration ratio, rating-notch count, qualitative flags) | DPD ≥ 30, PD ratio ≥ 2.0×, ≥ 2 notches, open case, forbearance | **BDR-47** |
| 31 | **Stage-3 / default trigger set + thresholds** | DPD ≥ 90, broken PtP, credit-impaired flag | **BDR-48** |
| 32 | **Stage curing rules** (min qualifying payments, max DPD, min observation days for 3→2 and 2→1) | 3 / 0 / 90 and 3 / 0 / 30 | **BDR-49** |
| 33 | **Manual override policy** — which parameters are overridable, default validity, review cadence, evidence requirement | stage/pd/lgd/ead/rating, 90-day validity, evidence required | **BDR-50** |
| 34 | **Portfolio methodology designation** — which portfolios use `three_stage` vs `simplified_lifetime` | all `three_stage` | **BDR-51** |
| 35 | **ECL calculation model** — simple `EAD × PD × LGD` vs PV-discounted at the effective profit rate with a marginal-PD term structure and macro scenarios | `EAD × PD × LGD` (`calculation_version` 1.0) | **BDR-52** |

### B. SYSTEM GAP (implementation, not a policy call)

| # | Gap | Priority |
|---|---|---|
| 1 | **Payment gateway / channel** — no `PaymentProvider`, no adapter, no mock, no `Payment.channel`, no `PaymentInitiation` | P0 (for real money) |
| 2 | **Down-payment collection** — never a real `Payment`; no reconciliation | P0 |
| 3 | **Reconcile-before-allocate** — the receivable/profit/delinquency all move at *record* time, before bank confirmation | P0 (architectural) |
| 4 | **Immutable-ledger read cutover** — ledger is write-only; rows still overwritten (S-4) | P0 |
| 5 | **Inventory over-sell beyond the last unit** — gate at generation not acceptance; no lock; no reservation model | P1 |
| 6 | **Write-off / Recovery modules** | P1 |
| 7 | **KYC** — `KycProfile`, `CustomerDocument`, `MobileVerification` | P1 |
| 8 | **T&C / consent** — `TermsVersion`, `ConsentRecord` (hash + acceptance context) | P1 (legal) |
| 9 | **Scheduled job runner** — DPD, late fees, broken-promise, maturity closure, offer/quote expiry, ECL close, accounting posting | P1 |
| 10 | **Collections depth** — escalation levels, restructure, legal referral, DPD action bands | P1 |
| 11 | **Contract-level delinquency state** + stored DPD | P1 |
| 12 | **`PricingRule` entity** — dimensions, effective dates, version; `pricing_rule_id` on the offer | P1/P2 |
| 13 | **`Delivery` / `Reservation` / `InventoryItem` (branch/warehouse) / serial capture** | P1 |
| 14 | **`Refund` entity** — method, status, reconciliation (return refund is just a number) | P1 |
| 15 | **Persisted `SettlementQuote`** (id, version, valid-until enforced) | P1 |
| 16 | **Notifications** — outbox + provider interface | P2 |
| 17 | **Decision metadata** — `rule_set_version`, `decision_source`, `risk_grade`, full input snapshot on `AssessmentResult` | P1 |
| 18 | **Effective-dated config** | P1 |
| 19 | **Standardised API errors** (RFC 9457) | P1 |
| 20 | **Generic `Idempotency-Key`** on all financial POSTs | P1 |
| 21 | **Customer self-service portal** | P2/Future |
| 22 | **Provider registry / interfaces** (`KycProvider`, `CreditBureauProvider`, `IncomeProvider`, `FraudProvider`, `PaymentProvider`, `GlProvider`, `InventoryProvider`) | P1/P2 |
| 23 | **ECL:** scheduled close run, macro overlay, PD/LGD source, customer/cohort aggregation, provision-account movement | P1/P2 |
| 24 | **Portfolio-level exposure UI** | P2 |
| 25 | **`created_by_user_id` FK** on `CreditApplication` (currently free text) | P1 |

### C. TECHNICAL IMPROVEMENT

| # | Improvement |
|---|---|
| 1 | External-service resilience (timeout / retry+backoff / circuit breaker / `IntegrationLog`) — needed once providers are real |
| 2 | Security hardening — CORS allow-list, `/auth/login` rate limit, structured logging + correlation id, `/health` DB check, JWT `jti` + refresh/revocation, secrets manager, HTTPS at the edge |
| 3 | `ecl.initial_assessment` swallows **all** exceptions silently — narrow it and log |
| 4 | `create_product` writes no `AuditEvent` — add one for consistency |
| 5 | Observability — no metrics, no request tracing |
| 6 | API consistency — mix of `Response` passthrough and `response_model`; error `detail` is `str` in some routes, `list` in others |
| 7 | `docs/enterprise-assessment.md` and `docs/business-rules-catalogue.md` are stale re: ECL and the 5 gap fixes — refresh |
| 8 | No DB-level check constraints on money columns (all app-enforced) |
| 9 | `alembic` chain: migration 0008's *downgrade* fails on Postgres (`ALTER COLUMN ... TYPE VARCHAR(20)` with existing longer data) — cosmetic but a real `downgrade base` blocker |

---

## 22. Critical Risks

| # | Risk | Impact | Likelihood in a live operation |
|---|---|---|---|
| R1 | **Payment recorded ≠ money received.** Allocation, profit recognition, delinquency cure, and auto-close all happen at record time. An unsettled/reversed gateway payment leaves the contract in a false "paid" state; reconciliation only flags it afterwards and doesn't roll anything back. | Financial misstatement; false collections closure; profit recognised on money not received | High once a gateway is added; Low while every payment is a staff-verified manual entry |
| R2 | **Down payment is fiction.** The contract's receivable is created net of a down payment that has no `Payment`, no reconciliation, no proof. | Receivable overstated by the DP if the DP was never actually taken | High |
| R3 | **Inventory over-sell.** N offers for a 1-unit product can all be accepted → negative stock, N contracts for 1 laptop. | Cannot fulfil; customer disputes; stock ledger wrong | Medium (needs concurrent/rapid offers on scarce stock) |
| R4 | **Financial rows are overwritten, not evented.** After a return, `installment.profit_paid` shows profit never collected; `unearned_profit_balance` is zeroed with no compensating entry. The ledger has the truth but nothing reads it. | Any report or integration that reads the rows (not the ledger) is wrong for closed/returned contracts | Certain for returns/settlements |
| R5 | **No write-off / recovery / default state.** A permanently defaulted customer has no clean lifecycle end; ECL keeps provisioning against a dead receivable with no way to write it off. | Portfolio can't be cleaned; provisions can't be released; loss never recognised | Certain over time |
| R6 | **No KYC / T&C / consent.** No proof of identity, income, or agreed terms. | Legal / regulatory exposure; unenforceable contracts | Certain |
| R7 | **No scheduler.** Delinquency, late fees, broken promises, maturity closure, ECL close all wait for a human to click a button. A contract that pays to one cent short of zero stays `active` forever. | Operational drift; stale portfolio; missed fees; unclosed contracts | Certain |
| R8 | **ECL depends entirely on placeholder loss rates** and re-runs only on demand. Coverage % / stage exposure meaningful only when *every* contract is computable. | Provision numbers are illustrative, not defensible; period-end close is manual | Certain until Finance signs off the methodology + rates |
| R9 | **Accounting-event stream has no debit/credit / account codes**, and `early_settlement` / `contract_closed` events carry `0.00`. | A GL consuming only the event stream cannot post correct journals or see settlement cash | Certain until BDR-31 |
| R10 | **Security posture** — `localStorage` JWT, no refresh/revocation, no CORS allow-list, no rate limit, no correlation ids. | Not production-safe on the open internet | Certain if exposed |

---

## 23. Priority Classification

**P0 — financial correctness / data integrity / critical lifecycle**

| Item | Type |
|---|---|
| Immutable-ledger **read cutover** (stop reading overwritten rows; ledger becomes authoritative) — S-4 | SYSTEM GAP |
| **Reconcile-before-allocate** (or an explicit "reported vs confirmed" payment state so the receivable doesn't move on unconfirmed money) | SYSTEM GAP (architectural) |
| **Down payment as a real `Payment`** (recorded + reconcilable) | SYSTEM GAP |
| **Payment channel + `PaymentInitiation` + provider interface** (even mock) so a real gateway is a drop-in | SYSTEM GAP |
| **Confirm the pricing methodology + rates** before extending pricing | BUSINESS DECISION (BDR-02/03) |
| **Confirm the profit recognition method** | BUSINESS DECISION (BDR-05) |
| **Confirm the late-fee base + repeatability** | BUSINESS DECISION (BDR-10) |
| **Confirm the ECL methodology + loss rates** | BUSINESS DECISION (BDR-14 + yaml) |
| **Inventory over-sell fix** (availability re-check + lock at acceptance) | SYSTEM GAP (data integrity) |

**P1 — core business capability for a realistic production workflow**

Write-off / Recovery · KYC (`KycProfile` / `CustomerDocument` / OTP) · T&C / consent · Scheduled job runner · Collections depth (escalation, restructure, DPD action bands, contract-level delinquency + stored DPD) · `PricingRule` entity + effective dates · `Refund` entity · Persisted `SettlementQuote` · `Delivery` / `Reservation` / serial capture · Decision metadata (`rule_set_version`, `risk_grade`) · Effective-dated config · `created_by_user_id` FK · Security hardening (CORS / rate-limit / logging) · Standardised API errors · Generic `Idempotency-Key` · ECL scheduled close + PD/LGD source path.

**P2 — important operational capability**

Notifications outbox + provider · Provider registry / interfaces (all mocks) · External-service resilience suite · Portfolio-level exposure UI · Applications list / Payments / Accounting-Events / User-Management screens · Fraud / device signals (online) · ECL macro overlay + cohort aggregation.

**P3 — enhancement / future**

Customer self-service portal · Tax/VAT-ready `Invoice` fields (design only) · Charts / scheduled / saved reports · Global cross-entity search · BNPL Path B boundary (keep clean, do not build).

---

## 24. End-to-End Capability Matrix

Legend: ✅ present · ⚠️ partial/thin · ❌ missing · 🔒 by design deferred

| Business Capability | Backend | Database | API | Frontend | Tests | End-to-End | Status | Gap |
|---|---|---|---|---|---|---|---|---|
| Customer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | no KYC; no edit-customer UI |
| Product | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | **D** | no SKU/serial/warehouse; create not audited |
| Application | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | `requested_amount` not tied to `cash_price` |
| Online application | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | channel cosmetic; no self-registration/fraud signals |
| Branch application | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | no `Branch` entity; `created_by` is free text |
| Credit assessment | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | thresholds are placeholders |
| Risk scoring | ⚠️ | ⚠️ | ⚠️ | ✅ | ✅ | ⚠️ | **I/D** | banding of a manual int; no bureau, no grade scale |
| Exposure aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | company-wide only; number placeholder; basis mismatch (BDR-08) |
| Approval (auto) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | — |
| Referred → manual review | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | no maker-checker / amount bands (BDR-25) |
| Rejection | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | — |
| Pricing | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | 1 dimension (tenor); no `PricingRule`, no effective dates |
| Tenor pricing | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | rate only, not absolute price; global table |
| Down payment | ⚠️ | ⚠️ | ⚠️ | ✅ | ⚠️ | ❌ | **E/F** | never a real `Payment`; not reconciled |
| Profit (total) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | methodology BDR-03/04 |
| Profit amortization (shape) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | declining-balance split, exact reconciliation |
| Profit recognition (timing) | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ | ⚠️ | **D/BDR-05** | cash-basis; not configurable; `profit_paid` overwritten at closure |
| Unearned profit | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **D** | two mutation paths, no single source of truth; lies after a return |
| Installment schedule | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | due dates anchored to acceptance wall-clock |
| Sales Order | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | — |
| Contract | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | 3 statuses only; no delinquency state |
| Delivery | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | **D** | bare status flip; no `Delivery` entity, no serial |
| Inventory | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ❌ | **I** | over-sell beyond last unit; `reserved_quantity` dead |
| Payment | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | **A** | no channel, no initiated/confirmed states, no gateway; no payment-history UI |
| Partial payment | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | — |
| Payment allocation | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | **A** | waterfall hard-coded (BDR-10 to confirm as universal) |
| DPD | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | **D** | computed, never stored; no contract-level DPD; manual trigger |
| Late fee | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | no recurring/compounding; base is BDR-10; "confirmed 2%" disputed |
| Collections | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | no escalation/restructure/legal/DPD-bands |
| Promise to Pay | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | override UI missing (API only) |
| Early settlement | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | `_close_out_schedule` overwrites rows; event amount 0.00 |
| Cancellation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | not maker-checker gated; flat refund % |
| Return | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | **D/I** | single signed number; no `Refund`, no condition/serial, no inventory-return record |
| Write-off | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **H** | whole module |
| Recovery | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **H** | whole module |
| Bank reconciliation | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **D** | no bank feed; allocates-then-reconciles; observes only |
| Payment gateway | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **H** | not even a mock class |
| Accounting events | ✅ | ✅ | ✅ | ⚠️ | ✅ | ⚠️ | **B** | single signed amount; no CoA; no dedicated screen |
| GL integration boundary | ⚠️ | ⚠️ | ✅ | ❌ | ✅ | 🔒 | **B/E** | `MockGlProvider` always ok; BDR-31 |
| ECL | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **D** | Path C computed / Path B structure-only; placeholder rates; no scheduler; no PD/LGD source |
| KYC | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **H** | — |
| Documents | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **H** | — |
| T&C / consent | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **H** | offer accept has no T&C step |
| Notifications | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **H** | nothing is sent |
| Reporting | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A/B** | no scheduled/saved; DPD buckets display-only |
| Customer portal | ⚠️ | ✅ | ⚠️ | ❌ | ⚠️ | ❌ | **H** | backend owner-checks exist; no UI, no registration |
| Audit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | product creation not audited |
| Maker-checker | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **A** | 4 action types; not on manual review or cancel/return |
| Scheduled jobs | ❌ | ❌ | ⚠️ (manual `/jobs/*`) | ❌ | ❌ | ❌ | **H** | — |

---

## 25. Final Readiness Assessment

### "If we stopped adding features today and tried to operate this platform as a realistic retail installment business, what could we execute end-to-end, and where would it break?"

**In plain business terms:**

**What would work, start to finish, today (with staff doing the manual steps):**

1. **Onboard a customer and take an application** — a sales employee (branch) or the customer (online) can create the application; the system captures name, ID number, self-reported income and existing obligations, and a manually-entered risk score.
2. **Get an automated credit decision in seconds** — the four rules (minimum income, debt-burden ratio, risk band, total customer exposure across all their contracts) run synchronously and return approved / referred / rejected with a full reason list and a snapshot of every threshold used.
3. **Send a borderline case to a credit officer** and have them approve/reject/return-for-more-info — with the manual decision recorded alongside the automated one.
4. **Generate a priced installment offer** — cash price + a tenor-based profit markup = installment sale price; a real amortization schedule where principal is level and profit is front-loaded (declining-balance) and the columns sum **exactly** to the totals. The offer is frozen.
5. **Accept the offer** — a Sales Order and an Installment Contract are created from the frozen offer; one unit of stock is deducted; the down-payment reference is noted (**but the money is not actually verified**).
6. **Confirm delivery** — the contract goes live and the receivable is now on the books; a "sale" and "down payment" accounting event are queued; a day-one ECL provision is booked.
7. **Take installment payments** — including **partial payments and payments that span more than one installment**; each payment is allocated oldest-installment-first, Late-Fee→Profit→Principal, the receivable drops, profit is recognised, and every movement is mirrored to an immutable ledger. Idempotent — re-submitting the same reference is safe.
8. **Run a manual overdue sweep** — mark installments overdue, charge a 2%-of-installment late fee once past the grace period, open a collections case, mark a customer's promise-to-pay kept or broken.
9. **Work a collections case** — log calls/SMS/visits (as records — nothing is actually sent), track promises.
10. **Waive a late fee** — through a two-person maker-checker approval.
11. **Settle a contract early** — with a server-recomputed payoff, a configurable profit rebate, and a **mandatory second approver** whenever the rebate deviates from the default.
12. **Cancel before delivery / return after delivery** — with a configured refund percentage and one signed net adjustment.
13. **Auto-close a contract** the moment its last payment lands it exactly on zero.
14. **Reconcile payments against a hand-entered or uploaded bank statement** — auto-match by reference or amount+date, queue exceptions, resolve them through maker-checker.
15. **Provision the portfolio for expected credit loss** — on demand, using a DPD-band provision model (with placeholder loss rates), and post one portfolio provision-movement to the accounting boundary.
16. **Run management reports** — contract lists, profitability (contractual / recognised / unearned, reconciling), a 5-tab executive dashboard, an aging report, all exportable to CSV / Excel / PDF.
17. **Change any business-rule number at runtime** through a two-person approval, with the old value captured in the audit log.
18. **Audit everything** — every state change writes an audit event; every financial action writes an accounting event and a ledger entry.

**Where the operation would break:**

1. **You cannot take money.** There is no payment channel or gateway. Every payment — including the down payment — has to be typed in by a staff member. The down payment in particular is never recorded as a real transaction, so **your receivable is overstated by every down payment you didn't actually collect** unless staff are perfectly disciplined about the manual step.
2. **A payment reduces the customer's balance the instant a staff member records it — before the money is confirmed in your bank account.** If you plug in a real gateway, a customer whose payment fails or reverses will still show as having paid: their delinquency cured, their profit recognised, possibly their contract auto-closed. Reconciliation catches the mismatch *afterwards* and rolls nothing back.
3. **You cannot prove who your customers are or what they agreed to.** No identity verification, no income verification, no document capture, no terms-and-conditions acceptance record. Every contract is legally thin.
4. **You cannot write off a bad debt.** A customer who defaults permanently stays on your books as an `active` contract forever (or you dress it up as a "return"). The ECL engine keeps provisioning against the dead receivable with no mechanism to write it off, recognise the loss, or release the provision on recovery. There is not even a "default" status.
5. **Nothing runs on a schedule.** Overdue detection, late fees, broken-promise detection, contract maturity, offer/quote expiry, ECL period-end — all wait for someone to click a button. A contract that pays to one cent short of zero is `active` forever with no maturity job to close it.
6. **Your stock can be over-sold.** The "is there a unit available" check happens when the offer is *generated*, not when it's *accepted*. Two offers written against your last laptop can both be accepted.
7. **Your accounting feed is not GL-ready.** Each event is a single signed number with no debit/credit and no account codes; the early-settlement and maturity-closure events carry zero. Finance cannot post journals from this without the chart-of-accounts mapping decision (BDR-31), and the mock ERP always says "posted".
8. **Your financial records get overwritten.** When a contract is settled or returned, the installment rows are rewritten to look fully paid even where no cash was collected. The immutable ledger has the correct history but **nothing in the platform reads it yet** — every report and figure still comes from the mutable rows.
9. **Your provision numbers are illustrative.** The ECL loss rates are explicitly fictional placeholders, the methodology hasn't been chosen by Finance, there's no PD/LGD model, and the calculation only runs when someone triggers it.
10. **Collections stops at "log a call and track a promise."** No escalation, no restructure, no legal referral, no DPD-driven action policy, and no message is actually sent to anyone.

**Bottom line:** the platform is a **correct, well-tested engine for the *arithmetic* of a retail installment sale** — pricing, scheduling, allocation, amortization, settlement, late fees, provisioning — with strong entity separation, RBAC, audit, maker-checker, and idempotency. It is **not yet an operational lending system**: it cannot collect money safely, cannot verify a customer, cannot end a bad loan, and cannot run itself.

---

## 26. TOP 10 — what to fix / add next

Ordered by "what unblocks operating the business". **None of these should be implemented until the audit is reviewed and the flagged business decisions are made.**

### 1. Payment: separate "recorded" from "confirmed" — reconcile before the receivable moves

- **Business reason:** the moment you accept a real gateway payment, "customer paid" must not equal "money is ours". Recognising profit and curing delinquency on unconfirmed money is a financial-misstatement risk.
- **Current status:** allocation, profit recognition, delinquency cure and auto-close all fire at `record_payment` time; reconciliation is a passive after-the-fact status flag (R1, S-5).
- **Exact gap:** no `PaymentInitiation` / "reported vs confirmed" state; `run_matching` doesn't trigger allocation; allocation is unconditional at record time.
- **Dependencies:** ties to #2 (gateway) and #3 (down payment); reuses the existing reconciliation engine and ledger.
- **Priority:** **P0.**
- **Business decision first?** **Yes** — do you allocate on "reported" and reverse on failure, or hold allocation until "confirmed"? (T+N settlement timing, BDR-26.)

### 2. Payment channel + gateway provider interface (mock first)

- **Business reason:** you cannot operate without taking money; a clean provider boundary means the real Easy/gateway integration is a drop-in, not a rebuild.
- **Current status:** no `Payment.channel`, no `PaymentProvider`, no adapter, no mock — G-05.
- **Exact gap:** the whole initiation→callback→settlement path; webhook signature verification; duplicate-callback guard.
- **Dependencies:** #1 (state model); G-27 (resilience) later.
- **Priority:** **P0** for real money.
- **Business decision first?** **Yes** — which provider(s); BDR-26 matching rules; BDR-30 webhook security.

### 3. Down payment as a real, reconcilable `Payment`

- **Business reason:** today the receivable is created net of a down payment that has no transaction record — your books are wrong by every uncollected DP.
- **Current status:** `down_payment_reference` free text on the offer; `down_payment_received` accounting event references money with no `Payment` behind it (R2, G-05).
- **Exact gap:** create a `Payment` (channel = down_payment) at acceptance/delivery; feed it into reconciliation and the ledger.
- **Dependencies:** #1, #2.
- **Priority:** **P0.**
- **Business decision first?** No (mechanism) — but the DP collection *point* (at acceptance vs at delivery) is worth confirming.

### 4. Immutable-ledger read cutover (make the ledger authoritative)

- **Business reason:** every report, GL feed, and integration currently reads mutable rows that get overwritten at closure — returned/settled contracts show profit that was never collected (R4, S-4).
- **Current status:** `LedgerEntry` is a tested, complete write-only dual-write shadow; no read path uses it.
- **Exact gap:** re-point `build_receivable`, the profitability report, exposure, and ECL EAD to derive from `LedgerEntry`; stop trusting `installment.profit_paid` / `unearned_profit_balance` as source of truth; keep the rows as a cache.
- **Dependencies:** the dual-write is already proven by `test_ledger.py` — this is a controlled read-swap.
- **Priority:** **P0** (data integrity).
- **Business decision first?** No — this is a correctness fix.

### 5. Confirm the pricing methodology, profit-recognition method, late-fee policy, and ECL methodology/rates

- **Business reason:** four foundational financial numbers are placeholders (BDR-02/03, BDR-05, BDR-10, BDR-14). Building further on them risks re-work.
- **Current status:** pricing = flat `rate(tenor) × financed principal`; recognition = cash-basis; late fee = 2% of scheduled installment total, once-only; ECL = DPD-band with fictional loss rates.
- **Exact gap:** decisions, not code — plus resolve the yaml's "CONFIRMED" claim on `late_fee_rate = 0.02` against the brief's instruction not to treat it as policy.
- **Dependencies:** blocks a `PricingRule` entity (#8), a configurable recognition run, late-fee repeatability, and any defensible provision number.
- **Priority:** **P0** (decision) / P1 (resulting code).
- **Business decision first?** **Yes — all four. Finance / audit sign-off is mandatory for BDR-05.**

### 6. Write-off + Recovery module

- **Business reason:** you need a way to end a bad loan, recognise the loss, release the provision, and handle post-write-off receipts.
- **Current status:** missing entirely (R5, G-17). No `written_off` status, no accounting event.
- **Exact gap:** `WriteOff` (eligibility, reason, maker-checker, principal/profit/fees written off, `AccountingEvent`), `Recovery` (post-write-off receipt allocation), contract status `written_off`, ECL Stage-3 / provision-release hook.
- **Dependencies:** reuses maker-checker + accounting boundary + ledger; needs a "default" definition first.
- **Priority:** **P1.**
- **Business decision first?** **Yes** — write-off policy (DPD threshold, approval levels, partial vs full) BDR-12; recovery policy BDR-13; default definition BDR-32.

### 7. KYC + T&C / consent

- **Business reason:** legal enforceability and regulatory compliance; you cannot prove identity, income, or agreed terms.
- **Current status:** missing entirely (R6, G-09, G-10, S-12).
- **Exact gap:** `KycProfile` (status, verified_by/at), `CustomerDocument` (type, metadata, verification_status), `MobileVerification`/OTP, configurable document-requirement list; `TermsVersion` (version, content hash, effective_from), `ConsentRecord` (customer, terms_version, offer/contract ref, accepted_at, channel, evidence); a T&C-acceptance step gating `accept_offer`.
- **Dependencies:** provider interfaces (KYC/income) later; document storage.
- **Priority:** **P1** (legal).
- **Business decision first?** **Yes** — required document set (BDR-17), T&C ownership + promissory-note requirements (BDR-16).

### 8. Scheduled job runner

- **Business reason:** the portfolio drifts without automation — overdue, late fees, broken promises, maturity closure, offer/quote expiry, ECL close, accounting posting all need to run on a cadence.
- **Current status:** everything is a manual `POST /jobs/*` or `POST /ecl/run` (R7, G-24).
- **Exact gap:** one idempotent job runner (APScheduler or external cron hitting internal endpoints); each job auditable + failure-aware; a maturity-closure job so a contract that pays short of zero still closes.
- **Dependencies:** the job *services* mostly exist (`assess_overdue`, `run_ecl`, `post_pending`) — this wraps them; add offer/quote-expiry and maturity jobs.
- **Priority:** **P1.**
- **Business decision first?** **Yes** — job cadence (BDR-28).

### 9. Inventory: availability re-check + lock at acceptance; reservation model

- **Business reason:** you can currently sell the same last unit multiple times (R3).
- **Current status:** stock gate at offer *generation*; `accept_offer` deducts with no floor check, no lock; `reserved_quantity` is dead (G-08, BDR-18).
- **Exact gap:** re-check `available_quantity > 0` **and** take a row lock (`SELECT ... FOR UPDATE`) inside `accept_offer`; a `Reservation` on offer generation with expiry; later a `Delivery` entity with serial capture and a branch/warehouse `InventoryItem`.
- **Dependencies:** none for the immediate fix; the full domain needs the WMS boundary decision.
- **Priority:** **P1** (the immediate over-sell fix is arguably P0).
- **Business decision first?** For the over-sell fix, no. For the full model — reservation & deduction point (BDR-18).

### 10. `PricingRule` entity + `pricing_rule_id` + effective dates + config snapshot on the offer

- **Business reason:** pricing has one dimension (tenor) and no versioning; you cannot price by category/segment/campaign, cannot change rates with an effective date, and cannot fully audit which rules priced a given offer.
- **Current status:** single global `tenor_profit_rate_table`; `profit_rate` frozen on the offer but no rule id, no config snapshot on the offer (G-11, BDR-02/11).
- **Exact gap:** a `PricingRule` entity (dimensions, effective_from, version, status) backing the config table; `InstallmentOffer` stores `pricing_rule_id` + the frozen numbers (keep freezing); attach the config snapshot to the offer.
- **Dependencies:** #5 (methodology must be confirmed first — don't build the entity around a placeholder model).
- **Priority:** **P1/P2.**
- **Business decision first?** **Yes** — the actual pricing matrix and methodology (BDR-02/03/04).

---

*End of audit. No code was changed. Awaiting review and instructions on which items to proceed with.*
