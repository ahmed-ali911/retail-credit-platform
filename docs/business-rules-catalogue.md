# Business Rules Catalogue

**Code-verified reference — traced to source, not summarised from memory.**

Produced by reading the actual service/API/model files listed in each row. Every
rule below was confirmed present in the current code (not carried over from an
earlier report). Config values are the **seed defaults** from
[`config/business_rules.yaml`](../config/business_rules.yaml); `ConfigService`
only inserts a key if it is missing, so a running database may hold an
operationally-edited value — the YAML is the version-controlled default and the
authoritative *shape*.

**"Confirmed vs Placeholder"** is taken verbatim from the YAML key comments and
the Business Decision Register in
[`docs/enterprise-assessment.md`](enterprise-assessment.md) (BDR-01 … BDR-42).
This document does not re-litigate those classifications.

Legend for the *Confirmed or Placeholder* column:

- **CONFIRMED** — the YAML comment or BDR marks it a confirmed rule/value.
- **PLACEHOLDER** — fictional/demo value, explicitly "not confirmed policy".
- **STRUCTURE ONLY** — the mechanism is real and confirmed; the *number* or
  *policy* it encodes is still an open BDR.
- **HARD-CODED** — the rule is in code with no config key at all.

---

## 1. Customer creation

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By (file / function) | Triggered Via (endpoint) |
|---|---|---|---|---|---|
| Only `sales_employee` / `admin` may create a customer | role gate | — | HARD-CODED | `app/api/customers.py::create_customer` (`require_roles(sales_employee, admin)`) | `POST /customers` |
| `national_id` is unique | DB `UNIQUE` constraint on `customers.national_id`; `IntegrityError` → **409** | — | HARD-CODED | `app/models/customer.py` (`unique=True`); `app/api/customers.py::create_customer` | `POST /customers` |
| Required fields | `name`, `national_id`, `profile` (object) all required; `profile.monthly_income` required (`>= 0`) | — | HARD-CODED (Pydantic) | `app/schemas/customer.py::CustomerCreate`, `CustomerProfileIn` | `POST /customers` |
| Field bounds | `risk_score` optional, `0 ≤ risk_score ≤ 1000`; `existing_monthly_obligations >= 0` (default 0) | — | HARD-CODED (Pydantic) | `app/schemas/customer.py` (`Field(ge=0, le=1000)`, `Field(ge=0)`) | `POST /customers` |
| `status` default | `active` (enum values are `"Active"` / `"Inactive"`) | — | HARD-CODED | `app/models/customer.py::CustomerStatus`; `app/schemas/customer.py` | `POST /customers` |
| `risk_score` is manually set | no bureau integration; nullable int, set by whoever creates/edits the customer | — | PLACEHOLDER (BDR-09/G-09, KYC) | `app/models/customer.py` (comment: "Stubbed until a real credit-bureau integration exists") | `POST /customers` |
| Customer ↔ login link | `customers.user_id` FK, nullable, "set manually / via helper — no self-service signup" | — | HARD-CODED | `app/models/customer.py` | n/a |

Optional profile fields captured but not validated further: `employer_name`,
`employment_type`, `address_line`, `city`, `contact_phone`. `employment_type` is
a free-text `String(50)`, not an enum.

---

## 2. Product creation

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By (file / function) | Triggered Via (endpoint) |
|---|---|---|---|---|---|
| **Any authenticated user** may create a product | no role gate — only the router-level `Depends(get_current_user)` | — | HARD-CODED (⚠ see Gaps) | `app/api/products.py::create_product` (no `require_roles`); `app/main.py` (`products.router, dependencies=[Depends(get_current_user)]`) | `POST /products` |
| Required fields / bounds | `name` required; `cash_price` required and `> 0`; `category` defaults to `other`; `installment_eligible` defaults `true`; `stock_quantity` optional, `>= 0` | — | HARD-CODED (Pydantic) | `app/schemas/product.py::ProductCreate` | `POST /products` |
| `category` allowed values | `electronics`, `appliances`, `furniture`, `automotive`, `other` | — | HARD-CODED | `app/models/product.py::ProductCategory` | `POST /products` |
| Opening stock when omitted | `stock_quantity` defaults to the config value (fallback constant `10`) | `default_initial_stock_quantity` = **10** | PLACEHOLDER ("BUSINESS DECISION REQUIRED: the real per-product opening counts come from the warehouse / ERP") | `app/api/products.py::create_product` (`ConfigService.get_int(KEY_DEFAULT_INITIAL_STOCK)`); `app/models/product.py::DEFAULT_STOCK_FALLBACK` | `POST /products` |
| `reserved_quantity` | always `0` — never written by any service (kept "for a future reservation-at-offer model") | — | STRUCTURE ONLY (BDR-18) | `app/models/product.py` | n/a |
| `available_quantity` | computed `= stock_quantity − reserved_quantity` (so today `= stock_quantity`) | — | HARD-CODED | `app/models/product.py::available_quantity`; `app/schemas/product.py::ProductOut.available_quantity` | any product read |
| Product single-GET is unauthenticated beyond bearer token | `get_product` has no role gate | — | HARD-CODED | `app/api/products.py::get_product` | `GET /products/{id}` |
| Directory list role | `sales_employee`, `credit_officer`, `credit_manager`, `finance_officer`, `admin`; `status` query accepts only `all` (else 422) | — | HARD-CODED | `app/api/products.py::list_products` | `GET /products?search=&status=&format=` |

---

## 3. Inventory / stock

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By (file / function) | Triggered Via (endpoint) |
|---|---|---|---|---|---|
| Stock **deduction point** | one unit off `product.stock_quantity` at **offer acceptance / contract creation** (not at offer generation, not at delivery). Additive — never blocks contract creation. | — | STRUCTURE ONLY — "working default, not confirmed policy" (BDR-18) | `app/services/offers.py::accept_offer` (`product.stock_quantity = (stock_quantity or 0) - 1`) | `POST /offers/{id}/accept` |
| Stock **release** | +1 back to `product.stock_quantity` on **cancellation** and on **return**. Additive; never blocks the closure. | — | STRUCTURE ONLY (BDR-18) | `app/services/closure.py::_release_stock` (called from `cancel_contract`, `return_contract`) | `POST /contracts/{id}/cancel`, `POST /contracts/{id}/return` |
| Out-of-stock gate at offer generation | `product.available_quantity <= 0` → **422**, no offer generated | — | CONFIRMED (Step 15 — "the one new stock rule") | `app/services/offers.py::generate_offer` | `POST /applications/{id}/offer` |
| Manual stock adjustment | `stock_quantity += delta` (delta may be negative); rejected **422** if the result would fall below `reserved_quantity` | — | HARD-CODED | `app/api/products.py::adjust_stock` | `POST /products/{id}/stock-adjustment` |
| Stock adjustment is **not** maker-checker gated | direct write + `stock_adjustment` audit event | — | Judgment call, not confirmed policy — "Finance may want it gated later" | `app/api/products.py::adjust_stock` (docstring) | `POST /products/{id}/stock-adjustment` |
| Stock adjustment role | `finance_officer`, `admin` | — | HARD-CODED | `app/api/products.py::_STOCK_ADJUST_ROLES` | `POST /products/{id}/stock-adjustment` |

There is **no reservation, warehouse/branch dimension, serial/IMEI, delivery
record, or double-sell prevention beyond the last-unit check at offer
generation.**

---

## 4. Application origination

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By (file / function) | Triggered Via (endpoint) |
|---|---|---|---|---|---|
| Origination role | `sales_employee`, `customer`, `admin` | — | HARD-CODED | `app/api/applications.py::_ORIGINATION_ROLES` | `POST /applications`, `POST /applications/{id}/submit` |
| Customer & product must exist | 404 if `customer_id` / `product_id` unknown | — | HARD-CODED | `app/api/applications.py::create_application` | `POST /applications` |
| Product must be installment-eligible | `product.installment_eligible` false → **422** | — | HARD-CODED | `app/api/applications.py::create_application` | `POST /applications` |
| Field bounds | `requested_amount > 0`; `1 ≤ requested_tenor_months ≤ 120`; `channel` required (`online` \| `branch`) | — | HARD-CODED (Pydantic) | `app/schemas/application.py::ApplicationCreate` | `POST /applications` |
| **Online vs branch channel** | **No behavioural difference.** `channel` is stored on `CreditApplication` and used **only** for reporting (`contracts_by_channel`). No rule, threshold, routing, KYC requirement, or pricing branches on it. | — | STRUCTURE ONLY (see BDR-35 "branch entity needed?") | `app/models/credit_application.py::ApplicationChannel`; only consumer: `app/services/reports.py::contracts_by_channel` | `POST /applications` (stored); `GET /reports/contracts/by-channel` (read) |
| `created_by` | free-text string, defaults `"system"`; "not a real actor link for branch origination" | — | PLACEHOLDER (G-35) | `app/schemas/application.py::ApplicationCreate.created_by` | `POST /applications` |
| Submit precondition | only a `draft` application can be submitted (else **409**) | — | HARD-CODED | `app/api/applications.py::submit_application` | `POST /applications/{id}/submit` |
| Submit lifecycle | `draft → submitted → under_assessment → (approved \| rejected \| referred)` — assessment runs **synchronously** inside the submit call | — | HARD-CODED | `app/api/applications.py::submit_application` → `assess_application` | `POST /applications/{id}/submit` |
| List / review-queue role | `credit_officer`, `credit_manager`, `admin` (deliberately narrow) | — | HARD-CODED | `app/api/applications.py::list_applications` | `GET /applications?status=` |
| Single-application view | staff roles `sales_employee`, `credit_officer`, `credit_manager`, `finance_officer`, `admin`, **or** the owning customer | — | HARD-CODED | `app/api/applications.py::get_application` → `authorize_owner_or_roles` | `GET /applications/{id}` |

---

## 5. Credit Assessment engine

All thresholds read from `ConfigService`. Engine file:
`app/services/assessment.py::assess_application`. Config snapshot is persisted
onto every `AssessmentResult`.

### 5a. Decision precedence

| Rule | Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Precedence | `rejected` (rank 2) **>** `referred` (rank 1) **>** `approved` (rank 0). Final decision = the worst outcome across all four rules. | — | HARD-CODED | `assessment.py` (`_OUTCOME_RANK`, `decision = _RANK_TO_DECISION[max(...)]`) | `POST /applications/{id}/submit` |
| Triggered-rules list | only rules that did **not** pass are recorded; an approved application has an empty list | — | HARD-CODED | `assessment.py` | same |
| Application `status` set = decision | `application.status = ApplicationStatus(decision)` | — | HARD-CODED | `assessment.py` | same |

### 5b. The four rules

| Rule | Current Value / Formula | Config Key(s) | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| **Rule 1 — minimum income** | `monthly_income >= minimum_monthly_income` → pass; otherwise → **rejected**. Missing profile ⇒ income treated as `0.0`. | `minimum_monthly_income` = **300** | PLACEHOLDER | `assessment.py` "Rule 1" | `POST /applications/{id}/submit` |
| **Rule 2 — debt-burden ratio (DBR)** | `dbr = round((existing_monthly_obligations + estimated_installment) / monthly_income, 4)`. `dbr <= maximum_debt_burden_ratio` → pass; `>` → **referred**. `monthly_income <= 0` → **rejected** (cannot compute). | `maximum_debt_burden_ratio` = **0.40** | PLACEHOLDER (BDR-22 for the basis) | `assessment.py` "Rule 2" | same |
| **DBR basis — `estimated_installment`** | `amount_financed = requested_amount × (1 − minimum_down_payment_pct)`. If the requested tenor has a configured rate: `estimated_profit = amount_financed × rate`, `estimated_installment = (amount_financed + estimated_profit) / tenor_months` (`installment_estimate_method = "rate_table"`). If **no** rate for that tenor: fallback `estimated_installment = requested_amount × installment_estimation_factor / tenor_months` (`method = "flat_factor"`). | `minimum_down_payment_pct` = **0.15**; `tenor_profit_rate_table` = `{6:0.04, 12:0.09, 18:0.135, 24:0.18, 36:0.30}`; `installment_estimation_factor` = **1.0** | PLACEHOLDER (P0-3 gave it *structure*; basis is BDR-22) | `assessment.py::estimate_installment` → `pricing.resolve_profit_rate` | same |
| **Rule 3 — risk banding** | `risk_score is None` → **referred**. `>= risk_score_auto_approve_min` → **approved**. `>= risk_score_refer_min` and `< auto_approve_min` → **referred**. `< risk_score_refer_min` → **rejected**. | `risk_score_auto_approve_min` = **650**; `risk_score_refer_min` = **600** | PLACEHOLDER (BDR-24 for the grading scale) | `assessment.py` "Rule 3" | same |
| **Rule 4 — customer exposure limit** | `projected_exposure = round(current_exposure + new_financed_estimate, 2)`. `projected_exposure <= max_customer_exposure_kwd` → pass; `>` → **referred** (never auto-rejects — "same shape as the DBR rule"). | `max_customer_exposure_kwd` = **8000**; `exposure_aggregation_level` = **`company_wide`** | STRUCTURE ONLY — P0-4 done; number + count-cap unconfirmed (BDR-07 / BDR-08) | `assessment.py` "Rule 4" → `exposure_service.compute_exposure` | same |
| **`current_exposure` definition** | sum of `outstanding_principal + outstanding_profit + outstanding_late_fees` across **every non-`closed` contract** of the customer, via the per-contract `build_receivable` (reused, not re-implemented). `created` (pre-delivery) contracts are included. | — | HARD-CODED (aggregation level config-gated) | `app/services/exposure.py::compute_exposure` | same (and `GET /customers/{id}/exposure`) |
| **`new_financed_estimate`** | `= amount_financed_estimate` from the installment estimate = `requested_amount × (1 − minimum_down_payment_pct)` (financed **principal** only, before profit) | `minimum_down_payment_pct` = 0.15 | HARD-CODED | `assessment.py` (`estimate_basis["amount_financed_estimate"]`) | same |
| **Aggregation level guard** | any value other than `"company_wide"` **raises** `ValueError` rather than silently under-counting | `exposure_aggregation_level` | CONFIRMED behaviour (only `company_wide` implemented — BDR-07) | `app/services/exposure.py::compute_exposure` | `POST /applications/{id}/submit`, `GET /customers/{id}/exposure` |

### 5c. Manual review of a `referred` application (fixes S-1)

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Precondition | application must be `referred` (else **409**) | — | HARD-CODED | `app/api/applications.py::review_application` | `POST /applications/{id}/review` |
| Decision → status | `approved → approved`; `rejected → rejected`; `return_for_info → draft` | — | HARD-CODED | `app/api/applications.py::_REVIEW_STATUS` | same |
| Does not touch the engine | writes a **second** `AssessmentResult` with `source = manual`, `reviewed_by`, `notes`; the automated `referred` row is untouched | — | HARD-CODED | `app/api/applications.py::review_application` | same |
| `reason` required | 1–1000 chars | — | HARD-CODED (Pydantic) | `app/schemas/application.py::ReviewRequest` | same |
| Role | `credit_officer`, `credit_manager`, `admin` | — | HARD-CODED | `_REVIEW_ROLES` | same |
| No maker-checker / no approval-authority thresholds by amount | any single eligible reviewer decides; no second approver, no amount bands | — | Open (BDR-25) | `app/api/applications.py` | same |

---

## 6. Offer generation

Service: `app/services/offers.py::generate_offer`. Roles on the endpoint:
`sales_employee`, `credit_officer`, `admin`.

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Application must be `approved` | else **409** | — | HARD-CODED | `generate_offer` | `POST /applications/{id}/offer` |
| Out-of-stock gate | `product.available_quantity <= 0` → **422** | — | CONFIRMED (Step 15) | `generate_offer` | same |
| Tenor resolution | `tenor = payload.tenor_months or application.requested_tenor_months`; `1 ≤ tenor ≤ 120` (Pydantic) | — | HARD-CODED | `generate_offer`; `app/schemas/offer.py::OfferCreate` | same |
| **Tenor → profit rate** | looked up in `tenor_profit_rate_table` by string key; a tenor with **no entry** → `PricingError` → **422** | `tenor_profit_rate_table` = `{"6":0.04, "12":0.09, "18":0.135, "24":0.18, "36":0.30}` | PLACEHOLDER — "invented demo numbers" (BDR-02 / BDR-03) | `app/services/pricing.py::resolve_profit_rate` | same |
| **Minimum down payment** | `min_down_payment = round(cash_price × minimum_down_payment_pct, 2)`; `down_payment_amount < min_down_payment` → **422** | `minimum_down_payment_pct` = **0.15** | PLACEHOLDER (BDR-06) | `generate_offer` | same |
| **Pricing formulas** | `principal_financed = cash_price − down_payment`; `total_profit = round(principal_financed × profit_rate, 2)` (whole-of-term, flat); `installment_sale_price = cash_price + total_profit`; `amount_financed = principal_financed + total_profit` | `tenor_profit_rate_table` | PLACEHOLDER methodology "flat `rate × principal_financed`" (BDR-03 / BDR-04) | `app/services/pricing.py::build_plan` | same |
| **Amortization shape** | principal repaid in equal monthly amounts (straight-line); **profit recognised declining-balance** — installment `i` of `N` carries profit weight `N − i + 1`; cumulative rounding, final installment absorbs residual so the schedule sums exactly to `principal_financed` / `total_profit` | — | Amortization *shape* fixed in code; recognition method is BDR-05 ("Finance/audit sign-off mandatory") | `app/services/pricing.py::build_plan` | same |
| `down_payment < 0` or `>= cash_price` | `PricingError` → **422** | — | HARD-CODED | `pricing.build_plan` | same |
| **Offer-time affordability re-check** | recompute against the **real priced schedule**: `peak_installment = max(line.total for line in schedule)` (profit is front-loaded, so normally installment 1). `dbr = round((obligations + peak) / income, 4)`; `affordable = dbr <= maximum_debt_burden_ratio`. Always persists an `AssessmentResult` with `source = offer_affordability_recheck`. | `maximum_debt_burden_ratio` = **0.40**; `offer_affordability_gate_mode` = **`block`** | STRUCTURE ONLY — P0-3 done; policy (basis, action) unconfirmed (BDR-22) | `app/services/offers.py::_affordability_recheck` | same |
| **Failed re-check action** | `gate_mode == "block"` (default) → refuse, **422** (`AffordabilityBlocked`), plus `offer.blocked_unaffordable` audit event. `gate_mode == "warn_only"` → offer still generated, the failed re-check `AssessmentResult` is kept. | `offer_affordability_gate_mode` — `block` \| `warn_only` | PLACEHOLDER default (`block` = "safe"); "route to referral instead" is NOT built (BDR-22) | `generate_offer` (`if not recheck["affordable"] and gate_mode == "block"`) | same |
| Supersede prior offers | any still-`presented` offer for the same application is set `expired` | — | HARD-CODED | `generate_offer` | same |
| **Offer validity** | `valid_until = now + offer_validity_days` | `offer_validity_days` = **7** | PLACEHOLDER | `generate_offer` | same |
| ⚠ naming note | there is **also** a `settlement_quote_validity_days` (=3) — different key, used at closure, not here | — | — | — | — |

---

## 7. Offer acceptance → Contract + Schedule creation

Service: `app/services/offers.py::accept_offer`. Endpoint roles:
`sales_employee`, `customer`, `admin`.

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Not already accepted | `offer.status == accepted` → **409** | — | HARD-CODED | `accept_offer` | `POST /offers/{id}/accept` |
| Not expired | `offer.status == expired` **or** `now > valid_until` → set `expired`, **409** | — | HARD-CODED | `accept_offer::_is_expired` | same |
| Down payment must be confirmed | `down_payment_confirmed` must be `true` (default `false`) — otherwise offer stays `presented`, nothing created, **422** | — | HARD-CODED | `accept_offer` | same |
| Optional down-payment cross-check | if `down_payment_amount` supplied and `!= offer.down_payment` → **422** | — | HARD-CODED | `accept_offer` | same |
| Down-payment collection | **stubbed** — `down_payment_reference` is stored as free text; no gateway, no actual money movement | — | PLACEHOLDER (G-05) | `accept_offer` | same |
| Contract created | `InstallmentContract(status=created, tenor_months, total_profit, unearned_profit_balance = total_profit)` + one `SalesOrder` + one `PaymentSchedule` | — | HARD-CODED | `accept_offer` | same |
| **Schedule due dates** | `base_date = now (UTC date)`; installment `i` due `add_months(base_date, i)` — day clamped to month length. **Anchored to wall-clock "today" at acceptance.** | — | HARD-CODED | `accept_offer`; `app/core/dates.py::add_months` | same |
| Installment components frozen | copied verbatim from `offer.schedule_preview` (the frozen pricing snapshot) | — | HARD-CODED | `accept_offer` | same |
| Stock deduction | −1 `product.stock_quantity` here (see §3) | — | STRUCTURE ONLY (BDR-18) | `accept_offer` | same |

---

## 8. Contract delivery

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Precondition | contract must be `created` (else **409**) | — | HARD-CODED | `app/services/offers.py::confirm_delivery` | `POST /contracts/{id}/confirm-delivery` |
| Effect | `status = active`, `activated_at = now`; the schedule is now "live" for overdue/payment | — | HARD-CODED | `confirm_delivery` | same |
| Accounting events emitted | `contract_activated` (amount = `sales_order.sale_price`) **and** `down_payment_received` (amount = `sales_order.down_payment_amount`), both dated `activated_at` | — | Boundary confirmed; CoA mapping BDR-31 | `confirm_delivery` → `accounting.emit` | same |
| Delivery is a bare status flip | no serial/IMEI, no delivery record, no signature | — | PLACEHOLDER (G-08) | `confirm_delivery` | same |
| Role | `sales_employee`, `admin` | — | HARD-CODED | `app/api/offers.py::confirm_delivery` | same |

---

## 9. Payment recording & allocation

Service: `app/services/payments.py::record_payment`. Endpoint roles:
`sales_employee`, `finance_officer`, `customer`, `admin` (⚠ **no owner check** —
see Gaps). Allocation engine: `app/services/allocation.py::allocate` (pure).

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| **Idempotency** | `external_reference` unique per contract (`uq_payment_ref`). A replay returns the original `Payment` + allocation, `replayed = true`, no re-processing, no new audit event. | — | CONFIRMED | `record_payment` (early `SELECT`); `app/models/payment.py::Payment.__table_args__` | `POST /contracts/{id}/payments` |
| `external_reference` required | non-empty after strip; 1–100 chars | — | HARD-CODED | `record_payment`; `app/schemas/payment.py::PaymentCreate` | same |
| Contract must be `active` | else **409** | — | HARD-CODED | `record_payment` | same |
| `amount > 0` | `amount_dec <= 0` → **422** | — | HARD-CODED (Pydantic `gt=0` + service) | `record_payment` | same |
| **Overpayment rejection** (P0 fix) | `total_outstanding = outstanding_receivable (principal + profit) + outstanding_late_fees`. `amount > total_outstanding` → **422**, error states the correct outstanding amount. No excess is accepted as a credit balance. | — | CONFIRMED (recent P0 fix); credit-balance handling explicitly out of scope | `record_payment` (`if amount_dec > total_outstanding`) → `build_receivable` | same |
| **Allocation Rule 1 — oldest installment first** | installments processed in ascending `sequence_number`; only those with any outstanding (principal, profit, or late fee) are "live". The oldest installment is fully settled — including its *principal* — before **any** money reaches a newer installment's late fee/profit. | — | CONFIRMED shape; making it config-driven is BDR-10 / G-13 | `allocation.py::allocate`; `payments.py::_outstanding_installments` | same |
| **Allocation Rule 2 — within an installment: Late Fee → Profit → Principal** | for each live installment, in order: pay outstanding late fee, then outstanding profit, then outstanding principal, taking `min(remaining, owed)` each step | — | CONFIRMED shape (BDR-10) | `allocation.py::allocate` (bucket loop) | same |
| Late fee sub-allocation | within an installment, late fees paid oldest-`assessed_at` first; `waived` charges skipped; a charge flips to `paid` once `amount_paid >= amount` | — | HARD-CODED | `payments.py::_apply_late_fee` | same |
| Profit recognition on payment | `installment.profit_paid += line.profit`; `contract.unearned_profit_balance -= line.profit` (cash-basis recognition) | — | Recognition method is BDR-05 ("do not pick") | `record_payment` | same |
| Installment status transition | fully paid (principal & profit outstanding `<= 0`) → `paid`; a partial payment on an `overdue` installment **stays `overdue`**; otherwise any payment → `partially_paid` | — | HARD-CODED | `payments.py::_update_installment_status` | same |
| Overpaid status | if `plan.unallocated_amount > 0` the payment is `overpaid` — but this is now **unreachable in normal flow** because overpayment is rejected first; retained as defense-in-depth | — | HARD-CODED | `record_payment`; `app/models/payment.py::PaymentStatus` | same |
| Ledger dual-write | one `LedgerEntry` per non-zero bucket per allocation line: `late_fee_paid`, `profit_recognized`, `principal_paid`, `related_action = payment` | — | CONFIRMED (P0-1, write-only phase) | `record_payment` → `ledger_service.record_entry` | same |
| Accounting events | `payment_received` (full `payment.amount`); `profit_recognized` (only the profit portion this allocation recognised) if `> 0` | — | Boundary confirmed; CoA BDR-31 | `record_payment` → `accounting.emit` | same |
| Collections hook | `close_case_if_cleared` runs after allocation (see §11) | — | CONFIRMED | `record_payment` → `collections_service.close_case_if_cleared` | same |
| **Receivable definition** | `outstanding_receivable = outstanding_principal + outstanding_profit` — **outstanding late fees are a separate line, never merged in** | — | CONFIRMED (Step 3 open-decision note) | `app/services/receivable.py::build_receivable` | `GET /contracts/{id}/receivable` |
| Receivable view role | `finance_officer`, `credit_manager`, `admin`, **or** the owning customer | — | HARD-CODED | `app/api/payments.py::get_receivable` → `authorize_owner_or_roles` | `GET /contracts/{id}/receivable` |

---

## 10. Auto-close on zero balance (fixes S-10)

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Trigger | runs **once per payment**, only inside `record_payment`, after allocation | — | CONFIRMED (recent P0 fix) | `app/services/closure.py::close_if_fully_repaid` (called from `payments.py::record_payment`) | `POST /contracts/{id}/payments` |
| Condition | contract is `active` **and** has no `ContractClosure` **and** every installment `status == paid` **and** `outstanding_receivable == 0` **and** `outstanding_late_fees == 0` | — | CONFIRMED | `close_if_fully_repaid` | same |
| Effect | one `ContractClosure(reason = normal, financial_adjustment = 0)`; `contract.status = closed`; `contract_closed` accounting event | — | CONFIRMED | `close_if_fully_repaid` | same |
| Idempotent / non-retroactive | no-op if already closed or not `active`; only the payment that actually completes the schedule can create the one allowed closure | — | CONFIRMED | `close_if_fully_repaid` | same |
| Audit | `contract.closed` audit event written by the API layer when `outcome.closure` is set | — | HARD-CODED | `app/api/payments.py::record_payment` | same |

There is still **no scheduled maturity job** — a contract only auto-closes if a
payment lands exactly on zero.

---

## 11. Overdue handling & late fees

Service: `app/services/overdue.py::assess_overdue`. Endpoint role: **`admin` only**.
Not a scheduler — manual trigger (or `as_of` override for simulation).

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Scope | installments where `due_date < as_of` **and** `status != paid` **and** the contract is `active` | — | HARD-CODED | `assess_overdue` (query) | `POST /jobs/assess-overdue` |
| `as_of` default | `now (UTC date)`; request may pass an explicit `as_of` | — | HARD-CODED | `assess_overdue` | same |
| **DPD calculation** | `dpd = (as_of − installment.due_date).days` (an installment due exactly on `as_of` is **not** in scope, so `dpd >= 1` for every processed row) | — | HARD-CODED | `assess_overdue` | same |
| Mark overdue | if not fully paid and not already `overdue` → `status = overdue`, counts once | — | HARD-CODED | `assess_overdue` | same |
| **Late-fee grace** | a fee is created only when `dpd > late_fee_grace_period_days` (strictly greater — `dpd == grace` gets **no** fee) | `late_fee_grace_period_days` = **10** | PLACEHOLDER — "open decision" | `assess_overdue` (`if dpd > grace_days and not already_charged`) | same |
| **Late-fee amount** | `fee = round(late_fee_rate × (installment.principal_component + installment.profit_component), 2)` — 2% of the installment's **own scheduled total** (principal + profit), **not** the overdue balance, **not** including any prior late fee | `late_fee_rate` = **0.02** | **CONFIRMED BUSINESS RULE** — "not a placeholder … the agreed value is 0.02" | `assess_overdue` | same |
| **Once per installment** | a fee is created only if `len(installment.late_fee_charges) == 0` — i.e. **any** existing charge (including a `waived` one) blocks a new one. `late_fee_once_per_installment` is read only to assert the supported mode; the flag's value has **no effect** (recurring re-charge is not built). | `late_fee_once_per_installment` = **true** (inert) | PLACEHOLDER — recurring re-charge "intentionally NOT implemented" | `assess_overdue` (`already_charged`) | same |
| **Max fees per contract** | **NOT WIRED UP** — `late_fee_max_per_contract` is never read | `late_fee_max_per_contract` = **0** ("0 = no cap") | PLACEHOLDER — "NOT WIRED UP … it exists so the parameter name is reserved" | — (no enforcement anywhere) | — |
| Late fee is not profit | separate `LateFeeCharge` table; never folded into `profit_component` / `unearned_profit_balance` / the Receivable | — | CONFIRMED | `app/models/payment.py::LateFeeCharge` (docstring) | — |
| Accounting event | one `late_fee_charged` per new charge (amount = fee, dated `assessed_at`) | — | Boundary confirmed; CoA BDR-31 | `assess_overdue` → `accounting.emit` | same |
| Audit | `overdue.assessed` (job summary) + one `late_fee.assessed` per charge | — | HARD-CODED | `app/api/payments.py::assess_overdue` | same |
| **DPD action bands** | `dpd_report_buckets` = `[[1,30],[31,60],[61,90],[91,null]]` is a **reporting-display grouping only** for the Portfolio dashboard aging table — **not** a collections-action policy | `dpd_report_buckets` | PLACEHOLDER — "DISPLAY GROUPING ONLY … NOT a collections policy" (BDR-32) | `app/services/reports.py` (aging report only) | `GET /reports/aging` |

---

## 12. Collections

Service: `app/services/collections.py`. Case lifecycle is **automatic**;
logging activities is a plain operational action.

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| **Case auto-open** | when overdue assessment first marks an installment `overdue` on a contract that has **no open case** → open one `CollectionCase(status=open)`, `opened_reason` = the first overdue installment. Idempotent (skips if an open case exists). | — | CONFIRMED (automatic hook) | `collections.py::open_case_if_needed`, called from `overdue.py::assess_overdue` | `POST /jobs/assess-overdue` |
| **Case auto-close** | after a payment allocation, if the contract has **no installment in `overdue` status**, the open case → `closed`, `closed_at = now` | — | CONFIRMED (automatic hook) | `collections.py::close_case_if_cleared`, called from `payments.py::record_payment` | `POST /contracts/{id}/payments` |
| ⚠ close condition subtlety | close checks only `status == overdue`. A **partially-paid overdue** installment stays `overdue` (per §9), so the case stays open until it's fully cleared. | — | HARD-CODED | `collections.py::_has_overdue` | same |
| Log activity — types | `call`, `sms`, `email`, `visit`, `promise_to_pay`, `other` | — | HARD-CODED | `app/models/collections.py::CollectionActivityType` | `POST /collections/cases/{id}/activities` |
| **Promise-to-pay required fields** | `promise_to_pay` activity → `promised_amount` **and** `promised_date` both required (**422** if missing); `promise_status` set to `pending`. For every other type these three fields are forced to `null`. | — | HARD-CODED | `collections.py::log_activity` | same |
| **Promise-to-pay outcome (kept/broken)** | **NOT IMPLEMENTED.** `promise_status` is only ever `pending` — nothing transitions it to `kept` or `broken`. Reports/dashboard count `kept`/`broken` but the counts are always 0. | — | Open (BDR-15 / G-15 — "PTP-evaluation step" not built) | `promise_status` set once in `log_activity`; no other writer exists | — |
| No escalation / restructure / legal / write-off / recovery | none of these exist | — | Open (BDR-11 / 12 / 13, G-16 / 17) | — | — |
| Case list role | `collections_officer`, `credit_manager`, `admin` | — | HARD-CODED | `app/api/collections.py::_VIEW_ROLES` | `GET /collections/cases` |
| Case detail | staff view roles **or** the owning customer | — | HARD-CODED | `app/api/collections.py::get_case` → `authorize_owner_or_roles` | `GET /collections/cases/{id}` |
| Log-activity role | `collections_officer`, `admin` | — | HARD-CODED | `app/api/collections.py::_ACT_ROLES` | `POST /collections/cases/{id}/activities` |

---

## 13. Early settlement

Service: `app/services/closure.py`. Endpoint role for `/settle`:
`finance_officer`, `credit_manager`, `admin`. `/settlement-quote` uses
`authorize_owner_or_roles` (same staff roles **or** the owning customer).

### 13a. Settlement quote

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Precondition | contract must be `active` and not `closed` (else **409**) | — | HARD-CODED | `build_settlement_quote::_guard_not_closed` + status check | `GET /contracts/{id}/settlement-quote` |
| `outstanding_principal` | `Σ installment.principal_outstanding` | — | HARD-CODED | `build_settlement_quote` | same |
| `outstanding_late_fees` | `Σ late_fee_charge.outstanding` (waived = 0) | — | HARD-CODED | same | same |
| `unearned_profit_total` | `contract.unearned_profit_balance` (2-dp) | — | HARD-CODED | same | same |
| **Default profit rebate** | `profit_rebate_amount = round(unearned_profit_total × early_settlement_profit_rebate_pct, 2)` when no rebate is requested | `early_settlement_profit_rebate_pct` = **0.0** | **CONFIRMED** — BDR item #7: default 0% = "no automatic rebate" | `build_settlement_quote` (`else` branch) | same |
| **Staff-requested rebate — by %** | `?requested_rebate_pct` in `[0,1]` (else 422); `profit_rebate_amount = round(unearned_profit_total × pct, 2)` | — | CONFIRMED mechanism (BDR item #7) | `build_settlement_quote` | same |
| **Staff-requested rebate — by amount** | `?requested_rebate_amount` in `[0, unearned_profit_total]` (else 422); `profit_rebate_amount = round(amount, 2)`; derived `pct = amount / unearned` (or 0 if unearned = 0) | — | CONFIRMED mechanism | `build_settlement_quote` | same |
| Both params at once | supplying `requested_rebate_pct` **and** `requested_rebate_amount` → **422** | — | CONFIRMED | `build_settlement_quote` | same |
| **`is_deviation` flag** | `true` when `profit_rebate_amount != round(unearned_profit_total × config_default_pct, 2)` — i.e. the effective rebate differs from the config default. Drives the maker-checker gate on `/settle`. | `early_settlement_profit_rebate_pct` | CONFIRMED (BDR item #7) | `build_settlement_quote` | same |
| `profit_still_charged` | `unearned_profit_total − profit_rebate_amount` | — | HARD-CODED | same | same |
| `final_payoff_amount` | `round(outstanding_principal,2) + round(outstanding_late_fees,2) + profit_still_charged` | — | HARD-CODED | same | same |
| `quote_expiry` | `now + settlement_quote_validity_days` — **informational only** (`/settle` always recomputes) | `settlement_quote_validity_days` = **3** | PLACEHOLDER | `build_settlement_quote` | same |

### 13b. Settlement execution + maker-checker gate

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| **Quote always recomputed server-side** | `/settle` (and the approval executor) rebuild the quote with the requested rebate and reject if `round(amount,2) != final_payoff_amount` → **422** ("fetch a fresh settlement quote"). A stale client-held amount never passes. | — | CONFIRMED (preserved safeguard) | `closure.py::settle_contract`; `api/closure.py::settle` | `POST /contracts/{id}/settle` |
| **Non-deviation → settle immediately** | `is_deviation == false` (no rebate, or a rebate equal to the config default) → execute now, exactly as before: `_close_out_schedule`, `ContractClosure(reason=early_settlement, financial_adjustment=NULL)`, `contract.status=closed`, `Payment(status=applied)` for the payoff, response `status = "closed"` | — | CONFIRMED | `api/closure.py::settle` | same |
| **Deviation → maker-checker** | `is_deviation == true` → **do not execute**. Create a pending `ApprovalRequest(action_type = "contract.settlement_rebate", entity = installment_contract:{id})` with payload `{requested_rebate_pct, requested_rebate_amount, external_reference, quoted_*}`. Response `status = "pending_approval"`, `closure = null`, `pending_approval = {…}`. | — | **CONFIRMED — BDR item #7** ("waiving real profit should require a second approver *every time it happens*") | `api/closure.py::settle` | same |
| One pending request per contract | a second `/settle` while a `contract.settlement_rebate` request is pending → **409** | — | HARD-CODED | `api/closure.py::settle` → `approval_service.pending_request_for` | same |
| Requester ≠ approver | `decided_by == requested_by` → **409** (generic maker-checker rule) | — | CONFIRMED | `approvals.py::decide` | `POST /approvals/{id}/approve` |
| **On approval** | quote recomputed with the stored rebate; `settle_contract` runs → all normal hooks fire: ledger (`principal_paid`, `late_fee_paid`, `profit_recognized`, `profit_rebated`, `related_action=settlement`), `early_settlement` accounting event, `contract.settled` audit event (now carrying `approval_request_id`) | — | CONFIRMED | `approvals.py::_execute` (`ACTION_SETTLEMENT_REBATE` branch) → `closure.settle_contract` | `POST /approvals/{id}/approve` |
| Schedule close-out | `_close_out_schedule`: every installment → `principal_paid = principal_component`, `profit_paid = profit_component`, `status = paid`; non-waived late fees → `paid`; `unearned_profit_balance = 0` | — | HARD-CODED | `closure.py::settle_contract` → `_close_out_schedule` | same |
| Ledger `profit_rebated` | records the waived amount as a separate positive entry (fixes S-4 — "profit 12.46 scheduled → X charged, Y rebated" is reconstructable) | — | CONFIRMED (P0-1) | `closure.py::settle_contract` | same |

---

## 14. Cancellation (pre-delivery)

Service: `app/services/closure.py::cancel_contract`. Endpoint role:
`finance_officer`, `credit_manager`, `admin`.

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Precondition | contract must be `created`. If `active` → **409** pointing at `/return`. Any other status → **409**. `closed` → **409** (already closed). | — | HARD-CODED | `cancel_contract` | `POST /contracts/{id}/cancel` |
| **Down-payment refund** | `refund = round(down_payment_amount × down_payment_refund_pct_cancellation, 2)` — a **flat, unconditional** percentage. No restocking fee, no condition check. | `down_payment_refund_pct_cancellation` = **1.0** (full refund) | PLACEHOLDER — "NOT CONFIRMED POLICY" (BDR-19) | `cancel_contract` | same |
| `financial_adjustment` | `= +refund` (positive = cash owed to the customer) | — | HARD-CODED (sign convention) | `cancel_contract` | same |
| Effects | `unearned_profit_balance = 0`; `ContractClosure(reason=cancellation)`; `status = closed`; stock released (+1); `cancellation` accounting event (amount = signed `financial_adjustment`); ledger `refund_issued` if `refund > 0` (`related_action = cancellation`) | — | HARD-CODED / boundary-confirmed | `cancel_contract` | same |
| Optional `notes` | free text overrides the auto-generated note | — | HARD-CODED | `cancel_contract` | same |

---

## 15. Return (post-delivery)

Service: `app/services/closure.py::return_contract`. Endpoint role:
`finance_officer`, `credit_manager`, `admin`.

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Precondition | contract must **not** be `created` (→ **409** pointing at `/cancel`) and not `closed` (→ **409**). In practice: `active`. | — | HARD-CODED | `return_contract` | `POST /contracts/{id}/return` |
| Payoff side | reuses `build_settlement_quote` **with no rebate** (config default 0%) for the principal / profit-still-charged / late-fee figure | `early_settlement_profit_rebate_pct` = 0.0 | HARD-CODED (reuse) | `return_contract` | same |
| **Down-payment refund on return** | `down_payment_refund = round(down_payment_amount × down_payment_refund_pct_return, 2)` — **flat, unconditional** | `down_payment_refund_pct_return` = **0.0** (no DP refund) | PLACEHOLDER — "NOT CONFIRMED POLICY" (BDR-19) | `return_contract` | same |
| **Net adjustment** | `net_adjustment = down_payment_refund − quote.final_payoff_amount` — signed from the customer's POV (`> 0` refund to customer, `< 0` customer still owes). With defaults this is `0 − payoff` = negative. | `down_payment_refund_pct_return`, `early_settlement_profit_rebate_pct` | STRUCTURE ONLY — "single signed `financial_adjustment`" is the whole model; itemized reversal / restocking fee / condition check / `Refund` entity / inventory-return record are **not built** (BDR-19, G-19) | `return_contract` | same |
| `ownership_transfers_on_delivery` | echoed back in the response only — **no logic branches on it** | `ownership_transfers_on_delivery` = **true** | PLACEHOLDER — "NOT A LEGAL POSITION" (BDR-01) | `return_contract` (only put in the response/notes) | same |
| Effects | `_close_out_schedule` (all installments → paid, `unearned_profit_balance = 0`); `ContractClosure(reason=return, financial_adjustment = net_adjustment)`; `status = closed`; stock released (+1); `return` accounting event; ledger `refund_issued` (signed `net_adjustment`, `related_action = return`) if `!= 0` | — | HARD-CODED / boundary-confirmed | `return_contract` | same |
| No maker-checker on return | any single eligible staffer executes it immediately | — | Open (return policy is still open per the prompt) | `api/closure.py::return_contract` | same |

**Return refund model is still flat/unconditional** — the profit-return
treatment, restocking fee, condition/serial verification, and DP forfeiture rules
are all open (BDR-19).

---

## 16. Reconciliation

Service: `app/services/reconciliation.py`. Ingest/run role: `finance_officer`,
`admin`. This layer only **observes** — it never blocks or alters a payment.

| Rule | Current Value / Formula | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Eligible payments | only `reconciliation_status == unreconciled` — makes matching naturally idempotent | — | HARD-CODED | `_classify` | `POST /reconciliation/run` |
| Lines processed | only `BankStatementLine` rows with no `matched_payment_id` **and** no existing exception; processed in `id` order | — | HARD-CODED | `run_matching` | same |
| **Match Rule 1 — exact reference** | `line.bank_reference` ∈ `{payment.external_reference, payment.gateway_reference}`. Exactly 1 hit **and amounts equal** → auto-match (`via = auto`). Exactly 1 hit, amount differs → `ReconciliationException(reason = amount_mismatch)` and that payment → `reconciliation_status = exception`. `> 1` hit → `reason = duplicate_candidate`. | — | STRUCTURE ONLY (BDR-26) | `_classify` (Rule 1) | same |
| **Match Rule 2 — amount + value date** (fallback, only if no reference hit) | payments where `amount == line.amount` **and** `abs(payment.received_at.date() − line.value_date) <= reconciliation_date_tolerance_days`. Exactly 1 → match (`via = auto`). `> 1` → `reason = duplicate_candidate`. | `reconciliation_date_tolerance_days` = **0** (same calendar day) | PLACEHOLDER — "Raise it once real settlement-timing (T+N) behaviour is known" (BDR-26) | `_classify` (Rule 2) | same |
| **Match Rule 3 — nothing** | no candidate → `ReconciliationException(reason = no_match)` | — | HARD-CODED | `_classify` (Rule 3) | same |
| On auto-match | `line.matched_payment_id = payment.id`; `payment.reconciliation_status = reconciled`; `payment.reconciled` audit event | — | HARD-CODED | `_link` | same |
| Idempotency of re-run | a line is "done" once matched **or** it has an exception → re-running never re-matches or duplicates an exception | — | CONFIRMED | `run_matching` | same |
| **Manual match** | via generic maker-checker (`action_type = reconciliation.manual_match`). Request roles: `finance_officer`, `credit_manager`, `admin`. Guards: exception must be `open` (409), payment must exist (404) and not already `reconciled` (409), no pending request already (409). On approval by a different user → link the line, payment → `reconciled`, exception → `resolved`. | — | CONFIRMED mechanism | `api/reconciliation.py::request_manual_match`; `reconciliation.py::apply_manual_match` (via `approvals._execute`) | `POST /reconciliation/exceptions/{id}/request-match` → `POST /approvals/{id}/approve` |
| **Excel bulk upload** | `.xlsx` only (else 422); required columns `bank_reference`, `amount`, `value_date` (any order, case-insensitive header, extra columns ignored). A missing required column → **422 for the whole file**. A bad row (empty ref / non-numeric amount / `amount <= 0` / unparseable date) is **skipped and reported with its row number and reason** — never silently dropped. Each good row → the same `ingest_bank_line`, then `run_matching` once. Role: `finance_officer`, `admin`. | — | CONFIRMED (Step 15) — column layout is documented, real bank feed still absent | `reconciliation.py::ingest_bank_statement_upload`, `parse_bank_statement_workbook`; `api/reconciliation.py::upload_bank_statement` | `POST /reconciliation/bank-lines/upload` |
| No real bank feed | matching runs only on manually-imported / uploaded lines | — | PLACEHOLDER (G-05 / G-06) | — | — |

---

## 17. Accounting events

Emitter: `app/services/accounting.py::emit` — **additive**, changes nothing else,
**idempotent** on `event_reference` (firing a hook twice is a no-op).

| Action | Event type(s) emitted | `event_reference` pattern | `amount` | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Delivery confirmation | `contract_activated`, `down_payment_received` | `contract-activated-{cid}`, `down-payment-received-{cid}` | sale price; down payment | `offers.py::confirm_delivery` | `POST /contracts/{id}/confirm-delivery` |
| Payment recorded | `payment_received`; `profit_recognized` (if profit recognised `> 0`) | `payment-received-{pid}`, `profit-recognized-{pid}` | full payment amount; profit portion | `payments.py::record_payment` | `POST /contracts/{id}/payments` |
| Late fee assessed | `late_fee_charged` | `late-fee-charged-{charge_id}` | fee amount | `overdue.py::assess_overdue` | `POST /jobs/assess-overdue` |
| Late fee waived (on approval) | `late_fee_waived` | `late-fee-waived-{charge_id}` | waived amount | `approvals.py::_execute` (`ACTION_LATE_FEE_WAIVE`) | `POST /approvals/{id}/approve` |
| Early settlement | `early_settlement` | `early-settlement-{closure_id}` | `0.00` (payoff collected via `/settle`; detail on the Payment + ledger) | `closure.py::settle_contract` | `POST /contracts/{id}/settle` or approval |
| Cancellation | `cancellation` | `cancellation-{closure_id}` | signed `financial_adjustment` (= +refund) | `closure.py::cancel_contract` | `POST /contracts/{id}/cancel` |
| Return | `return` | `return-{closure_id}` | signed `financial_adjustment` (`net_adjustment`) | `closure.py::return_contract` | `POST /contracts/{id}/return` |
| Normal (maturity) closure | `contract_closed` | `normal-closure-{closure_id}` | `0.00` | `closure.py::close_if_fully_repaid` | `POST /contracts/{id}/payments` |

| Rule | Detail | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|
| Posting job | walks every event **not** `posted`, hands each to the mock `GlProvider`, records `posted` + `external_gl_reference` or `failed` + `retry_count`. On-demand (not a scheduler). Idempotent. Role: `admin`. | STRUCTURE ONLY | `accounting.py::post_pending`; `erp_adapter.py::MockGlProvider` (always `ok=True`) | `POST /jobs/post-accounting-events` |
| GL debit/credit mapping | **not stored / not computed** — one signed `amount` per event; the double-entry split is deferred to a real `GlProvider` | Open — **BDR-31** | — | — |
| View role | `finance_officer`, `admin` | HARD-CODED | `api/accounting.py::_VIEW_ROLES` | `GET /accounting/events` |

---

## 18. Maker-checker approvals

Generic entity: `ApprovalRequest` (`action_type`, `entity_type`, `entity_id`,
`requested_by`, `payload` JSON, `status`, `decided_by`, `decided_at`,
`decision_notes`). Service: `app/services/approvals.py`.

| Rule | Detail | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|
| **Core rule: requester ≠ approver** | `decided_by == requested_by` → **409**, whatever the role (including `admin`). Enforced in the service layer, not by convention. | **CONFIRMED** | `approvals.py::decide` | `POST /approvals/{id}/approve` \| `/reject` |
| Only a pending request can be decided | already `approved` / `rejected` → **409** | HARD-CODED | `approvals.py::decide` | same |
| Decision executes on approve only | `_execute` runs only when `approve == true`; reject changes nothing | HARD-CODED | `approvals.py::decide` | same |
| Decider role | `finance_officer`, `credit_manager`, `admin` | HARD-CODED | `api/approvals.py::_DECIDE_ROLES` | `GET /approvals`, `POST /approvals/{id}/approve` \| `/reject` |

### Action types that require maker-checker

| `action_type` | Constant | Requested via | Request roles | On approval | One-pending guard |
|---|---|---|---|---|---|
| `late_fee.waive` | `ACTION_LATE_FEE_WAIVE` | `POST /late-fees/{id}/request-waiver` (`reason` required) | `finance_officer`, `credit_manager`, `admin` | `LateFeeCharge.status = waived`; ledger `late_fee_waived`; `late_fee_waived` accounting event | yes (409) — charge must be `assessed` |
| `config.update` | `ACTION_CONFIG_UPDATE` | `PUT /config/parameters/{key}` → **202** pending | `admin` only | `ConfigService.set(key, new_value)`; `config.updated` audit event | yes (409) per key |
| `reconciliation.manual_match` | `ACTION_RECON_MANUAL_MATCH` | `POST /reconciliation/exceptions/{id}/request-match` (`payment_id`, `reason`) | `finance_officer`, `credit_manager`, `admin` | link bank line ↔ payment; payment → `reconciled`; exception → `resolved` | yes (409) per exception |
| `contract.settlement_rebate` | `ACTION_SETTLEMENT_REBATE` | `POST /contracts/{id}/settle` **when the quote is a deviation** | `finance_officer`, `credit_manager`, `admin` | recompute quote server-side; `settle_contract` runs (all settlement hooks); `contract.settled` audit event with `approval_request_id` | yes (409) per contract |

Enforced by: `app/services/approvals.py::_execute` (one branch per `action_type`;
an unknown type → **409** "don't know how to execute").

---

## 19. Config management

| Rule | Detail | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Storage | DB table `config_parameters`, seeded from `config/business_rules.yaml`. Seeding **only inserts missing keys** — an existing row is never overwritten by a redeploy. | — | CONFIRMED design | `config_service.py::seed_from_yaml`; `app/main.py::lifespan` | app startup |
| Read | `list_parameters` — `admin` only (router-level gate) | — | HARD-CODED | `api/config.py` (`dependencies=[require_roles(admin)]`) | `GET /config/parameters` |
| **Update is two-step (maker-checker)** | `PUT /config/parameters/{key}` no longer applies immediately — returns **202** with a pending `ApprovalRequest(action_type=config.update)`. A *different* `credit_manager`/`admin` must approve. 404 for an unknown key; 409 if a request for that key is already pending. | — | **CONFIRMED — deliberate Step 6 behaviour change** | `api/config.py::request_parameter_update`; `approvals.py::_execute` | `PUT /config/parameters/{key}` → `POST /approvals/{id}/approve` |
| Change history | only the `config.updated` audit event records the old value; no effective-dated config, no rule-set version id on `AssessmentResult` | — | Open (S-11) | `approvals.py::_execute` | — |

---

## 20. Immutable ledger (dual-write, Phase 1)

| Rule | Detail | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|
| Write-only | `LedgerEntry` rows are written **alongside** the existing in-place balance mutations. **No read path uses the ledger yet** — the Receivable and every other figure are unchanged. | CONFIRMED (P0-1, S-4) | `app/services/ledger.py::record_entry` | payment / settlement / cancellation / return / waiver |
| Entry types | `principal_paid`, `profit_recognized`, `profit_rebated`, `late_fee_paid`, `late_fee_waived`, `refund_issued` (others defined, not all written yet) | HARD-CODED | `app/models/ledger.py::LedgerEntryType` | — |
| `related_action` | `payment`, `settlement`, `cancellation`, `return_`, `waiver` | HARD-CODED | `app/models/ledger.py::LedgerRelatedAction` | — |
| Sign convention | `refund_issued` signed from customer POV (`> 0` owed to customer, `< 0` owed by); paid/charged/rebated types are positive magnitudes | HARD-CODED | `app/models/ledger.py` (docstring) | — |
| Reconciliation identity (tested) | `Σ profit_recognized + Σ profit_rebated == Σ Installment.profit_paid` for a settled contract | CONFIRMED (test-locked) | `tests/test_ledger.py` | — |

---

## 21. Authentication & RBAC

| Rule | Detail | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|
| Token | HS256 JWT, `sub` + `role` + `exp`; `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30) | STRUCTURE — refresh/revocation/storage is BDR-30 | `app/core/security.py`; `app/api/auth.py::login` | `POST /auth/login` |
| Every route except `/auth/login` and `/health` needs a bearer token | router-level `Depends(get_current_user)` on every included router | HARD-CODED | `app/main.py` | all |
| Role check | `require_roles(*roles)` → **403** unless `user.role` ∈ allowed | HARD-CODED | `app/core/auth.py::require_roles` | per-endpoint |
| Ownership check | `authorize_owner_or_roles` — a staff role **or** the `customer` whose record it is; a customer hitting someone else's record → **403** (not 404) | HARD-CODED | `app/core/auth.py::authorize_owner_or_roles` | `GET /applications/{id}`, `GET /contracts/{id}/receivable`, `GET /contracts/{id}/settlement-quote`, `GET /collections/cases/{id}` |
| User registration | `admin` only; **409** on duplicate username | HARD-CODED | `app/api/auth.py::register` | `POST /auth/register` |
| Roles | `sales_employee`, `credit_officer`, `credit_manager`, `finance_officer`, `collections_officer`, `customer`, `admin` | HARD-CODED | `app/models/user.py::UserRole` | — |
| Inactive user | `user.active == false` → **401** on any authenticated call | HARD-CODED | `app/core/auth.py::get_current_user` | all |

---

## 22. Reporting (rule-adjacent config)

Reporting queries are read-only over existing tables; the one rule-adjacent
config is the DPD display grouping.

| Rule | Current Value | Config Key | Confirmed or Placeholder | Enforced By | Triggered Via |
|---|---|---|---|---|---|
| Aging buckets | inclusive `[low, high]` day ranges; `null` high = "and beyond" | `dpd_report_buckets` = `[[1,30],[31,60],[61,90],[91,null]]` | PLACEHOLDER — **display grouping only, not a collections-action policy** (BDR-32) | `app/services/reports.py::aging_report`, `aging_bucket_detail` | `GET /reports/aging` |
| Report totals row | every `ReportResult` carries `totals` (row count + declared summable columns); appended to CSV/XLSX/PDF exports | — | CONFIRMED (Step 15) | `app/services/reports.py::ReportResult.totals`, `export` | `GET /reports/*` |
| Report roles | `finance_officer`, `credit_manager`, `admin` | — | HARD-CODED | `app/api/reports.py::_REPORT_ROLES` | `GET /reports/*` |
| `risk_score_refer_min` band edges in credit-risk summary | `low ≥ risk_score_auto_approve_min`; `medium` in `[risk_score_refer_min, auto_approve_min)`; `high < risk_score_refer_min`; `unscored` = null | `risk_score_auto_approve_min` (650), `risk_score_refer_min` (600) | mirrors the assessment thresholds | `app/services/reports.py` (credit-risk summary) | `GET /reports/summary/credit-risk` |

---

## 23. Reference codes (cross-cutting, not a business rule but rule-adjacent)

| Rule | Detail | Enforced By |
|---|---|---|
| Display identity | `f"{PREFIX}-{id:06d}"` computed at serialization, never stored. Prefixes: `Customer→CU`, `Product→PR`, `CreditApplication→AP`, `InstallmentOffer→OF`, `SalesOrder→SO`, `InstallmentContract→CN`, `Payment→PY`, `CollectionCase→CC` | `app/core/references.py::format_reference`; `computed_field` on each `*Out` schema |

---

## 24. Gaps — rules expected but not found in code

| Expected rule | Status in code | Where it would live | Register ref |
|---|---|---|---|
| **Late-fee cap per contract** | Config key `late_fee_max_per_contract` exists (=0) but is **never read** anywhere | `app/services/overdue.py` | BDR-10 |
| **Recurring / repeated late fees while overdue** | Not built — `late_fee_once_per_installment` flag is inert; one fee per installment forever, even a `waived` one blocks a re-charge | `app/services/overdue.py` | BDR-10 |
| **Promise-to-pay kept/broken evaluation** | `promise_status` only ever `pending`; no writer transitions it. Dashboard "promises kept/broken" counts are structurally always 0 | `app/services/collections.py` / an overdue-run step | BDR-15 / G-15 |
| **Collections escalation / restructure / legal / write-off / recovery** | None exist | new modules | BDR-11 / 12 / 13, G-16 / 17 |
| **Online vs branch channel behaviour** | `channel` is stored and reported on only; **no rule branches on it** (no channel-specific KYC, limits, pricing, routing) | assessment / offers | G-35 (light) |
| **Approval-authority thresholds by amount** on manual review | Not built — any single eligible reviewer decides a `referred` application; no amount bands, no maker-checker on the review itself | `app/api/applications.py` | BDR-25 |
| **Return-policy financial treatment** | Only a single signed `financial_adjustment`; no itemized profit reversal, restocking fee, condition/serial check, DP-forfeiture rule, `Refund` entity, or inventory-return record | `app/services/closure.py::return_contract` | BDR-19 / G-19 |
| **Ownership-transfer logic** | `ownership_transfers_on_delivery` is echoed in the return response but **no code branches on it** | closure / write-off | BDR-01 |
| **Scheduled jobs** (DPD, late fee, reminders, recognition, recon, maturity, offer/quote expiry) | All manual — `POST /jobs/assess-overdue` and `POST /jobs/post-accounting-events` only. Maturity closure only happens if a payment lands exactly on zero. | a scheduler | BDR-28 |
| **GL chart-of-accounts / debit-credit mapping** | Not stored/computed — one signed `amount` per `AccountingEvent`; mock adapter always succeeds | `erp_adapter.py` + real `GlProvider` | BDR-31 |
| **Effective-dated config / rule-set versioning on decisions** | `ConfigParameter` updated in place; `AssessmentResult` snapshots values but has no `rule_set_version` | config + assessment | S-11 / G-34 |
| **Product creation role gate** | `POST /products` has **no `require_roles`** — any authenticated user (incl. `customer`, `collections_officer`) can create a product | `app/api/products.py::create_product` | — (implementation gap, not a BDR) |
| **Payment endpoint owner check** | `POST /contracts/{id}/payments` allows role `customer` but does **not** verify the contract belongs to that customer (unlike `GET /contracts/{id}/receivable`, which does) | `app/api/payments.py::record_payment` | — (implementation gap) |
| **`reserved_quantity` write path** | Field exists on `Product`, always `0`, never written — no reservation-at-offer model | `app/services/offers.py` | BDR-18 |
| **`current_exposure` vs `new_financed_estimate` basis mismatch** | Exposure Rule 4 compares `current_exposure` (principal + profit + late fees) against `max_customer_exposure_kwd`, but adds `new_financed_estimate` (financed principal only, pre-profit). Consistent with the code, but the two addends are on slightly different bases. | `app/services/assessment.py` Rule 4 | BDR-08 (threshold policy) |
| **Overpayment / credit-balance handling** | Explicitly out of scope — overpayment is rejected (422), never credited forward | `app/services/payments.py` | — (deliberate) |
| **Down-payment actual collection** | Stubbed — `down_payment_reference` is free text, no gateway, no money movement, no reconciliation of the DP itself | `app/services/offers.py::accept_offer` | G-05 |

---

*Source of truth for values: `config/business_rules.yaml` (seed defaults).
Source of truth for "confirmed vs placeholder": the YAML key comments and
`docs/enterprise-assessment.md` Business Decision Register (BDR-01 … BDR-42).
Every "Enforced By" reference was opened and read while producing this document.*
