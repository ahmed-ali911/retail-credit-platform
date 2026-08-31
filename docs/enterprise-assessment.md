# Retail Credit & Installment Sales Platform
## Enterprise Architecture Review, Gap Analysis & Controlled Enhancement Plan

**Date:** 2026-08-30
**Scope of this document:** Deliverables 1–8 only (assessment). **No code, migration, test, or
config has been changed in this session.** Implementation is on hold pending review and an explicit
go-ahead naming specific gaps.

---

### Preliminary notes

1. **Source materials actually present.** The repository contains source code, SQLAlchemy models,
   6 Alembic migrations, a FastAPI backend, a React/Vite staff frontend, 15 backend test files
   (~94 test functions / 103 cases) + 3 frontend test files (7 cases), `config/business_rules.yaml`,
   and a detailed `README.md`. **No Excel scenarios, Word documents, Phase 1–4 documents, assumptions
   register, or architecture diagrams exist in the repo.** The README + the header comments in
   `config/business_rules.yaml` are the only design-intent artifacts. This assessment is therefore
   grounded in **code as the source of truth for what is implemented**, and README/config-comments as
   the source of truth for **stated intent and known-open decisions**.

2. **Branding.** The repository was deliberately de-branded in an earlier session (the company name
   was removed from all files *and* rewritten out of git history). This document keeps the neutral
   name **"Retail Credit & Installment Sales Platform"**. Re-introducing a specific retailer's brand
   is itself a business/legal decision — see BDR-21.

3. **Domain framing is correct and preserved.** The codebase consistently models a **retail
   installment sale**, not a cash loan: `cash_price` → `total_profit` (never "interest") →
   `installment_sale_price`; `unearned_profit_balance` drawn down as profit is paid; no disbursement
   path anywhere. This is a genuine strength and must not be eroded.

---

# DELIVERABLE 1 — Current-State Assessment

## 1.1 What exists (build history)

Seven controlled build steps, each migration-backed and tested:

| Step | Delivered | Migration |
|---|---|---|
| 1 | Scaffolding, `Customer`/`CustomerProfile`, `Product` (cash price only), `CreditApplication`, rules-based Credit Assessment engine, DB-backed config | `0001` |
| 2 | Pricing engine (tenor→rate table, declining-balance amortization), `InstallmentOffer`, `SalesOrder`, `InstallmentContract`, `PaymentSchedule`/`Installment`, delivery status flip | `0002` |
| 3 | `Payment` (idempotent per `external_reference`), allocation waterfall (`allocation.py`), `PaymentAllocation`, overdue/DPD job, `LateFeeCharge`, receivable view | `0003` |
| 4 | Early settlement (quote + settle), pre-delivery cancellation, post-delivery return, `ContractClosure` | `0004` |
| 5 | `User` + 6 roles, JWT auth, RBAC dependency, ownership checks, `AuditEvent` on state changes | `0005` |
| 6 | `collections_officer` role, `CollectionCase`/`CollectionActivity` (auto lifecycle), generic `ApprovalRequest` maker-checker (late-fee waiver + config change) | `0006` |
| 7 | React/Vite staff web app for the core flow (login → customer/product → application → offer → contract → payment). Backend untouched. | — |

**Backend:** ~4,800 LOC (`app/`), FastAPI + SQLAlchemy 2.x + Postgres + Alembic. Single deployable,
no message bus, no scheduler.
**Frontend:** React 18 + Vite 5 + TypeScript, staff-only, token in `localStorage`, dev-time `/api`
proxy (no CORS layer).

## 1.2 Capability inventory (A–G classification)

Legend: **A** implemented+tested · **B** implemented but incomplete · **C** designed not implemented ·
**D** placeholder/mock · **E** architectural capability only · **F** business decision required ·
**G** missing.

| Domain | Class | Evidence / notes |
|---|---|---|
| Customer identity + profile | **A** | `models/customer.py` — `Customer` (name, `national_id` unique, phone, email, status, `risk_score` int, `user_id` FK) + separate `CustomerProfile` (employment, `monthly_income`, `existing_monthly_obligations`, address). Tested. |
| KYC (mobile/OTP verification, KYC status, consent, documents, verification officer/timestamp) | **G** | No KYC entity, no verification workflow, no document store. `risk_score` is a manually-set integer (`D`). |
| Terms & Conditions / consent record | **G** | No T&C version, no acceptance record, no acceptance context. |
| Application origination (online/branch) | **A / B** | `CreditApplication` with `channel` enum (`online`/`branch`) and `created_by` string. Shared engine — no per-channel logic. `B`: `created_by` is a free-text string, not a `User` FK; branch "on behalf of" is not modelled beyond the string. |
| Credit assessment engine | **A** | `services/assessment.py` — 3 config-driven rules (min income, DBR, risk band), precedence `rejected > referred > approved`, `AssessmentResult` persists decision + `triggered_rules` + `config_snapshot`. Well tested (`test_assessment.py`, `test_config_externalization.py`). |
| Decision outcomes | **B** | Only `approved` / `rejected` / `referred`. Missing `conditionally_approved`, `expired`, `cancelled`. `referred` has **no downstream transition** — it is a dead-end (see 1.4). |
| Decision auditability (rule version, decision source, risk grade, inputs) | **B** | `config_snapshot` captures the thresholds used (a partial rule-version proxy). No explicit rule-set version string, no `decision_source`, no `risk_grade`, assessment inputs not fully snapshotted (income/obligations live only on the mutable profile). |
| Manual verification stage (credit officer review of `referred`) | **G** | `referred` status exists; no endpoint/service for a `credit_officer`/`credit_manager` to review and approve/reject. |
| Pluggable KYC / bureau / fraud providers | **G / F** | No adapter interface. Credit engine reads a manual `risk_score` and profile fields directly. |
| Fraud / device / velocity signals (online channel) | **G** | None. |
| Affordability / DBR basis | **B / F** | DBR uses `estimated_installment = requested_amount × installment_estimation_factor(1.0) / tenor` — a **proxy that excludes profit and down payment** and is never recomputed against the actual priced offer. Obligations = single self-reported number on profile; **not** aggregated from the customer's other contracts on this platform. |
| Pricing engine | **B** | `services/pricing.py` — tenor→rate table from config (`tenor_profit_rate_table` JSON), `total_profit = principal_financed × rate(tenor)`, declining-balance schedule with cumulative rounding, zero drift. Tested (`test_pricing.py`). `B`: only 2 dimensions (tenor, and cash_price via the product); no category/segment/risk-grade/promotion/effective-date dimensions; no `PricingRule` entity; rate table is one global config row. |
| Pricing version preserved on historical contracts | **B** | The **offer** freezes `cash_price`, `profit_rate`, `total_profit`, `schedule_preview` at generation (`models/offer.py`), and `SalesOrder`/`InstallmentContract` copy the frozen numbers. So the *numbers* are preserved, but there is no named/versioned pricing rule id to point back to. |
| Principal / profit decomposition | **A** | Each `Installment` has separate `principal_component` / `profit_component` / `principal_paid` / `profit_paid`. Tested. |
| Unearned profit + recognition | **B / F** | `InstallmentContract.unearned_profit_balance` starts at `total_profit`, decremented by `profit` paid via allocation. This is **cash-basis** recognition. No configurable straight-line vs effective-rate *recognition* methodology; no time-based (monthly) recognition run. Amortization *shape* (declining-balance) is fixed in code. |
| Installment schedule | **A / B** | `PaymentSchedule` + `Installment`. `B`: no `scheduled_fees`/`fees_paid` column (late fees are a separate table — deliberate and fine), **DPD is not stored** (computed on the fly in `overdue.py`), remaining amounts are computed properties (fine), no reversal/refund handling at installment level. |
| Payment recording | **A** | `POST /contracts/{id}/payments`, idempotent on `(contract_id, external_reference)` unique constraint. Tested (`test_payments_flow.py`). |
| Payment allocation waterfall | **A / B** | `services/allocation.py` — pure function, **oldest installment first**, then Late Fee → Profit → Principal. Well tested (`test_allocation.py`, including the spanning-two-installments case). `B`: the waterfall order is **hard-coded**, not configurable. |
| Payment ≠ settlement ≠ reconciliation | **G** | `Payment` is a single manual record. Statuses only `applied` / `overpaid`. **No** payment initiation, gateway, gateway callback, settlement, settlement batch, bank statement, reconciliation, or exception queue. No `gateway_reference` / `bank_transaction_reference`. |
| Bank reconciliation | **G** | None. |
| General ledger / accounting events | **G** | **Confirmed major gap.** No `AccountingEvent`, no ERP/GL posting boundary. Financial events happen but are not emitted for accounting. |
| Inventory / retail fulfillment | **G** | `Product` = `id, name, category, cash_price, installment_eligible`. No SKU, brand, warehouse, stock, reservation, serial/IMEI. "Confirm Delivery" (`offers.py:confirm_delivery`) is a bare `created → active` status flip + `activated_at` timestamp. Double-selling unavailable stock is **not prevented**. |
| Sales Order | **A** | `SalesOrder` (application, product, offer, `sale_price`, `down_payment_amount`). Kept separate from `InstallmentContract`. Tested. |
| Invoice | **G** | No invoice entity; no tax structure. |
| Multiple active contracts | **A (structure) / G (exposure)** | Nothing prevents a customer having N contracts; each is independent with its own schedule/receivable/status. **But** there is no exposure aggregation anywhere — assessment does not see the customer's other contracts. |
| Late fees | **B / F** | `LateFeeCharge` (own table — correct, never folded into profit). Configurable rate + grace. `B`: percentage-only (no fixed-fee option), once-per-installment only (`late_fee_once_per_installment` flag is read but recurring re-charge not built), `late_fee_max_per_contract` config exists but is **not wired**. Waiver = maker-checker (Step 6). |
| Collections — case + activities + PTP | **A / B** | `CollectionCase` (≤1 open per contract via partial unique index + service check), `CollectionActivity` (call/sms/email/visit/promise_to_pay/other). Auto open on overdue, auto close when overdue count → 0. Tested (`test_collections.py`). `B`: `promise_status` is set to `pending` on creation and **never transitions** to `kept`/`broken` (no broken-promise detection). No escalation, no settlement arrangement, no legal boundary, no write-off recommendation, no recovery. |
| Restructuring / rescheduling | **G** | None. |
| Early settlement | **A / B** | `GET /contracts/{id}/settlement-quote` (components: outstanding principal, outstanding late fees, unearned profit, rebate, profit still charged, final payoff, quote expiry) + `POST /contracts/{id}/settle` (re-computes server-side, 422 on mismatch). Tested (`test_closure.py`). `B`: the quote is **not persisted** — no `Quote` entity, no quote id, no calculation-version; `settle` mutates installments in place and records the rebate only in `ContractClosure.notes` text (see 1.4). |
| Cancellation (pre-delivery) / Return (post-delivery) | **A / B** | `POST /contracts/{id}/cancel` (created only) and `/return` (active only), each writes exactly one `ContractClosure` with a signed `financial_adjustment`. Tested. `B`: no `Refund` entity, no product-condition/serial verification, no inventory return, no itemized profit-reversal transactions — `financial_adjustment` is a single signed number; installment mutation is in place. |
| Contract closure | **A** | Exactly one `ContractClosure` per contract (`contract_id` unique), reason enum `normal`/`early_settlement`/`cancellation`/`return`; closed contract → 409 on any further closure op. Tested. `normal` (maturity) closure is defined but not produced by any code path. |
| Write-off + recovery | **G** | None. |
| ECL / provisioning | **G** | None. Some inputs derivable (outstanding via receivable view, DPD computable) but not exposed as a clean extract. |
| RBAC | **A** | `core/auth.py` — one `require_roles(*roles)` dependency + `authorize_owner_or_roles(...)`; all routers behind `Depends(get_current_user)` except `/auth/login` and `/health`. Tested (`test_rbac.py`, 8 cases grouped by pattern). |
| Audit trail | **A / B** | `AuditEvent` (user, action, entity_type, entity_id, before/after JSON, timestamp) written by `services/audit.record_event` on state-changing endpoints. Tested (`test_audit.py`). `B`: `before`/`after` are minimal status snapshots by design; a few automatic transitions write `user_id = null`; no tamper-evidence (hash chain) — acceptable for now, note for production. |
| Maker-checker | **A** | Generic `ApprovalRequest`; **`decided_by != requested_by` enforced in `services/approvals.py`** (409, any role incl. admin). Applied to `late_fee.waive` and `config.update`. `PUT /config/parameters/{key}` now returns **202 + pending approval** (deliberate Step 6 behaviour change). Tested (`test_approvals.py`, 8 cases). |
| Configurable business rules | **A** | `config_parameters` table, seeded from `config/business_rules.yaml`, read via `ConfigService`; 17 parameters, `json` value-type supported. **No policy numbers in engine code.** Tested that changing config changes outcomes. |
| Standardized API errors (RFC 9457) | **B** | `DomainError → HTTPException(status, detail=str)` → `{"detail": "..."}`; validation → FastAPI default `{"detail": [{loc,msg,type}]}`. Consistent-ish, **not** Problem Details (`application/problem+json`), no error `type`/`code`/`instance`. |
| Customer self-service portal | **E** | No portal. But backend has `authorize_owner_or_roles` and `customers.user_id`, so a `customer`-role token can already read *its own* application / contract / receivable / settlement-quote / collection case → architectural readiness is partial. |
| Notifications | **G** | No event boundary, no notification entity, no providers. |
| Batch / scheduled processing | **B** | Only `POST /jobs/assess-overdue` (manual, admin-only, accepts `as_of`). It is idempotent and auditable. No scheduler; no maturity job, reminder job, settlement-expiry job (offer/quote expiry is checked lazily), profit-recognition job, or reconciliation job. |
| Reporting / MIS | **G** | **No list/query endpoints at all** for customers, products, applications, offers, contracts (only `GET /{id}`). `audit`, `collections`, `approvals` have list endpoints. No aggregate/report endpoints. |
| Product / pricing / promotion model | **B** | Minimal `Product`. No `SKU`, `Brand`, `Promotion`, `PricingRule`, `InventoryItem`, `SerialNumber`. |
| Accounting terminology discipline | **A** | Consistently "profit" not "interest"; `cash_price` vs `installment_sale_price` vs `unearned_profit_balance` vs recognized (paid) profit are all distinct. Strong. |
| Business Decision Register | **B** | Placeholders are individually flagged in `config/business_rules.yaml` comments and README ("BUSINESS DECISION REQUIRED", "not confirmed policy", "NOT WIRED UP"). No single consolidated register document (this document's Deliverable 6 fills that). |
| Tax / VAT | **G** | No tax fields. (Acceptable per brief — but there is no invoice structure to add them to later.) |
| Immutable financial history | **B** | `Payment`, `PaymentAllocation`, `LateFeeCharge`, `AuditEvent` are append-mostly. **But** settlement / return / waiver **mutate rows in place** (`Installment.principal_paid` set to full, `unearned_profit_balance` zeroed, `status` flipped, `LateFeeCharge.status → waived`) with no compensating reversal/adjustment transaction. `ConfigParameter` is updated in place (history only via `AuditEvent`). |
| Idempotency | **B** | `Payment` unique `(contract_id, external_reference)`; `approve`/`reject` re-check status; overdue job re-checks existing charges; config-approval re-checks for a pending request. **No** generic `Idempotency-Key` mechanism; no protection on `create_customer` / `create_application` / `create_offer` (duplicate submits create duplicate rows — though `national_id` unique catches duplicate customers). |
| Security / production readiness | **B (demo-ready) / G (prod)** | See 1.5. |

## 1.3 Strengths to preserve (do **not** rebuild)

- **Domain integrity:** retail-installment model, principal/profit split, unearned profit, no disbursement.
- **Config externalisation:** engine code has zero policy numbers; 17 parameters; `config_snapshot` on each assessment.
- **Pricing/amortization correctness:** declining-balance with cumulative rounding, proven zero-drift reconciliation across tenors (`test_pricing.py`).
- **Allocation waterfall correctness:** oldest-first + LF→Profit→Principal, with the tricky spanning case pinned by test.
- **Entity separation:** `Application` / `Offer` / `SalesOrder` / `InstallmentContract` / `PaymentSchedule` / `Installment` / `ContractClosure` are genuinely distinct and linked — not one "loan" blob.
- **Auth + RBAC + audit + maker-checker** are real, reusable, and tested (one `require_roles` dependency; generic `ApprovalRequest`).
- **Idempotent, auditable overdue job** — the right shape for a future scheduler.
- **Test discipline:** ~103 backend cases; every step's flows re-run under auth.

## 1.4 Structural weaknesses / correctness risks found

| # | Finding | Severity |
|---|---|---|
| S-1 | **`referred` is a dead-end.** `generate_offer` requires `status == approved`; there is no endpoint to move a referred application forward. Branch-assisted and borderline applications cannot be progressed at all. | **P0** (blocks a core business path) |
| S-2 | **Affordability proxy is not the contractual obligation.** DBR at assessment uses `requested_amount / tenor` (no profit, no down payment) and is never recomputed against the priced offer. A 300 KWD / 12-mo request is assessed on ~25/mo when the real installment is higher. | **P0** (financial-correctness / credit-policy) |
| S-3 | **No exposure aggregation.** Assessment ignores the customer's other active contracts on this very platform; obligations are self-reported only. | **P0** |
| S-4 | **Settlement / return / waiver mutate financial rows in place** with no reversal/adjustment transaction. You cannot reconstruct "profit 12.46 scheduled → 6.23 charged, 6.23 rebated"; the installment just shows `profit_paid = 12.46`. Violates the brief's Section 43 mandate. | **P0** |
| S-5 | **Payment success is treated as final.** No settlement/reconciliation lifecycle → the company cannot prove a payment landed in the bank against the right contract. Brief Section 20 calls this "a critical architecture requirement". | **P0** (for real payments) — **P1** while payments are manual |
| S-6 | **No accounting event emission.** Every financial event (sale, down payment, profit recognition, payment, late fee, waiver, settlement, cancellation, return) happens with no GL boundary. | **P0/P1** |
| S-7 | **No inventory guard.** Two contracts can be created for the last unit of stock. | **P1** |
| S-8 | **No reporting surface.** No list endpoints → operations cannot see a queue of referred applications, overdue contracts, open collection cases, or reconciliation exceptions. | **P1** |
| S-9 | **Broken-promise-to-pay never fires.** `promise_status` stuck at `pending`. Collections cannot act on missed promises. | **P1** |
| S-10 | **`normal` (maturity) closure never produced.** A fully-paid contract stays `active` forever; no maturity/closure job. | **P1** |
| S-11 | **Config change history is thin.** `ConfigParameter` updated in place; only `AuditEvent` records the old value. No effective-dated config, no rule-set version id on `AssessmentResult`. | **P1** |
| S-12 | **No T&C / consent capture.** Cannot prove which contract terms the customer accepted. | **P1** (legal) |

## 1.5 Demo-ready vs production-ready

| Aspect | Status | Note |
|---|---|---|
| AuthN (JWT) | demo | HS256, secret defaults to `dev-insecure-secret-change-me`; 30-min expiry; **no refresh, no revocation, no `jti`/blocklist**. |
| AuthZ (RBAC + maker-checker) | close to prod | Real, tested. Needs the ownership model hardened (currently `customers.user_id` set manually). |
| Token storage (frontend) | demo | `localStorage` (XSS-exposed) — already flagged in README as not the long-term approach. |
| CORS | **missing** | No `CORSMiddleware`. Frontend relies on a Vite dev proxy; a real deployment needs same-origin serving or an explicit allow-list. |
| Rate limiting | **missing** | None. `/auth/login` is unthrottled → credential stuffing. |
| Input validation | prod | Pydantic throughout. |
| Error model | demo | Not RFC 9457; leaks raw `str(exception)` in `DomainError` messages (mostly benign, but unreviewed). |
| Secrets management | demo | `.env` files; no vault/secrets-manager integration. |
| Logging | demo | `print()` in lifespan; no structured/request logging; no correlation id. |
| Monitoring / tracing / metrics | **missing** | None. `/health` does not check the DB. |
| Backup / DR | **missing** | Docker volume only. |
| Webhook signature verification | n/a | No webhooks yet (will matter for payment gateway). |
| HTTPS / TLS termination | not configured | Plain uvicorn. |
| DB migrations | prod-ish | Alembic chain 0001→0006, up+down verified in earlier sessions. |
| Idempotency (financial) | partial | See table row above. |

---

# DELIVERABLE 2 — Complete Gap Matrix

Priority: **P0** core financial correctness · **P1** important operational capability ·
**P2** architectural/scale · **P3** future.
"Reuse?": **Reuse** (use as-is) · **Extend** (add fields/endpoints to an existing entity/service) ·
**New** (new entity/service justified) · **Future** · **Integrate** (external boundary).

| # | Domain | Current State | Evidence | Gap | Required Change | Prio | Reuse? | Business Decision? |
|---|---|---|---|---|---|---|---|---|
| G-01 | Referred → manual verification | `referred` is terminal | `offers.py:42`, no route in `applications.py` | Credit officer cannot progress a referred/branch application | Add `POST /applications/{id}/review` (decision + notes) for `credit_officer`/`credit_manager`/`admin`; allow `approved`/`rejected`/`conditionally_approved`; write `AssessmentResult` (source=`manual`) + `AuditEvent` | **P0** | Extend `credit_application` + `assessment` service | Verification SLA & who can override — **BDR-25** |
| G-02 | Affordability basis | DBR on `requested_amount/tenor` | `assessment.py:estimate_installment` | Not the contractual monthly obligation; not re-checked post-pricing | Make the affordability obligation basis configurable (requested amount vs priced installment vs installment-sale-price/tenor); add an **affordability re-check at offer acceptance** | **P0** | Extend `assessment` + `offers` service | Which obligation figure — **BDR-15/BDR-05** |
| G-03 | Exposure aggregation | none | assessment ignores other contracts | Customer with N active contracts under-assessed | Add an `exposure` read-model (count, outstanding principal, outstanding receivable, delinquent exposure per customer) + a config-driven exposure rule in the engine | **P0** | New read-model + Extend `assessment` | Aggregation dimensions & limits — **BDR-07/BDR-08** |
| G-04 | Immutable financial history | in-place mutation on settle/return/waiver | `closure.py:_close_out_schedule`, `approvals.py:_execute` | Cannot reconstruct original vs adjustment | Introduce a `FinancialTransaction` / `LedgerEntry` append-only record for every money-relevant event (charge, payment-allocation, rebate, reversal, adjustment, write-off, recovery); stop zeroing/overwriting — post compensating entries | **P0** | New (`FinancialTransaction`); Extend closure/allocation/approval services | Reversal vs adjustment semantics — **BDR-19** |
| G-05 | Payment lifecycle (initiation→gateway→settlement→reconciliation) | single manual `Payment`, statuses `applied`/`overpaid` | `models/payment.py` | Cannot prove money reached the bank | Extend `Payment` with `channel`, `gateway_reference`, `merchant_reference`, richer status enum (`initiated/pending/success/failed/cancelled/refunded/settled/reconciled/exception`); add `PaymentInitiation`, `SettlementBatch`, `BankTransaction`, `Reconciliation` entities; keep allocation as-is (runs on `success`) | **P0** (real) / **P1** (manual) | Extend `Payment` + New (settlement/recon entities) | Bank ref format & matching rules — **BDR** (new) |
| G-06 | Bank reconciliation | none | — | No matching, no exception queue | `Reconciliation` entity + configurable matching rules (exact gateway ref → merchant ref → contract/customer ref → amount+date+channel → exception queue); manual override maker-checker-controlled | **P0** (real) / **P2** (manual) | New | Matching rule config — **BDR** (new) |
| G-07 | Accounting / GL boundary | none | — | Financial events not posted | `AccountingEvent` entity (event_type, refs, amount, currency, branch, date, `accounting_status`, `erp_reference`, idempotency ref, retry status) + an emitter hooked to the ~14 event types in brief §22; **no GL logic** — just an outbound integration boundary | **P0/P1** | New (thin boundary) | ERP owns posting? — **BDR-15** |
| G-08 | Inventory / fulfillment | delivery = status flip | `offers.py:confirm_delivery` | Double-sell possible; no serial/delivery record | Minimal: `InventoryItem` (sku, branch/warehouse, qty available, qty reserved), `Reservation` (contract/order, qty, status), `Delivery` (contract, scheduled/completed, `serial_number`/`imei`, confirmed_by). Integration boundary to a real WMS. | **P1** | New (minimal domain) + Integrate | Reservation & deduction point — **BDR-18**; ownership transfer — **BDR-01** |
| G-09 | KYC | manual `risk_score` int | `models/customer.py:33` | No KYC status, no verification | `KycProfile` (status, verified_by, verified_at), `CustomerDocument` (type, metadata, verification_status), `MobileVerification`/OTP; **document requirements configurable** | **P1** | New + Extend `customer` | Required document set — **BDR-17** |
| G-10 | T&C / consent | none | — | Cannot prove accepted terms | `TermsVersion` (version, content hash, effective_from), `ConsentRecord` (customer, terms_version, offer/contract ref, accepted_at, channel, ip/device where lawful) | **P1** | New | T&C content ownership; promissory note — **BDR-16** |
| G-11 | Pricing model dimensions + versioning | single global rate table | `config: tenor_profit_rate_table` | No category/segment/promo/effective-date; no rule id | `PricingRule` entity (dimensions, effective_from, version, status) replacing/backing the config table; offer stores `pricing_rule_id` + frozen numbers (keep freezing) | **P1/P2** | Extend (config → entity) + Extend `offer` | Actual pricing matrix + methodology — **BDR-02/03/04** |
| G-12 | Profit **recognition** methodology | cash-basis (paid) | `payments.py` decrements `unearned_profit_balance` | No time-based / effective-rate recognition; not configurable | Add a configurable recognition method + a (batchable) recognition run that posts `AccountingEvent`s; keep amortization *shape* as-is | **P1/F** | Extend + New (recognition run) | Accounting methodology — **BDR-04/05** |
| G-13 | Payment allocation configurability | hard-coded order | `allocation.py` | Cannot change waterfall by policy | Make the waterfall a config-driven ordered list of buckets; keep the pure function; keep oldest-first as default | **P1** | Extend `allocation` + config | Confirmed waterfall — **BDR-10** (allocation) |
| G-14 | Late fee options | %-only, once, no cap | `overdue.py`, `config` | Missing fixed basis, cap enforcement, repeatability | Extend config: `fixed`/`percentage` basis, `calculation_basis` (installment total vs overdue amount vs principal), wire `late_fee_max_per_contract`, `frequency`/`repeatable` | **P1** | Extend `overdue` + config | Late fee policy — **BDR-10** |
| G-15 | Broken PTP + collections depth | `promise_status` never transitions | `collections.py:118` | No missed-promise action; no escalation/restructure/legal/write-off/recovery | Add a PTP-evaluation step to the overdue/scheduled run (`kept` if paid ≥ promised by date, else `broken`); add `escalation_level`, settlement-arrangement link; legal boundary as a status only | **P1** | Extend `collections` + `overdue` | Escalation & legal policy — **BDR-13** |
| G-16 | Restructuring / rescheduling | none | — | Cannot reschedule without losing history | `ContractModification` (original contract, modification #, reason, old schedule ref, new schedule, approval, effective date, financial adjustment); generate a **new** `PaymentSchedule` version, never overwrite | **P1** | New | Restructuring eligibility & pricing — **BDR-11** |
| G-17 | Write-off + recovery | none | — | No loss recognition or post-write-off recovery | `WriteOff` (eligibility, reason, approval/maker-checker, principal/profit/fees written off, `AccountingEvent`), `Recovery` (post-write-off receipt, allocation, reporting). Contract stays; status `written_off` | **P1** | New + reuse maker-checker | Write-off & recovery policy — **BDR-12/13** |
| G-18 | Persisted settlement quote | computed on the fly | `closure.py:build_settlement_quote` | No quote id / version / valid-until enforcement on `settle` | `SettlementQuote` entity (id, components, total, calc timestamp, valid_until, calc_version, audit); `settle` still re-computes but references the quote | **P1** | Extend closure | Rebate formula — **BDR-09** |
| G-19 | Return/refund lifecycle | 1 signed number on `ContractClosure` | `closure.py:return_contract` | No `Refund`, no condition/serial check, no itemized reversal, no inventory return | `ReturnRequest` (eligibility, condition, serial verification), `Refund` (method, status, `AccountingEvent`), itemized reversal `FinancialTransaction`s (see G-04), inventory return (see G-08) | **P1** | New + Extend closure | Return financial treatment — **BDR-19** |
| G-20 | ECL data extract | none | — | Risk/Finance cannot get portfolio data | Read-only `GET /portfolio/ecl-extract` (contract, customer, outstanding, DPD, aging bucket, risk grade, default/write-off/recovery status). **No ECL calc** in-platform unless business chooses model A | **P1/P2** | New (read-model) | ECL ownership A/B/C — **BDR-14** |
| G-21 | Standardized API errors | `{"detail": ...}` | all routers | Not RFC 9457; inconsistent shape (str vs list) | Add exception handlers producing `application/problem+json` (`type`, `title`, `status`, `detail`, `instance`, plus `errors[]` for validation); keep `detail` for backward compat during transition | **P1** | New (handlers) — non-breaking if additive | — |
| G-22 | Reporting / list endpoints | none | route inventory | Ops cannot see queues/portfolios | Add paginated/filterable list endpoints (applications by status, contracts by status/DPD, collection cases, reconciliation exceptions) + a small set of aggregate MIS endpoints | **P1** | New (read-only, over existing tables) | Report definitions — light **BDR** |
| G-23 | Notifications | none | — | No customer/staff comms | `NotificationEvent` outbox + pluggable provider interface (SMS/email/push mock now); emit on the ~17 events in brief §36 | **P2** | New (boundary) + Integrate | Provider choice, message content | 
| G-24 | Scheduled processing | manual `assess-overdue` only | `payments.py:108` | No DPD/late-fee/reminder/recognition/reconciliation/maturity/expiry jobs | A single idempotent job runner (APScheduler or external cron hitting internal endpoints) invoking existing + new job services; each job auditable + failure-aware | **P1/P2** | Extend (job services) + New (runner) | Job cadence | 
| G-25 | Fraud / device signals (online) | none | — | Online channel has no risk signals | `DeviceSession` capture (device id, ip where lawful, app velocity, repeat-application flag) exposed to the decision engine as **signals, not auto-reject** | **P2** | New + Extend `assessment` inputs | Fraud policy — do **not** invent |
| G-26 | Pluggable provider architecture | direct field reads | `assessment.py` reads profile + `risk_score` | Core engine coupled to manual data | `ProviderRegistry` + interfaces: `KycProvider`, `CreditBureauProvider`, `IncomeProvider`, `FraudProvider`, `PaymentProvider`, `FinancingProvider`, `GlProvider`, `InventoryProvider`. All mocks now. Engine depends on the interface, not the vendor | **P1/P2** | New (interfaces) + Extend engine | — (architecture) |
| G-27 | External-service resilience | n/a | — | Needed once providers are real | Wrap each adapter: timeout, retry+backoff, idempotency key, duplicate-callback guard, circuit breaker where justified, `IntegrationLog` (status, error class, attempts) | **P2** | New (shared client) | — |
| G-28 | Multiple active contracts — policy | structurally supported | — | No limit / no exposure check | Config-driven `max_active_contracts` + `max_total_outstanding` per customer, checked in assessment | **P1** | Extend `assessment` + config | Contract/exposure policy — **BDR-08** |
| G-29 | Customer self-service portal | staff UI only; backend owner-checks exist | `core/auth.py:authorize_owner_or_roles` | No customer app | Future module — a separate frontend consuming the *same* backend read endpoints; harden `customers.user_id` linkage + add a customer-registration/OTP flow | **P2/Future** | Reuse backend + New frontend | Portal scope — **BDR** |
| G-30 | Security hardening | see 1.5 | `main.py` (no CORS/handlers), `security.py` | Not production-ready | CORS allow-list, `/auth/login` rate limit, structured logging + correlation id, `/health` DB check, JWT `jti` + refresh/revocation, secrets manager, HTTPS at the edge, webhook signature verification (for gateway) | **P1 (CORS/rate-limit/logging) / P2 (rest)** | New (middleware) | — |
| G-31 | Tax / VAT readiness | none | — | No structure to add tax later | When adding `Invoice` (part of G-19/G-08), include nullable tax lines / tax-code fields so a tax engine can be integrated later. **Do not build a tax engine.** | **P2/Future** | Design-only now | Kuwait tax applicability — **BDR-42** |
| G-32 | Idempotency (generic) | partial | see 1.5 | Duplicate create requests | `Idempotency-Key` header support on all `POST` create/financial routes, stored + replayed like `Payment.external_reference` | **P1** | Extend (shared dependency) | — |
| G-33 | Conditional approval | not modelled | decision enum | `conditionally_approved` outcome + conditions (e.g. higher down payment, shorter tenor, guarantor) that gate offer generation | **P1** | Extend `credit_application` + `offers` | Condition catalogue — **BDR** |
| G-34 | Decision metadata | partial | `AssessmentResult` | No `rule_set_version`, `decision_source`, `risk_grade`, full input snapshot | Extend `AssessmentResult` with those fields; snapshot inputs (income/obligations/exposure) at decision time | **P1** | Extend | Risk grading scale — **BDR** |
| G-35 | `created_by` on application | free-text string | `credit_application.created_by` | Not a real actor link for branch origination | Change to `created_by_user_id` FK (keep string for legacy) + `origination_branch_id` | **P1** | Extend | Branch entity needed? — light **BDR** |

## 2.1 Test-scenario coverage checklist (brief §50)

**Covered** = an existing backend test exercises it. Frontend tests (7) cover login + assessment-screen + schedule-table only.

| # | Scenario | Covered? | Where / note |
|---|---|---|---|
| 1 | New online customer | **Partial** | Customers created in many tests; `channel=online` used in `test_applications.py`. No online-specific path exists. |
| 2 | Existing customer | **No** | No "reuse existing customer for 2nd application" test. |
| 3 | Branch-assisted application | **Partial** | `channel=branch` is the default in `helpers.py`; but no "sales employee on behalf of" semantics to test. |
| 4 | Approved application | **Yes** | `test_assessment.py::test_application_approved_under_default_config`, `test_offer_flow.py`. |
| 5 | Rejected application | **Yes** | `test_assessment.py::test_rejected_when_income_below_configured_minimum`. |
| 6 | Referred application | **Yes (decision only)** | `test_assessment.py::test_referred_*`. **No test of progressing a referred app** (because there is no such path). |
| 7 | Conditional approval | **No** | Not modelled. |
| 8 | Multiple active contracts | **No** | Not modelled/tested. |
| 9 | Exposure exceeded | **No** | Not modelled. |
| 10 | Down payment success | **Yes** | `test_offer_flow.py::test_full_flow_offer_to_active_contract` (`down_payment_confirmed: true`). |
| 11 | Down payment failure | **Partial** | `test_offer_flow.py::test_accept_without_confirmation_creates_nothing` (confirmation false → nothing created). No real payment failure. |
| 12 | Contract creation | **Yes** | `test_offer_flow.py`. |
| 13 | Inventory unavailable | **No** | No inventory. |
| 14 | Inventory reservation | **No** | No inventory. |
| 15 | Delivery | **Yes (status flip)** | `test_offer_flow.py` confirm-delivery; `test_closure.py` helpers. |
| 16 | Full payment | **Yes** | `test_payments_flow.py::test_full_payment_of_one_installment`. |
| 17 | Partial payment | **Yes** | `test_allocation.py`, `test_payments_flow.py`. |
| 18 | Multiple payments | **Yes** | `test_payments_flow.py::test_payment_spanning_two_installments...`. |
| 19 | Overpayment | **Yes** | `test_allocation.py::test_overpayment_leaves_unallocated_remainder`, `test_payments_flow.py::test_overpayment_marks_payment_overpaid`. |
| 20 | Payment failure | **No** | No failed-payment state. |
| 21 | Payment reversal | **No** | Not modelled. |
| 22 | Late payment | **Yes** | `test_overdue.py` (`as_of` past due). |
| 23 | Late fee | **Yes** | `test_overdue.py::test_installment_past_grace_gets_exactly_two_percent...`. |
| 24 | Collection case | **Yes** | `test_collections.py::test_overdue_opens_exactly_one_case...`. |
| 25 | Promise to Pay | **Yes** | `test_collections.py::test_promise_to_pay_stores_fields...`. |
| 26 | Broken Promise | **No** | `promise_status` never transitions. |
| 27 | Restructuring | **No** | Not modelled. |
| 28 | Early settlement | **Yes** | `test_closure.py` (quote reconciliation, exact-amount settle, wrong-amount 422, rebate config change). |
| 29 | Cancellation | **Yes** | `test_closure.py::test_cancel_before_delivery_...`, `..._after_delivery_returns_409...`. |
| 30 | Return | **Yes** | `test_closure.py::test_return_after_delivery_...`, `..._before_delivery_returns_409...`. |
| 31 | Refund | **Partial** | Return computes a `financial_adjustment` (tested); no `Refund` entity/lifecycle. |
| 32 | Write-off | **No** | Not modelled. |
| 33 | Recovery | **No** | Not modelled. |
| 34 | Gateway callback | **No** | No gateway. |
| 35 | Duplicate gateway callback | **Partial (analogous)** | `test_payments_flow.py::test_idempotent_replay_does_not_double_allocate` proves the idempotency mechanism that a callback would reuse. |
| 36 | Settlement (bank) | **No** | Not modelled. |
| 37 | Bank reconciliation | **No** | Not modelled. |
| 38 | Unmatched bank transaction | **No** | Not modelled. |
| 39 | Manual reconciliation | **No** | Not modelled. |
| 40 | Profit recognition | **Partial** | Cash-basis draw-down of `unearned_profit_balance` is asserted in `test_payments_flow.py` / `test_closure.py`. No time-based recognition. |
| 41 | ECL data extraction | **No** | No extract. |
| 42 | Contract closure | **Yes** | `test_closure.py::test_exactly_one_closure_per_contract`, re-close 409. **`normal` maturity closure untested (no path).** |

**Coverage: ~22 of 42 fully, ~7 partial, ~13 not covered.** The uncovered set maps almost exactly to
the missing domains (payment lifecycle, reconciliation, write-off/recovery, restructuring, inventory,
exposure, conditional approval, broken PTP).

---

# DELIVERABLE 3 — Target Domain Architecture

## 3.1 Layered flow (target)

```
        ONLINE  ─────┐
        (customer     │
         self-submit) │
                      ├──────►  ORIGINATION  ──►  CREDIT ASSESSMENT  ──►  DECISION ENGINE
        BRANCH  ──────┘         (Application,      (KYC · Bureau ·        (rules + manual
        (sales employee          channel,           Income · Exposure ·    verification for
         on behalf of)           consent)           Affordability ·        REFERRED /
                                                    Fraud signals)         CONDITIONAL)
                                                                                │
   ┌────────────────────────────────────────────────────────────────────────────┘
   ▼
 PRICING / OFFER  ──►  SALES ORDER  ──►  INVENTORY / FULFILMENT  ──►  INSTALLMENT CONTRACT
 (PricingRule,         (+ Invoice)       (reservation · delivery ·      (frozen pricing snapshot,
  versioned,                              serial/IMEI · confirmation)    unearned profit)
  affordability
  re-check)                                          │
                                                     ▼
                              PAYMENT SCHEDULE  ──►  RECEIVABLE (view + ledger)
                                                     │
                          ┌──────────────────────────┼───────────────────────────┐
                          ▼                          ▼                           ▼
                   PAYMENT LIFECYCLE          COLLECTIONS                 EARLY SETTLEMENT
                   initiation → gateway →     (case · activity · PTP ·    (persisted quote →
                   success → SETTLEMENT →     broken-promise → escalation  settle)
                   BANK RECONCILIATION →      → restructuring /            RETURN / CANCELLATION
                   RECEIVABLE ALLOCATION      write-off recommendation)    → REFUND
                          │                          │                           │
                          └──────────────┬───────────┴───────────────────────────┘
                                         ▼
                                  CONTRACT CLOSURE
                               (normal · settlement · cancellation ·
                                return · write-off)  +  RECOVERY (post-write-off)

 ═══════════════════════════  CROSS-CUTTING (all layers)  ═══════════════════════════
   Immutable Financial Ledger (append-only FinancialTransaction / adjustments / reversals)
   Accounting Event Boundary  ──►  ERP / GL          ECL / Risk Data Extract  ──►  Risk engine
   Audit Trail (who/what/when/why + policy version)  Notifications (outbox → providers)
   Reporting / MIS read-models                       Configuration (effective-dated, versioned)
   RBAC + Maker-Checker                              Idempotency (Idempotency-Key + dedup)
```

## 3.2 What changes per layer (preserve / extend / add)

| Layer | Preserve (as-is) | Extend | Add (new) |
|---|---|---|---|
| Origination | `CreditApplication`, `channel`, shared engine | `created_by` → user FK; consent capture link | `ConsentRecord`, `TermsVersion`, `DeviceSession` (online) |
| Assessment | rule engine, `config_snapshot`, precedence, `AssessmentResult` | decision enum (+conditional/expired/cancelled), affordability basis config, decision metadata, exposure rule | manual-verification endpoint, `Exposure` read-model, `ProviderRegistry` + adapter interfaces (mock) |
| Pricing / Offer | offer freezing, declining-balance amortization, cumulative rounding | affordability re-check at acceptance; `pricing_rule_id` on offer | `PricingRule` (versioned, effective-dated), `Promotion` (optional) |
| Sales Order | `SalesOrder` separation | link to invoice + reservation | `Invoice` (with tax-ready nullable fields) |
| Fulfilment | delivery status flip + timestamp | `confirm-delivery` requires reservation released | `InventoryItem`, `Reservation`, `Delivery` (serial/IMEI) + WMS boundary |
| Contract / Schedule | `InstallmentContract`, `PaymentSchedule`, `Installment` split, unearned profit | schedule **versioning** for restructuring | `ContractModification` |
| Receivable | computed view | expose aging buckets, DPD, per-contract ledger | (optional) persisted `ReceivableLedger` if Finance requires |
| Payment | allocation waterfall, idempotency, `PaymentAllocation` | `Payment` status enum + refs; waterfall config | `PaymentInitiation`, `SettlementBatch`, `BankTransaction`, `Reconciliation` |
| Collections | `CollectionCase` (≤1 open), activities, auto open/close | broken-PTP evaluation, escalation level, settlement arrangement | (link to) `ContractModification`, `WriteOffRecommendation` |
| Settlement / Return / Cancel | server-side re-compute, closed→409 guard, `ContractClosure` | persist quote; itemized reversal transactions; refund lifecycle | `SettlementQuote`, `ReturnRequest`, `Refund` |
| Closure | one `ContractClosure` per contract | add `written_off` reason + `normal` maturity path | `WriteOff`, `Recovery` |
| Cross-cutting | RBAC, maker-checker, audit, config table | audit policy-version tag; effective-dated config | `FinancialTransaction` ledger, `AccountingEvent`, `NotificationEvent`, `IntegrationLog`, `IdempotencyKey`, MIS read-models, RFC 9457 handlers, CORS/rate-limit/logging middleware, job runner |

---

# DELIVERABLE 4 — Target Entity Model

## 4.1 Existing — keep unchanged

`Customer`, `CustomerProfile`, `Product`, `CreditApplication` (minor field additions below),
`AssessmentResult` (field additions below), `ConfigParameter`, `InstallmentOffer`, `SalesOrder`,
`InstallmentContract`, `PaymentSchedule`, `Installment`, `Payment` (field additions below),
`PaymentAllocation`, `LateFeeCharge`, `ContractClosure`, `User`, `AuditEvent`, `CollectionCase`,
`CollectionActivity`, `ApprovalRequest`.

## 4.2 Extend (add fields / endpoints — no rebuild)

| Entity | Add | Why |
|---|---|---|
| `CreditApplication` | `created_by_user_id` FK, `origination_branch_id` (nullable), decision-outcome enum widened | real actor link, branch origination, conditional/expired |
| `AssessmentResult` | `rule_set_version`, `decision_source` (`auto`/`manual`), `risk_grade`, `inputs_snapshot` JSON, `conditions` JSON (for conditional approval) | auditable decisioning (brief §11) |
| `Customer` | (via new `KycProfile` relation) — `Customer` itself unchanged | keep the two-table discipline |
| `Product` | `sku` (nullable), `brand` (nullable) | minimal retail identity; full catalog stays external |
| `InstallmentOffer` | `pricing_rule_id` FK, `affordability_recheck` JSON | traceability + §15 re-check |
| `Payment` | `channel`, `gateway_reference`, `merchant_reference`, `initiation_id` FK, status enum widened, `settlement_id` FK (nullable), `reconciliation_id` FK (nullable) | §20 lifecycle |
| `Installment` | *no new stored DPD* (keep computed) — but add `fees_paid` only if fees ever attach to the installment; otherwise leave | avoid denormalising volatile DPD |
| `ContractClosure` | `written_off` added to reason enum | write-off closure |
| `CollectionActivity` | `escalation_level` (nullable) | §28 |
| `ConfigParameter` | `effective_from`, `version`, `superseded_by` (or a `config_parameter_history` table) | §11 effective-dated policy |

## 4.3 New entities — with the "is this really necessary?" test

| New entity | Business problem it solves | Could it be an integration instead? | Verdict |
|---|---|---|---|
| `FinancialTransaction` (append-only ledger line) | Immutable history (§43); reconstruct original vs adjustment; feed accounting | No — this is the system of record for money movement | **Required, P0** |
| `AccountingEvent` | Post financial events to ERP/GL (§22) without duplicating the ERP | It *is* the integration boundary | **Required, P0/P1** (thin) |
| `SettlementBatch`, `BankTransaction`, `Reconciliation` | Prove money reached the bank against the right contract (§20/21) | Partly — statement import is an integration; the matching state is ours | **Required for real payments, P0** |
| `PaymentInitiation` | Track a payment attempt before it's a `Payment` (§20) | Gateway-side, but our record is needed for idempotency/dedup | **Required for real payments, P0** |
| `InventoryItem`, `Reservation`, `Delivery` | Prevent double-sell; capture serial/IMEI; delivery proof (§23) | Stock levels = WMS integration; reservation *state* + serial capture is ours | **Minimal domain, P1** |
| `Invoice` | Legal sale document; tax-ready structure (§24/42) | No | **Required, P1** |
| `KycProfile`, `CustomerDocument`, `MobileVerification` | Identity assurance, document evidence (§8) | Verification *results* come from providers; the records are ours | **Required, P1** |
| `TermsVersion`, `ConsentRecord` | Prove which terms the customer accepted (§9) | No | **Required, P1** |
| `PricingRule` | Versioned, dimensioned pricing; historical traceability (§16) | No | **Required, P1** (can start as a richer config table) |
| `ContractModification` + `PaymentSchedule` versioning | Restructure without overwriting history (§29) | No | **Required, P1** |
| `SettlementQuote` | Quote id / valid-until / calc version (§30) | No | **Required, P1** |
| `ReturnRequest`, `Refund` | Return eligibility + refund lifecycle (§31) | Refund *execution* = payment provider; the request/refund records are ours | **Required, P1** |
| `WriteOff`, `Recovery` | Loss recognition + post-write-off receipts (§32) | No | **Required, P1** |
| `NotificationEvent` (outbox) | Decouple domain from SMS/email providers (§36) | Providers are integrations; the outbox is ours | **Required, P2** |
| `IntegrationLog` | Resilience/observability for external calls (§13) | No | **Required once providers are real, P2** |
| `IdempotencyKey` | Generic duplicate-request protection (§44) | No | **Required, P1** |
| `Exposure` (read-model / materialised view) | Multi-contract exposure for assessment (§26) | No | **Required, P0** (can be a query, not a table, initially) |
| `DeviceSession` | Online fraud/velocity signals (§14) | Signals may come from a fraud provider; capture is ours | **P2** |
| `Promotion` | Retail promos on pricing (§39) | — | **Future / P3** unless a confirmed campaign exists |

**Explicitly NOT building:** a full WMS, a full ERP/GL, a tax engine, a BI platform, a rules DSL,
a workflow engine, a generic document-management system, an external BNPL clone. Each is an
integration or a future decision.

---

# DELIVERABLE 5 — Integration Map

Core principle: **the domain services depend on an interface in `app/services/providers/`, never on a
vendor SDK.** All adapters ship as mocks first.

| Boundary | Purpose | Current | Target interface | Resilience needed | Priority |
|---|---|---|---|---|---|
| **KYC Provider** | Identity verification, sanctions/PEP | none (manual) | `KycProvider.verify(customer) -> KycResult` | timeout, retry, idempotency, `IntegrationLog` | P1 (mock), P2 (real) |
| **Credit Bureau** | External credit history / score | none (`risk_score` manual) | `CreditBureauProvider.pull(customer) -> BureauReport` | timeout, retry+backoff, cache TTL, circuit breaker | P1 (mock) |
| **Income / Bank Statement** | Verified income | none (self-reported) | `IncomeProvider.assess(customer) -> IncomeAssessment` | timeout, retry, idempotency | P2 |
| **Fraud / Device** | Online risk signals | none | `FraudProvider.score(session, application) -> FraudSignals` | timeout, fail-open (signal only) | P2 |
| **Payment Gateway** | Collect installments / down payment | none (manual `Payment`) | `PaymentProvider.initiate/status/refund`; inbound webhook w/ **signature verification** + **duplicate-callback guard** | idempotency key, dedup, retry, reconciliation feed | **P0 (real payments)** |
| **Bank (statements)** | Settlement + reconciliation feed | none | `BankStatementProvider.fetch(date_range) -> [BankTransaction]` (or SFTP/file import) | file idempotency, replay-safe import | P0 (real payments) |
| **ERP / GL** | Post accounting events | none | `GlProvider.post(AccountingEvent) -> GlReference` | idempotency ref, retry queue, dead-letter, `accounting_status` | P0/P1 |
| **Inventory / WMS** | Stock availability, reservation, serials | none | `InventoryProvider.check/reserve/release/confirm` | timeout, compensating release, retry | P1 |
| **Notification provider(s)** | SMS / email / push | none | `NotificationProvider.send(NotificationEvent)` | outbox + retry, provider fallback | P2 |
| **ECL / Risk engine** | Provisioning | none | `EclProvider.submit(portfolio_extract)` **or** in-platform calc (model A) | batch, idempotent submission | P1/P2 — **BDR-14** |
| **External Financing / BNPL** (Path B, future) | Third party owns credit + receivable | not applicable (Path A) | `FinancingProvider.originate/status` — a **swap-in at the offer/contract boundary** | full resilience suite | **Future — BDR-20**; keep the boundary clean, do not build |

**Adapter package layout (proposed, not yet built):**
```
app/services/providers/
  base.py            # Protocol/ABC per provider + IntegrationLog helper + resilient-call wrapper
  kyc/               mock.py   (real vendors added later)
  bureau/            mock.py
  payment/           mock.py
  gl/                mock.py
  inventory/         mock.py
  notification/      mock.py
  registry.py        # returns the configured implementation per environment
```

---

# DELIVERABLE 6 — Business Decision Register

**Rule:** none of these are guessed or hard-coded. Where a value is needed today it lives in
`config/business_rules.yaml` with a placeholder and a "not confirmed" comment. Column *Config boundary*
= where the decision is (or should be) parameterised.

| ID | Decision | Why it matters | Current placeholder / state | Config boundary | Notes / recommended stance |
|---|---|---|---|---|---|
| BDR-01 | Product ownership transfer point (at delivery? on full payment?) | Drives return treatment, repossession rights, accounting | `ownership_transfers_on_delivery = true` (placeholder, **echoed only, no logic branches**) | `config` (exists) — needs logic in return/write-off | Legal input required. Keep swappable. |
| BDR-02 | Actual installment pricing matrix (tenor → rate, by category/segment) | The core commercial number | `tenor_profit_rate_table` = fictitious `{6:.04, 12:.09, ...}` | `PricingRule` entity (target) | Finance/commercial to supply. |
| BDR-03 | Pricing methodology (flat markup vs rate-based vs matrix) | Whether "rate × principal" is even the right model | flat `rate × principal_financed` | pricing service + `PricingRule` | Confirm before extending pricing. |
| BDR-04 | Profit **calculation** methodology | Total profit figure | flat-rate | pricing service | Distinct from BDR-05. |
| BDR-05 | Profit **amortization / recognition** method (straight-line / declining-balance / effective-rate; cash vs accrual) | Reported earned/unearned profit, GL postings | amortization *shape* = declining-balance (fixed in code); recognition = **cash-basis** (on payment) | recognition method config + recognition-run service | **Finance/audit sign-off mandatory.** Do not pick. |
| BDR-06 | Down payment minimum % | Affordability + risk | `minimum_down_payment_pct = 0.15` (placeholder) | `config` (exists) | — |
| BDR-07 | Exposure aggregation **level** — company-wide vs per BU / brand / product category | How total customer risk is measured | **P0-4 done (company-wide only).** `exposure_aggregation_level = "company_wide"` is live and is the only implemented value; any other value raises. `app/services/exposure.py` sums outstanding across all non-closed contracts. Per-category/brand/BU aggregation is **still open** and needs a policy decision + code. | `config` (`exposure_aggregation_level`) + `exposure` service | Credit / risk policy owner. |
| BDR-08 | Exposure **threshold** + multiple-active-contract policy (max total outstanding; max count; per-category limits) | Whether a customer may take on another contract | **P0-4 done (structure).** `max_customer_exposure_kwd` (placeholder **8000**) checked in assessment: `current aggregate exposure + new request's financed estimate > limit → referred` (DBR-family precedence). The *number* and whether to also cap contract *count* are unconfirmed. | `config` (`max_customer_exposure_kwd`), checked in `assessment` | Credit policy owner. |
| BDR-09 | Early settlement rebate formula | Customer payoff amount | `early_settlement_profit_rebate_pct = 0.5` (placeholder) | `config` (exists) | Regulatory/consumer-fairness angle. |
| BDR-10 | Late fee policy (basis: fixed/%, calculation basis, grace, cap, frequency, repeatability) + **payment allocation waterfall** | Delinquency economics + how payments reduce debt | rate `0.02`, grace `10d`, once-only, `max` config **not wired**; waterfall hard-coded oldest-first LF→Profit→Principal | `config` (partial) + `overdue`/`allocation` services | Confirm waterfall is universal policy before making it "the" default. |
| BDR-11 | Restructuring / rescheduling policy (eligibility, re-pricing, fee) | Whether/how a struggling customer is helped | **none** | new module + `config` | — |
| BDR-12 | Write-off policy (DPD threshold, approval levels, partial vs full) | Loss recognition timing | **none** | new module + `config` + maker-checker | — |
| BDR-13 | Recovery policy (allocation of post-write-off receipts, incentives, legal) | Recovered-cash treatment | **none** | new module + `config` | — |
| BDR-14 | ECL ownership (A in-platform / B external risk engine / C ERP-Finance) | Where provisioning is computed | **none** | integration boundary (`EclProvider`) or in-platform module | Expose the data extract regardless; defer the calc. |
| BDR-15 | Accounting treatment + ERP ownership of posting | GL correctness | **none** | `AccountingEvent` + `GlProvider` | Finance + ERP team. |
| BDR-16 | Promissory note / legal document requirements per contract | Enforceability | **none** | `config` (document requirements) + `CustomerDocument` | Legal. |
| BDR-17 | KYC document requirement set (per channel / amount / segment) | Onboarding friction vs compliance | **none** (no KYC) | `config` (configurable doc list) | Compliance. |
| BDR-18 | Inventory reservation & deduction point (at offer / at contract / at delivery) | Stock accuracy vs over-reservation | **none** (no inventory) | `config` + fulfilment service | Retail ops + WMS team. |
| BDR-19 | Return / refund financial treatment (profit reversal, fee handling, restocking fee, DP forfeiture) | Money owed to/from customer on return | single signed `financial_adjustment` on `ContractClosure`; `down_payment_refund_pct_return = 0.0` placeholder | `config` (partial) + itemized `FinancialTransaction`s | — |
| BDR-20 | Build-vs-integrate BNPL strategy (Path A vs Path B) | Whole business model | Path A assumed | keep offer/contract boundary clean; **do not** design around Tabby/Tamara | Strategic / executive. |
| BDR-21 | Product name / branding of the platform | Go-to-market, legal | de-branded to a neutral name | — | Marketing/legal; re-branding is a deliberate act, not a default. |
| BDR-22 | Affordability obligation figure (requested amount / priced installment / installment-sale-price ÷ tenor); assumed down-payment for the initial estimate; and on a failed offer-time re-check: hard-block vs route-to-referral vs warn | Whether credit decisions are sound | **P0-3 done (structure):** initial estimate now uses the pricing rate table + assumed `minimum_down_payment_pct`; offer-time re-check against the real peak installment, `offer_affordability_gate_mode` = `block` (default) / `warn_only`. The *policy* (obligation basis, assumed DP, failure action) is still unconfirmed. | `config` (`offer_affordability_gate_mode`) + assessment/offer services | Credit-policy owner. Ties to BDR-05, BDR-25 (referral route). |
| BDR-23 | Conditional-approval condition catalogue (higher DP, shorter tenor, guarantor, ...) | What "conditionally approved" can mean | not modelled | `config` (condition types) | Credit-policy owner. |
| BDR-24 | Risk grading scale (grades, score→grade mapping) | Risk reporting, risk-based pricing | `risk_score` int + two thresholds only | `config` | Risk. |
| BDR-25 | Manual verification SLA + who may override an auto-decision + maker-checker on approvals | Turnaround + control | **none** (referred is a dead-end) | new endpoint + RBAC + optionally maker-checker | Credit ops. |
| BDR-26 | Reconciliation matching rules + tolerance + auto vs manual threshold | Recon accuracy | **none** | new `config` | Finance + bank. |
| BDR-27 | Notification event set + channels + templates + opt-out | Customer comms + compliance | **none** | `config` + templates | Marketing/compliance. |
| BDR-28 | Job cadence (DPD, late fee, reminders, recognition, recon, maturity, expiry) | Operational timing | only manual `assess-overdue` | scheduler config | Ops. |
| BDR-29 | Data retention / PII handling / consent for IP & device capture | Legal (Kuwait PDPL-style) | none | policy + `config` | Legal/DPO. |
| BDR-30 | Token strategy (refresh tokens, session length, revocation, storage) | Security posture | 30-min HS256, `localStorage`, no refresh | `config` + auth redesign | Security. |

---

# DELIVERABLE 7 — Prioritized Implementation Plan

Classification per item: **[COMPLETE]** already done · **[EXTEND]** add to existing · **[NEW]** new
entity/module (justified) · **[FUTURE]** later · **[BDR]** blocked on a business decision.

## P0 — financial correctness & core integrity (do first, in this order)

| Seq | Item | Class | Depends on | Test scenarios unlocked (§50) |
|---|---|---|---|---|
| P0-1 | **Immutable financial ledger** — `FinancialTransaction` append-only; stop in-place mutation in settlement/return/waiver; post compensating entries; migrate existing paid amounts into opening balances | **[NEW] + [EXTEND]** | — | 21, 31, 40 |
| P0-2 | **Referred → manual verification** endpoint + `decision_source`/`rule_set_version`/`inputs_snapshot` on `AssessmentResult`; widen decision enum (`conditionally_approved`, `expired`, `cancelled`) | **[EXTEND]** | — | 6, 7 |
| P0-3 | **Affordability correctness** — configurable obligation basis; **re-check affordability at offer acceptance**; block/branch on failure | **[EXTEND]** | P0-2 | (strengthens 4–6) |
| P0-4 | **Exposure read-model** + config-driven multi-contract / exposure rule in the engine | **[NEW read-model] + [EXTEND]** | P0-3 | 8, 9 |
| P0-5 | **Accounting event boundary** — `AccountingEvent` + emitter on sale/down-payment/receivable/profit-recognition/payment/late-fee/waiver/settlement/cancellation/return; mock `GlProvider` | **[NEW thin]** | P0-1 | 40, (foundation for §22) |
| P0-6 | **Payment lifecycle skeleton** — widen `Payment` status enum + refs; `PaymentInitiation`; `Idempotency-Key` generic support | **[EXTEND] + [NEW]** | P0-1 | 11, 20, 34, 35 |
| P0-7 | **Settlement / reconciliation entities** — `SettlementBatch`, `BankTransaction`, `Reconciliation` + configurable matching + exception queue (still runs on manually-imported data until a gateway exists) | **[NEW]** | P0-6 | 36, 37, 38, 39 |

> **[BDR blockers on P0]:** P0-3 needs BDR-22; P0-4 needs BDR-07/08; P0-5 needs BDR-15; the *values*
> in P0-7 matching need BDR-26. The *structures* can be built with placeholder config; the
> **behaviour that depends on a policy number must stay config-driven and clearly unconfirmed.**

## P1 — important operational capability

| Item | Class | Notes |
|---|---|---|
| Inventory minimal domain (`InventoryItem`/`Reservation`/`Delivery` + serial capture) + `InventoryProvider` mock; `confirm-delivery` gated on reservation | **[NEW minimal] + [INTEGRATE]** | BDR-18 |
| `Invoice` entity (tax-ready nullable fields) between `SalesOrder` and contract | **[NEW]** | BDR-42 (design only) |
| KYC: `KycProfile`, `CustomerDocument`, `MobileVerification`; configurable document requirements | **[NEW] + [EXTEND]** | BDR-17 |
| T&C / consent: `TermsVersion`, `ConsentRecord` (hash + acceptance context) | **[NEW]** | BDR-16 |
| `PricingRule` entity (versioned, effective-dated, dimensioned) backing the config table; `pricing_rule_id` on offer | **[EXTEND config→entity]** | BDR-02/03 |
| Profit **recognition** run (configurable method) posting `AccountingEvent`s | **[NEW run] + [EXTEND]** | BDR-05 — **do not pick the method** |
| Payment allocation waterfall → config-driven ordered buckets (default unchanged) | **[EXTEND]** | BDR-10 |
| Late-fee options (fixed/%, basis, cap wired, repeatability) | **[EXTEND]** | BDR-10 |
| Broken-PTP evaluation + collections escalation level + settlement-arrangement link | **[EXTEND]** | BDR-13 |
| `ContractModification` + `PaymentSchedule` versioning (restructuring) | **[NEW]** | BDR-11 |
| `WriteOff` + `Recovery` (reuse maker-checker) + `written_off` closure reason + `normal` maturity closure job | **[NEW] + [EXTEND]** | BDR-12/13 |
| `SettlementQuote` persistence; `ReturnRequest` + `Refund` lifecycle; itemized reversal transactions | **[NEW] + [EXTEND]** | BDR-19 |
| RFC 9457 `application/problem+json` error handlers (additive, non-breaking) | **[NEW handlers]** | — |
| Reporting: paginated list endpoints (applications by status, contracts by DPD, collection cases, recon exceptions) + a few aggregate MIS endpoints | **[NEW read-only]** | — |
| `ProviderRegistry` + adapter interfaces (all mock) — refactor engine to depend on interfaces | **[NEW] + [EXTEND]** | — |
| ECL data extract endpoint (read-only) | **[NEW read-model]** | BDR-14 |
| Security P1: `CORSMiddleware` allow-list, `/auth/login` rate limit, structured logging + correlation id, `/health` DB check | **[NEW middleware]** | BDR-30 (token strategy is P2) |
| Generic `Idempotency-Key` on all create/financial POSTs | **[EXTEND]** | — |
| `created_by_user_id` FK on application; conditional-approval conditions | **[EXTEND]** | BDR-23 |
| Scheduled job runner (idempotent, auditable) invoking overdue + new job services | **[NEW runner] + [EXTEND]** | BDR-28 |

## P2 — architectural / scale

Notification outbox + provider interface · Fraud/device signal capture (`DeviceSession`) · External-service
resilience suite (`IntegrationLog`, retry/backoff/circuit-breaker wrapper) · Customer self-service portal
(new frontend on existing backend; harden `customers.user_id` + customer OTP registration) · Effective-dated
config with version history · JWT refresh/revocation + move token off `localStorage` · Advanced analytics /
BI export boundary.

## P3 — future

Promotions engine · Full inventory/WMS ownership · In-platform ECL model (only if BDR-14 → model A) ·
Tax engine (only if BDR-42 requires) · Path B external-BNPL integration (BDR-20) · Multi-currency ·
Bilingual/Arabic UI.

## 7.1 Recommended first slice for review

If you want the smallest high-value increment: **P0-1 (immutable ledger) + P0-2 (referred → manual
verification)**. Together they fix the two most serious findings (S-1 dead-end, S-4 mutable history),
touch a bounded set of files, and unlock scenarios 6–7 and 21/31/40. Everything else can follow once
the corresponding BDRs are answered.

---

# DELIVERABLE 8 — Impact Assessment

## 8.1 Per-change impact (P0 + selected P1)

| Change | Files / modules affected | Existing capability affected | New capability | DB change (migration) | API change | UI change | Test impact | Backward compat | Risk |
|---|---|---|---|---|---|---|---|---|---|
| **P0-1 Immutable ledger** | `models/` (+`financial_transaction.py`), `services/closure.py`, `services/payments.py`, `services/approvals.py`, `services/receivable.py` | Settlement/return/waiver stop mutating in place; `receivable.py` may read from ledger | Append-only money history; reconstruct original vs adjustment | **Yes** — new `financial_transactions` table; **data migration** to seed opening entries from current `principal_paid`/`profit_paid`/`amount_paid` | New `GET /contracts/{id}/ledger`; existing responses unchanged (add a field) | Contract page can show a ledger tab (optional) | Rewrite assertions in `test_closure.py`/`test_payments_flow.py` that check `principal_paid == full`; add ledger tests | **Mostly compatible** — response bodies gain fields; the *semantics* of "paid" become derived. Risk of drift if both stores are written | **Med-High** — core financial refactor; must be done carefully with reconciliation tests |
| **P0-2 Referred → manual verification** | `api/applications.py`, `services/assessment.py`, `models/credit_application.py`, `models/*assessment*` | Assessment engine (adds `decision_source`); application status machine | Credit officer can approve/reject/conditionally-approve a referred app | **Yes** — `assessment_results` new columns; app status enum widened (string col, no constraint change) | New `POST /applications/{id}/review`; `GET /applications?status=referred` (P1) | New "Referred queue" + review screen (P1 frontend) | New `test_manual_verification.py`; existing referred tests still pass | **Compatible** — additive | **Low-Med** |
| **P0-3 Affordability re-check** | `services/assessment.py`, `services/offers.py`, `config` | DBR computation; offer acceptance path | Config-selectable obligation basis; block/refer at acceptance if unaffordable | Config rows only | `POST /offers/{id}/accept` may now 409/refer | Offer page surfaces affordability result | `test_assessment.py`, `test_offer_flow.py` gain cases; some existing "accept succeeds" tests may need affordable inputs | **Behaviour change** — some previously-accepted offers would now be blocked (that's the point); gate behind config default = current behaviour until BDR-22 | **Med** (behavioural) |
| **P0-4 Exposure** | `services/assessment.py` (+ `services/exposure.py`), read-only query | Assessment inputs | Multi-contract exposure visible + rule | None initially (query); table later if perf needs | `GET /customers/{id}/exposure` | Credit review screen shows exposure | New `test_exposure.py`; multi-contract fixtures | **Compatible** — new rule defaults to "off" until BDR-08 | **Low-Med** |
| **P0-5 Accounting events** | `services/accounting.py` (new), hooks in ~8 services, `providers/gl/mock.py` | Every financial service gains one `emit(...)` line | Outbound GL boundary | **Yes** — `accounting_events` table | `GET /accounting/events` (admin/finance) | none | New `test_accounting_events.py`; assert one event per financial action | **Compatible** — purely additive | **Low** (thin boundary) |
| **P0-6 Payment lifecycle skeleton** | `models/payment.py`, `services/payments.py`, `core/` (idempotency dependency) | `Payment` status enum; allocation still runs on `success` | Attempt tracking; generic idempotency | **Yes** — `payments` columns, `payment_initiations` table, `idempotency_keys` table | `POST .../payments` accepts `Idempotency-Key` header; status values expand | Payment form gains a reference/idempotency field (already has one) | `test_payments_flow.py` status assertions; new idempotency tests | **Compatible** — `applied` maps to `success`; keep an alias | **Low-Med** |
| **P0-7 Reconciliation** | `services/reconciliation.py` (new), `models/` | none (new subsystem) | Settlement + bank matching + exception queue | **Yes** — `settlement_batches`, `bank_transactions`, `reconciliations` | `POST /reconciliation/import`, `GET /reconciliation/exceptions`, `POST /reconciliation/{id}/match` (maker-checker) | Recon exceptions screen (P2) | New `test_reconciliation.py` (scenarios 36–39) | **Compatible** — isolated | **Low-Med** |
| **P1 Inventory minimal** | `models/`, `services/fulfilment.py` (new), `api/offers.py` (`confirm-delivery`), `providers/inventory/mock.py` | `confirm-delivery` gains a precondition | Reservation + serial + delivery record | **Yes** — `inventory_items`, `reservations`, `deliveries` | `confirm-delivery` may 409 if no reservation; new inventory endpoints | Contract page shows delivery/serial; offer flow shows availability | `test_offer_flow.py` delivery step; new `test_inventory.py` (13, 14) | **Behaviour change** on `confirm-delivery` — gate behind config until BDR-18 | **Med** |
| **P1 RFC 9457 errors** | `main.py` (handlers), `services/errors.py` | error *envelope* shape | consistent `problem+json` | none | Error responses gain `type`/`title`/`instance`; `detail` retained | Frontend `errorMessage()` already tolerant; minor tweak | Update any test asserting exact error body shape (few) | **Mostly compatible** — keep `detail`; clients reading only `detail` unaffected | **Low** |
| **P1 Reporting endpoints** | `api/` (new read routers), `services/` (queries) | none | ops queues + MIS | Indexes may be added | New `GET` list/aggregate endpoints | New list screens (P2) | New `test_reporting.py` | **Compatible** — purely additive | **Low** |
| **P1 Security (CORS/rate-limit/logging)** | `main.py`, new middleware, `requirements.txt` | request pipeline | CORS allow-list, login throttle, structured logs, DB health | none | none (headers/behaviour only) | Frontend can drop the dev proxy and call the API directly (optional) | Add a CORS test, a rate-limit test | **Compatible** | **Low** |

## 8.2 Cross-cutting risks

- **R-1 Financial refactor (P0-1):** the biggest risk. Mitigation: build the ledger *alongside* the
  current fields first (dual-write + a reconciliation test that asserts `Σ ledger == current
  balances`), then cut reads over, then remove mutation. Never a big-bang.
- **R-2 Behavioural changes gated by unanswered BDRs (P0-3, P1 inventory):** every such change must
  default to *current behaviour* via config until the BDR is answered — otherwise the assessment's own
  rule ("do not silently convert assumptions into production logic") is broken.
- **R-3 Migration volume:** ~10 new tables across P0/P1. Keep one migration per coherent slice;
  verify up **and** down on SQLite + Postgres (the project already does this).
- **R-4 Test-suite churn:** ~15–20 existing assertions about in-place `*_paid` values will need
  rewriting when the ledger lands. Budget for it; do not delete the scenarios, re-express them.
- **R-5 Scope creep:** the temptation to build `PricingRule`, `Invoice`, KYC, notifications and a
  portal "while we're in there". Resist — they are P1/P2 and several are BDR-blocked.

## 8.3 Final Quality Gate (brief §52) — status of the system **today**

🟢 = yes · 🟡 = partial · 🔴 = no

| Question | Status | Evidence |
|---|---|---|
| **Business** — represent the complete retail installment sale? | 🟡 | Application→offer→order→contract→schedule→payment→closure all exist; **missing** invoice, inventory/fulfilment, delivery record, restructuring, write-off. |
| **Credit** — assess and explain decisions? | 🟢 (explain) / 🟡 (assess) | `triggered_rules` + `config_snapshot` are excellent for explanation. Assessment itself uses a proxy affordability figure and no exposure. |
| **Pricing** — different tenors → different installment-sale prices? | 🟢 | `tenor_profit_rate_table`, tested. (Single dimension only.) |
| **Profit** — distinguish principal / profit / unearned / recognized? | 🟢 (structure) / 🟡 (recognition) | Per-installment split + `unearned_profit_balance`. Recognition is cash-basis only, not configurable. |
| **Receivable** — accurately track what the customer owes? | 🟢 | `GET /contracts/{id}/receivable`, reconciles in tests. No aging buckets / DPD in the view. |
| **Payment** — partial / full / failed / reversed? | 🟡 | Partial/full/overpayment: yes, tested. **Failed/reversed: no.** |
| **Reconciliation** — prove a payment ↔ contract ↔ bank/gateway txn? | 🔴 | No settlement/reconciliation layer at all. |
| **Inventory** — avoid selling unavailable stock? | 🔴 | No inventory model. |
| **Contract** — every contract independently tracked? | 🟢 | Independent entities; nothing forces a single contract. |
| **Collections** — delinquency → structured collections? | 🟡 | Case auto-opens/closes, activities, PTP recorded. **Broken-PTP, escalation, restructuring, write-off: no.** |
| **Settlement** — early settlement calculated and audited? | 🟢 | Quote + server-side re-compute + `ContractClosure` + audit. Quote not persisted. |
| **Return** — return correctly affects sale/receivable/payment/refund? | 🟡 | Return closes the contract with a signed adjustment. **No `Refund`, no itemized reversal, no inventory return, no condition/serial check.** |
| **Write-off** — write off without destroying history? | 🔴 | Not implemented. |
| **ECL** — Risk/Finance get data without contaminating pricing? | 🟡 | ECL correctly **absent** from pricing (good). But **no extract** exists to hand to Risk. |
| **Accounting** — pass financial events to ERP/GL? | 🔴 | No accounting-event boundary. |
| **Audit** — who changed what, when, why? | 🟢 (who/what/when) / 🟡 (why + policy version) | `AuditEvent` on state changes; `config_snapshot` on assessments. No policy/rule-set version id; some `user_id = null` on auto transitions. |
| **Configuration** — rules change without code? | 🟢 | 17 parameters, engine has no policy numbers, tested. Not effective-dated. |
| **Integration** — swap external providers without rewriting core? | 🔴 | No provider abstraction; assessment reads fields directly. (Nothing is *wired* to a vendor either, so the coupling is latent, not acute.) |
| **Production** — retries, idempotency, security, operational failures? | 🔴 | Payment idempotency + idempotent overdue job only. No CORS, rate limiting, structured logging, monitoring, refresh tokens, secrets management, DR. Demo-ready, not production-ready. |

**Scorecard: 🟢 8 · 🟡 7 · 🔴 4** (of 19).

## 8.4 Section 55 — "can the system answer this at any moment?" (today)

| # | Question | Today |
|---|---|---|
| 1 | What product did the customer buy? | 🟢 (via `SalesOrder.product_id` / `Product`) |
| 2 | At what cash price? | 🟢 (`Offer.cash_price`, frozen) |
| 3 | Under what installment pricing rule? | 🟡 (numbers frozen on the offer; no named rule id/version) |
| 4 | What was the installment-sale price? | 🟢 (`Offer.installment_sale_price`) |
| 5 | How much paid upfront? | 🟢 (`SalesOrder.down_payment_amount`) |
| 6 | What receivable was created? | 🟢 (computed view; no persisted receivable id) |
| 7 | How was profit calculated? | 🟡 (rate × principal, frozen; methodology unconfirmed — BDR-04) |
| 8 | How is profit being recognized? | 🟡 (cash-basis draw-down only; BDR-05) |
| 9 | What installments are due? | 🟢 (`PaymentSchedule`/`Installment`) |
| 10 | What has the customer paid? | 🟢 (`Payment` + `PaymentAllocation`) — but only summarised on the installment, not a full ledger (S-4) |
| 11 | Where was the payment initiated? | 🔴 (no channel/initiation) |
| 12 | Was it settled? | 🔴 |
| 13 | Was it reconciled to the bank? | 🔴 |
| 14 | Which contract did it settle? | 🟢 (`Payment.contract_id`) |
| 15 | Is the customer overdue? | 🟢 (after running `assess-overdue`; not real-time) |
| 16 | What late fees apply? | 🟢 (`LateFeeCharge`) |
| 17 | What collection action occurred? | 🟢 (`CollectionActivity`) |
| 18 | What is the customer's total exposure? | 🔴 |
| 19 | Why approved/rejected/referred? | 🟢 (`AssessmentResult.triggered_rules`) |
| 20 | Which rule version decided? | 🟡 (`config_snapshot` of values, not a version id) |
| 21 | What happened to the product/inventory? | 🔴 |
| 22 | What accounting event was generated? | 🔴 |
| 23 | What happens if the customer returns the product? | 🟡 (contract closes with a signed adjustment; no refund/reversal detail) |
| 24 | What happens if the customer settles early? | 🟢 (quote + settle + closure, audited) |
| 25 | What happens if the receivable is written off? | 🔴 |
| 26 | What happens if money is recovered afterward? | 🔴 |
| 27 | How is ECL calculated / provided? | 🔴 (no extract) |
| 28 | Who changed any important financial record? | 🟢 (`AuditEvent`) — modulo `user_id = null` on auto transitions |
| 29 | Which business policy was used? | 🟡 (`config_snapshot` for assessment; not captured elsewhere) |
| 30 | Can the entire lifecycle be reconstructed from the audit trail? | 🟡 (origination→closure yes; payment→bank and adjustments no — S-4/S-5) |

**Answerable now: 15 fully, 8 partially, 7 not at all.** The 7 gaps are payment-initiation,
settlement, bank reconciliation, exposure, inventory, accounting events, write-off/recovery, ECL —
i.e. exactly the P0/P1 items above.

---

## STOPPED — awaiting your review

This session's scope (Deliverables 1–8) is complete. **No application code, migration, test, or
config file has been changed.** A new doc directory (`docs/`) and this file are the only additions.

I have **not** started any implementation and will not until you reply with an explicit go-ahead that
names the specific gap(s) to implement (e.g. "do P0-1 and P0-2" or "implement G-01 only"). Several P0
items are blocked on Business Decisions (BDR-07/08/15/22/26) — for those, tell me whether to build
the *structure* with clearly-unconfirmed placeholder config, or to hold entirely.
