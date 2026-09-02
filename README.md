# Retail Credit & Installment Sales Platform

**Steps 1–13 — a FastAPI backend (application → assessment → offer → contract → payments → collections → maker-checker, behind JWT auth + RBAC + audit) and a React staff web app (core flow, review queue, exposure, reconciliation, approvals, closure, config, audit-log, directories, Collections, inventory, a 5-tab Executive Dashboard and a Reports Center with per-category sub-reports + CSV/Excel/PDF export).** Plus post-[assessment](docs/enterprise-assessment.md) P0 fixes: referred → manual verification (P0-2), an immutable financial ledger in dual-write (P0-1), affordability re-check at offer time (P0-3), company-wide customer exposure aggregation (P0-4), and payment → bank reconciliation (P0-5) — which together close all five most-severe findings (S-1 through S-5) — plus an accounting-event boundary for a downstream ERP (Gap Matrix G-07).

This is a **retail installment-sale** platform, not a cash-loan system. The
company never disburses cash. A customer buys a product on credit terms; a
receivable is created; the customer pays it off in installments.

```
Customer → Product Purchase → Credit Assessment → Installment Sale → Receivable → Collections → Closure
           └──────────────── Step 1 ────────────┘ └─── Step 2 ───┘ └── Step 3 ──┘  └ Step 6 ┘   └ Step 4 ┘
                                                  offer→contract    payments,      cases,       settlement,
                                                                    allocation,    activities,  cancellation,
                                                                    overdue, fees  promises     return
   Step 5: every endpoint requires a bearer token, is role-gated, and writes an AuditEvent.
   Step 6: late-fee waivers and config changes now go through a dual-approval (maker-checker) workflow.
                                                  (no real payment gateway; SMS/email not actually sent)
```

---

## What's in Step 1

| Area | Included |
|------|----------|
| Project scaffolding | FastAPI layout, Docker Compose Postgres, Alembic, externalised config |
| Customer | `Customer` + separate `CustomerProfile` (two tables, not merged) |
| Product | Minimal: `id, name, category, cash_price, installment_eligible`. **Cash price only** |
| Application origination | `CreditApplication` as its own entity (never merged with Sales Order / Contract) |
| Credit Assessment Engine | Rules-based, every threshold read from config, returns `approved` / `rejected` / `referred` + audit reasons |

## What's in Step 2

| Area | Included |
|------|----------|
| Pricing / Profit Engine | `app/services/pricing.py` — tenor→rate table from config, declining-balance profit amortization |
| Installment Offer | `InstallmentOffer` generated from an **approved** application; validity window; schedule preview |
| Offer acceptance | Down payment recorded (stubbed — no gateway); on accept → Sales Order + Contract + Schedule |
| Sales Order & Contract | `SalesOrder` (what was sold) and `InstallmentContract` (how it's financed) — **separate linked tables** |
| Payment Schedule | `PaymentSchedule` + `Installment` rows, each with `principal_component` + `profit_component` |
| Delivery | `POST /contracts/{id}/confirm-delivery` moves Contract `created` → `active` |
| Unearned Profit | `InstallmentContract.unearned_profit_balance` seeded to `total_profit` |

## What's in Step 3

| Area | Included |
|------|----------|
| Payment recording | `Payment` entity; `POST /contracts/{id}/payments` — **idempotent** on `external_reference` per contract |
| Allocation Engine | `app/services/allocation.py` — pure waterfall: **oldest installment first**, then **Late Fee → Profit → Principal** within it |
| Allocation audit | `PaymentAllocation` rows record how each payment split across installments/components |
| Overdue / DPD | `app/services/overdue.py` via `POST /jobs/assess-overdue` (manual trigger, optional `as_of`) — marks installments `overdue`, assesses late fees |
| Late fee | `LateFeeCharge` — **its own table**, never folded into profit; `2% × (principal + profit)` of the overdue installment; status `assessed`/`waived`/`paid` |
| Receivable | `GET /contracts/{id}/receivable` — `outstanding_principal`, `outstanding_profit`, `outstanding_late_fees` (**kept separate**), installments paid / remaining |

## What's in Step 4

| Area | Included |
|------|----------|
| Early settlement | `GET /contracts/{id}/settlement-quote` (computes, charges nothing) + `POST /contracts/{id}/settle` (regenerates & re-compares the payoff, then closes) |
| Cancellation | `POST /contracts/{id}/cancel` — **pre-delivery only** (`created`); computes the configured down-payment refund |
| Return | `POST /contracts/{id}/return` — **post-delivery only** (`active`); settlement-shape payoff + return down-payment refund → signed net adjustment |
| `ContractClosure` | **exactly one per contract**, always with a `reason` (`normal`/`early_settlement`/`cancellation`/`return`); signed `financial_adjustment` |
| Re-close guard | a `closed` contract returns **409** on settle / cancel / return / quote |

## What's in Step 5

| Area | Included |
|------|----------|
| Users & roles | `User` (bcrypt hash, never plaintext) + 6-role enum; bootstrap `admin` seeded from `ADMIN_USERNAME`/`ADMIN_PASSWORD` on startup |
| Auth | `POST /auth/login` → short-lived HS256 JWT (`sub`, `role`, `exp`); `POST /auth/register` (**admin only**); `GET /auth/me` |
| RBAC | one reusable `require_roles(...)` dependency + an owner-or-roles check; **all** endpoints (except `/auth/login`, `/health`) need a token; sensitive routes are role-gated per the table below |
| Ownership | a `customer`-role user (linked via `customers.user_id`) may read **their own** application / contract; someone else's → **403** |
| Audit trail | `AuditEvent` written on every state-changing action; `GET /audit/events` (filter by `entity_type`/`entity_id`/`action`) — **admin & credit_manager only** |

## What's in Step 6

| Area | Included |
|------|----------|
| New role | `collections_officer` added to `UserRole` |
| Collections | `CollectionCase` (**≤ 1 open per contract** — partial unique index + service check) + `CollectionActivity` (`call`/`sms`/`email`/`visit`/`promise_to_pay`/`other`) |
| Auto case lifecycle | `assess-overdue` **opens** a case when it first marks an installment overdue; a payment that clears the last overdue installment **closes** it — both hooked into the existing Step 3 services, no poller |
| Maker-checker | generic `ApprovalRequest`; **`decided_by` ≠ `requested_by`** enforced in the service layer (409, any role) |
| Applied to | **late-fee waivers** (`POST /late-fees/{id}/request-waiver` → approve → `LateFeeCharge.status = waived`) and **config changes** (see behaviour change below) |
| ⚠️ Behaviour change | `PUT /config/parameters/{key}` **no longer applies immediately** — it now returns **202** with a pending `ApprovalRequest`; a *different* `credit_manager`/`admin` must approve it before the value (and the `config.updated` audit event) actually change |

## What's in Step 7 — Staff web app (`frontend/`)

First real UI for internal staff, covering **only the core flow**
(backend is untouched). React + Vite + TypeScript in [`frontend/`](frontend/).

| Screen | What it does |
|--------|--------------|
| Login | `POST /auth/login` → token in `localStorage`; shell shows `username (role)` from `/auth/me`, logout, nav. 401 from any call → back to login |
| Create Customer / Product | minimal forms matching `CustomerCreate` / `ProductCreate`; carries the new id forward into "New Application" |
| New Application | one form → `POST /applications` then `/submit`; renders the **assessment panel** — decision, `debt_burden_ratio` (4dp), and every `triggered_rules` entry with its reason (the screen a Credit Officer reads) |
| Offer | from an approved application, enter `down_payment_amount` → `POST …/offer`; shows cash price / down payment / installment sale price / total profit and the full **schedule table** (principal flat, profit declining); "Accept & confirm down payment" → contract |
| Contract | status, sales-order summary, installment table with per-row paid/pending/overdue; "Confirm Delivery" (only while `created`); "Record Payment" → refreshes the table and the `GET …/receivable` figures |

Colour is centralised as CSS custom properties in
[`frontend/src/styles/tokens.css`](frontend/src/styles/tokens.css) (the palette
from the brief, plus a separate `--color-danger` red for rejections) and used
everywhere via `var(--…)` — never hardcoded hex.

## What's in Step 9 — remaining admin/staff screens (`frontend/`)

Real screens for the backend capability that had only ever been exercised via
Swagger. Same tokens / components / role-aware nav as Step 7; Step 7 screens and
flow are unchanged. Nav items appear only for the roles below (`admin` sees all).

| Screen | Nav item / route | Roles | What it does |
|--------|------------------|-------|--------------|
| Review Queue | "Review Queue" · `/review` | `credit_officer`, `credit_manager`, `admin` | lists `referred` applications (`GET /applications?status=referred`); click into one → application details + the automated assessment's triggered rules + a decision form (Approve / Reject / Return for Info + reason → `POST /applications/{id}/review`); on approve, a link into offer generation |
| Customer + Exposure | Dashboard "Customer #" → `/customers/{id}` | any signed-in (exposure panel needs `credit_officer` / `credit_manager` / `finance_officer` / `admin` or the owning customer) | `GET /customers/{id}` details plus the `GET /customers/{id}/exposure` panel — total outstanding and a per-contract breakdown, each row linking to that contract |
| Reconciliation | "Reconciliation" · `/reconciliation` | `finance_officer`, `admin` | `GET /reconciliation/status` summary; "Add Bank Line" → `POST /reconciliation/bank-lines`; "Run Matching" → `POST /reconciliation/run` (shows matched / exceptions-created); exceptions table (`GET /reconciliation/exceptions`, filter by status) with a "Request Match" action → `POST …/request-match` (resolved in Approvals) |
| Approvals | "Approvals" · `/approvals` | `finance_officer`, `credit_manager`, `admin` | pending `GET /approvals?status=pending` with a human-readable payload summary; Approve / Reject → `POST /approvals/{id}/approve` \| `/reject`. A row whose `requested_by` is the current user has its buttons **disabled client-side** with "a different approver is required" (the backend 409 is still authoritative) |
| Contract closure | *(extends the Step 7 contract screen)* | `finance_officer`, `credit_manager`, `admin` | "Get Settlement Quote" (`GET …/settlement-quote`) → full breakdown → "Confirm Settlement" (`POST …/settle` with the fresh amount); "Cancel Contract" only while `created`; "Return Product" only while `active`; once a `closure` exists, its reason + signed `financial_adjustment` are shown and the actions are hidden |
| Configuration | "Configuration" · `/config` | `admin` | `GET /config/parameters` list + per-parameter edit → `PUT /config/parameters/{key}`; because this is maker-checker gated the response is 202/pending — the screen says "Change requested — awaiting a different approver" and links to Approvals, **not** "saved" |
| Audit Log | "Audit Log" · `/audit` | `admin`, `credit_manager` | `GET /audit/events` table, filterable by `entity_type` / `entity_id` / `action` |

One backend addition this step: **`GET /applications?status=…`** (`credit_officer`,
`credit_manager`, `admin`) — a compact list (id, customer_id, product_id,
requested_amount, status, `submitted_at`), added only because the review queue is
unusable without it. Not a general listing surface. (`submitted_at` is the
application's `created_at`; there is no separate submitted timestamp yet.)

Not built (nothing on the backend to show): Collections screens (built in
Step 10 instead — see below), inventory, KYC / document upload, restructuring,
write-off / recovery, notifications, reporting / KPIs.

## What's in Step 10 — directories, portfolio snapshot, Collections, inventory (`frontend/`)

Five more screens. Same tokens / components / role-aware nav as Steps 7–9;
those screens and flow are unchanged. Collections was checked first (Step 8 had
specified it but it was never actually shipped to `frontend/`) and built here.

| Screen | Nav item / route | Roles | What it does |
|--------|------------------|-------|--------------|
| Customer Directory | "Customers" · `/customers` | `sales_employee`, `credit_officer`, `credit_manager`, `finance_officer`, `admin` | search box (name or national ID) → `GET /customers?search=`; each row links into the existing Step 9 customer + exposure screen (not duplicated) |
| Product Directory | "Products" · `/products` | same as above | search box (name or category) → `GET /products?search=`; shows cash price, stock, reserved, and an **Available** / **Sold Out** badge. Read-only — no edit-stock action here |
| Portfolio Snapshot | "Snapshot" · `/snapshot` | `finance_officer`, `credit_manager`, `admin` | current counts only, each panel sourced from an endpoint that already existed: reconciliation health (`GET /reconciliation/status`), accounting events by status (client-side grouped from `GET /accounting/events`), pending approvals (`GET /approvals?status=pending`), open collection cases (`GET /collections/cases?status=open`). The screen states in its own copy that it is **not** a reporting/KPI platform — no charts, no date filters, no exports |
| Collections | "Collections" · `/collections`, `/collections/:id` | `collections_officer`, `credit_manager`, `admin` (case detail also the owning `customer`) — matches the existing backend `_VIEW_ROLES` exactly; `finance_officer` is **not** on that list, so the nav item is not offered to them either | case list filterable by status/contract_id; case detail with activity history; Log Activity form with conditional Promise-to-Pay fields (amount + date, shown only when `activity_type=promise_to_pay`); "Run overdue assessment" utility (`admin` only, same manual job as `/jobs/assess-overdue`) |
| Inventory Adjustment | "Inventory" · `/inventory` | `finance_officer`, `admin` | separate, privileged screen (deliberately not the read-only directory) — table of every product with stock/reserved/available, a per-row "Adjust" (delta + reason) → `POST /products/{id}/stock-adjustment`, refreshes that row; a recent-adjustments panel from the **existing** `GET /audit/events?entity_type=Product&action=stock_adjustment` (no second audit trail) |

Three backend additions, all deliberately narrow:

- **`GET /customers?search=`** — partial, case-insensitive match on `name` OR
  `national_id`. No other filters.
- **`GET /products?search=`** — partial, case-insensitive match on `name` OR
  `category`; **omitted `search` returns every product** (the Inventory screen
  needs the full list and there is no other list endpoint — the one deliberate
  widening beyond "just search").
- **`POST /products/{id}/stock-adjustment`** — body `{delta, reason}`
  (`finance_officer`/`admin`); 422 if the result would drop `stock_quantity`
  below `reserved_quantity`; writes an `AuditEvent` (actor, delta, reason,
  before/after `stock_quantity`) — the same audit pattern used everywhere else.

**Minimal stock tracking** (a real, if small, feature — not just a query):
`Product` gains `stock_quantity` / `reserved_quantity` (migration `0011`,
existing rows backfilled to the placeholder `default_initial_stock_quantity`,
**10**); `available_quantity = stock_quantity - reserved_quantity` is computed,
never stored. The one new business rule: `POST /applications/{id}/offer`
rejects (**422**) if `available_quantity <= 0`. Deduction happens at **contract
creation** (offer acceptance) — additive, one unit off `stock_quantity`;
cancellation and return each release one unit back, via the same kind of
additive hook already used for the accounting-event boundary (never blocks the
closure itself).

> **BUSINESS DECISION REQUIRED** (register, assessment BDR-18): the stock
> **deduction point**. The brief's own Section 23 says this must stay
> configurable/policy-driven — reservation-at-offer, deduction-at-acceptance,
> or deduction-at-delivery are all legitimate models. This step picks
> **deduction at contract creation** as the simplest defensible default so the
> platform can't double-sell the last unit — **not** confirmed policy.
>
> **Judgment call, not confirmed policy:** the stock adjustment endpoint is
> **not** maker-checker gated (it is a stock count, not a financial
> transaction — unlike the P0-5 reconciliation manual match or the Step 6
> config/late-fee-waiver flows). Finance may want a second approver on
> write-downs later; flagging it here rather than assuming.

Out of scope (unchanged): warehouse/branch-level stock, barcode/serial
tracking, low-stock alerts, bulk stock import, anything from a future deep
audit pass.

## What's in Step 11 — reporting layer + Executive Dashboard

The Gap Matrix flagged Reports/KPIs as needing its own design pass since Phase 1
— this is that pass. **Bounded on purpose:** no BI engine, no charts, no
scheduled/emailed reports, no saved definitions, CSV export only. **Every figure
is a live query over real tables** — if a number can't be computed from data
that exists today (true portfolio-at-risk needs ECL, which still doesn't
exist), it is simply absent rather than estimated.

**Backend — 4 report endpoints + 5 dashboard summaries** (all
`finance_officer` / `credit_manager` / `admin`, matching how the P0-4 exposure
endpoint is scoped):

| Endpoint | Returns |
|---|---|
| `GET /reports/contracts` | the one genuinely missing general contract list — filters `status` / `customer_id` / `product_id` / `date_from` / `date_to` (on `created_at`), paginated (`limit`/`offset`), `?format=csv` |
| `GET /reports/profitability` | total contractual / recognized (`Σ profit_paid`) / unearned profit, split by tenor and by product category; filters `date_from` / `date_to` / `product_id`. Contracts closed by **cancellation** are excluded (no sale completed); for every other contract `recognized + unearned == contractual` holds by construction |
| `GET /reports/summary/executive` | total customers, active contracts, outstanding receivable (Σ over active), profit recognized to date, all-time approval rate `approved / (approved+rejected+referred)` |
| `GET /reports/summary/operations` | payments today (count + amount), applications submitted today, currently-overdue installments, open reconciliation exceptions |
| `GET /reports/summary/portfolio` | contracts by status, DPD aging distribution (`dpd_report_buckets` config), average contract size (`installment_sale_price`) |
| `GET /reports/summary/collections` | open cases, promises kept vs broken, late fees charged vs waived (count + amount) |
| `GET /reports/summary/credit-risk` | customers by risk band (**reusing the assessment engine's own `risk_score_auto_approve_min` / `risk_score_refer_min`** — not a second copy), top 10 by exposure (reusing the P0-4 calc), rejection & referral rates |

**CSV export added to the existing Step 8/10 screens** (extended, not
duplicated): `?format=csv` on `GET /customers?search=`, `GET /products?search=`
and `GET /collections/cases` (which also gains `date_from` / `date_to` on
`opened_at`).

**Frontend:**

- **Executive Dashboard** — the Dashboard landing is now a 5-tab dashboard
  (**Executive · Operations · Portfolio · Collections · Credit & Risk**),
  visible to `finance_officer` / `credit_manager` / `admin`. Each tab is a grid
  of `MetricTile`s from its summary endpoint; healthy figures in
  `--color-secondary`, attention figures (overdue, open exceptions, high-risk)
  in `--color-warm`, `--color-danger` reserved for genuinely broken states.
  No charts. The existing "Start a new flow" / "Open an existing record" panels
  are kept, below the tabs. Non-privileged roles see the Dashboard with the
  panels but no tabs.
- **Reports Center** (`/reports`, nav "Reports", same roles) — a left category
  list: **Contracts** and **Profitability** are the two new report screens
  (filter form → Run Report → results table → Export CSV); **Customers**,
  **Products**, **Collections** link to the existing directory / case-list
  screens (with the Part-1C export button now on them) rather than duplicating.

> **BUSINESS DECISION REQUIRED** (register): **DPD bucket boundaries**. The new
> `dpd_report_buckets` config (`[[1,30],[31,60],[61,90],[91,null]]`) is a
> **reporting-display grouping only** — it drives how the Portfolio tab groups
> aging, and is explicitly **not** a collections policy on when action should be
> taken. The real DPD action thresholds are a separate, unconfirmed
> collections-policy decision.

Out of scope *(for Step 11 — PDF/Excel arrived in Step 13)*: charts/graphs,
scheduled or emailed reports, saved report definitions, any metric not
computable from existing tables (true portfolio-at-risk / ECL), DPD buckets as
actual collections policy.

## What's in Step 13 — report sub-categories, Aging report, PDF/Excel export

*(Step 12 — a `level=portfolio/category/product/customer` selector on the
profitability report — was not taken; profitability keeps its Step 11
by-tenor + by-category shape.)*

Step 11's Reports Center only linked out to existing screens for
Customers/Products/Collections. Step 13 gives all six categories real
sub-reports, adds an **Aging** category, and adds **PDF / Excel** export
alongside CSV everywhere. Every figure reuses the calculation that already
exists — risk bands from the assessment engine, exposure from P0-4, DPD buckets
from `dpd_report_buckets`, `available_quantity` from Step 10, `LateFeeCharge`
rows from the accounting-event boundary — no second implementation of any of it.

**New dependencies:** `openpyxl` (Excel) and `reportlab` (plain tabular PDF) —
in `requirements.txt`. Frontend: `lucide-react` for the icon set (Part 4).

**Every report now available, by category:**

| Category | Sub-reports |
|---|---|
| **Contracts** | *All Contracts* (the Step 11 filterable list) · **By Status** (`/reports/contracts/by-status`) · **By Channel** (`/reports/contracts/by-channel` — via Contract → SalesOrder → Application) |
| **Profitability** | *Portfolio Summary* (the Step 11 contractual / recognized / unearned view, by tenor & category) |
| **Customers** | *Full Directory* (links out to `/customers`) · **By Risk Band** (`/reports/customers/by-risk`) · **By Exposure** (`/reports/customers/by-exposure` — full ranked list, paginated) |
| **Products** | *Full Directory* (links out to `/products`) · **By Availability** (`/reports/products/by-availability`) · **By Category** (`/reports/products/by-category` — counts + stock totals) |
| **Collections** | *Full Case List* (links out to `/collections`) · **Status Summary** (`/reports/collections/status-summary`) · **Promise Performance** (`/reports/collections/promise-performance`) · **Late Fees Summary** (`/reports/collections/late-fees-summary`) |
| **Aging** *(new)* | **DPD Buckets** (`/reports/aging`) — every currently-overdue installment on an active contract, grouped into the `dpd_report_buckets` bands: per bucket a count and total outstanding. `GET /reports/aging?bucket=<index>` drills into one bucket (contract, customer, DPD, outstanding per installment) |

**Export:** `?format=csv` · `?format=xlsx` · `?format=pdf` on every `/reports/*`
endpoint (and on the `GET /customers?search=` / `GET /products?search=` /
`GET /collections/cases` directory endpoints). Excel = one sheet, header + rows,
auto column widths. PDF = title, generated-at timestamp, a plain bordered table
(natural pagination only — no charts, no branding). An unknown `format` → 422.

**Frontend:** the Reports Center left rail is now six **category cards** (icon +
title + one-line description); selecting one shows a **sub-report pill nav** and
auto-selects the first sub-report. Each report screen's export control is a
CSV / Excel / PDF button group. Visual polish (icons on metric tiles, tighter
tile density, category cards) is scoped to the **Reports Center and Executive
Dashboard only** — no other screen changed. Colours stay on the existing token
set.

Out of scope: charts inside the app (icons only), Excel/PDF styling beyond
plain tables, scheduled/emailed/saved reports, a second Aging-style report for
any other metric, restyling any screen outside Reports Center / Executive
Dashboard.

### Explicitly out of scope (later steps)

**Backend:** maker-checker on contract settlement / cancellation / return,
collections escalation rules, SMS/email actually being sent, promise-to-pay
follow-up reminders, external IdP / OAuth, refresh tokens, actual refund payment
execution, ECL / provisioning, an **actual scheduled job** (assess-overdue is
still manually triggered).

**Frontend (Step 7):** Collections UI (Step 8), a customer-facing self-service
portal, visual polish, mobile responsiveness, i18n / Arabic UI (backend and this
UI are English-only for now). Token storage is `localStorage` — acceptable for
an internal tool this step, **to be reconsidered** (httpOnly cookie / silent
refresh) in a later step. *(Step 9 added the maker-checker approval UI,
settlement / cancellation / return UI, config-parameter management UI, audit-log
viewer, review queue and customer-exposure screens.)*

## Post-assessment P0 fixes (P0-1 … P0-5)

Following the enterprise review in
[docs/enterprise-assessment.md](docs/enterprise-assessment.md). **P0-1 through
P0-5 address all five most-severe findings (S-1 through S-5).**

### P0-2 — Referred → manual verification (fixes finding S-1)

`referred` was a dead end (an offer can only be generated from an `approved`
application, and nothing moved a referral forward). New:

- `POST /applications/{id}/review` — roles `credit_officer` / `credit_manager` /
  `admin`; **409** unless the application is `referred`.
- Body `{ decision: "approved" | "rejected" | "return_for_info", reason }` →
  status becomes `approved` (proceeds to offer generation exactly like an
  auto-approval) / `rejected` / `draft` (resubmit through the normal `/submit`).
- The review is recorded as a second `AssessmentResult` with **`source =
  manual`** (the automated `referred` row is untouched), plus `reviewed_by` and
  `notes`, and an `application.reviewed` `AuditEvent`.
- **Not** in this slice: approval-authority thresholds by amount (still a
  business decision); any change to the automated assessment rules.

### P0-1 — Immutable financial ledger, **Phase 1: dual-write only** (fixes finding S-4)

Settlement / return / late-fee-waiver mutate balances in place, so "profit 12.46
scheduled → 6.23 charged → 6.23 rebated" cannot be reconstructed. New:

- `LedgerEntry` (append-only): `contract_id`, `entry_type`
  (`principal_paid`, `profit_recognized`, `profit_rebated`, `late_fee_paid`,
  `late_fee_waived`, `refund_issued`, …), signed `amount`, `related_action`,
  `reference_type` + `reference_id` (the `Payment` / `ContractClosure` /
  `ApprovalRequest` that caused it), `created_at`, `created_by`.
- Entries are written **alongside** the existing in-place mutation in the five
  places identified: payment allocation, early settlement (including the
  `profit_rebated` line for the waived amount), cancellation, return, late-fee
  waiver.
- **This is write-only.** `GET /contracts/{id}/receivable` and every other
  calculation are **unchanged** — no read path consults the ledger yet, and no
  existing mutation was removed.
- Proof it is correct: [`tests/test_ledger.py`](tests/test_ledger.py) runs the
  full-repayment, delinquency-then-repayment, and early-settlement scenarios and
  asserts that `Σ LedgerEntry` reproduces the exact figures the existing balance
  code already reports (`Σ principal_paid` ledger == `Σ Installment.principal_paid`;
  `Σ profit_recognized + Σ profit_rebated == Σ Installment.profit_paid`;
  `Σ late_fee_paid == Σ LateFeeCharge.amount_paid`).
- **Next slice (not done):** cut reads over to the ledger, then remove the
  in-place mutation. Not started until this dual-write is proven in production
  data.

> One pre-existing test (`test_grace_period_boundary_is_strictly_greater_than`)
> hard-coded `as_of` dates assuming a fixed "today"; it was made clock-independent
> (derive the run date from the actual first due date). No behaviour changed.

### P0-3 — Affordability correctness (fixes finding S-2)

DBR at application time used `requested_amount / tenor` (no profit, no down
payment) and was never re-checked once the real offer was priced. Two halves:

**1. Better application-time estimate.** No offer exists yet, so:
- read the **same** tenor→rate table the Pricing Engine owns
  (`pricing.resolve_profit_rate` — never a second copy of that logic);
- assume a down payment equal to the configured `minimum_down_payment_pct`;
- `estimated_installment = amount_financed_est × (1 + tenor_rate) / tenor`,
  replacing the flat proxy in the DBR calculation;
- the estimate basis (method, rate used, assumed down-payment %, financed
  amount) is stored on the `AssessmentResult.config_snapshot`. If the requested
  tenor has no configured rate yet, it falls back to the old flat proxy (method
  `flat_factor`).

**2. Re-check when the real offer is priced.** Inside `POST /applications/{id}/offer`,
after the schedule is generated:
- take the **largest single installment** (profit is front-loaded, so normally
  the first — the customer's real peak monthly burden);
- recompute `(existing_obligations + peak_installment) / monthly_income` and
  compare to the same `maximum_debt_burden_ratio`;
- record it as an `AssessmentResult` with **`source = offer_affordability_recheck`**
  (same audit shape as the P0-2 review — every affordability decision is now
  visible in one place);
- on failure the behaviour is config-switchable via
  **`offer_affordability_gate_mode`** (`block` → HTTP **422** with the figures
  [default]; `warn_only` → offer proceeds but the failed re-check is recorded).
  A blocked generation also writes an `offer.blocked_unaffordable` `AuditEvent`
  and creates **no** offer.

> **BUSINESS DECISION REQUIRED** (register): what should happen when the offer
> re-check fails — hard-block (current default), route to manual referral via the
> P0-2 `/review` endpoint, or warn-and-let-a-human-decide? `block` is the safe
> default, **not** confirmed policy. Also unconfirmed: whether assuming the
> *minimum* down payment is the right estimate basis, and whether the initial
> DBR should use the requested amount vs the eventual installment-sale price.

> Two numeric assertions in `tests/test_assessment.py` (the old
> `estimated_installment == 100.0` and `debt_burden_ratio == 0.5`) tested the
> exact proxy formula P0-3 replaces; they were updated to the new figures. All
> decision-outcome assertions are unchanged.

### P0-4 — Customer exposure aggregation (fixes finding S-3)

Credit Assessment never looked at a customer's **other** contracts on this
platform — obligations were self-reported only, so a customer with several
existing contracts was assessed as if they had none. New:

- **`app/services/exposure.py`** — `compute_exposure(db, customer_id)` sums
  `outstanding_principal + outstanding_profit + outstanding_late_fees` across
  **every non-closed contract** of that customer, reusing the per-contract
  Receivable calculation (`build_receivable` — never a second copy). Returns a
  total + a per-contract breakdown. Pure; unit-tested with 0 / 1 / N contracts.
- **New assessment rule `customer_exposure`** — at submit time, `current
  aggregate exposure + this request's estimated financed amount` (reusing P0-3's
  `amount_financed_estimate`, not a third computation) is compared to
  `max_customer_exposure_kwd`. A breach → **`referred`** (same precedence tier
  as the DBR rule — a prudential debt-capacity check, not an auto-reject).
  Recorded in `triggered_rules` + the config snapshot like every other rule.
- **`GET /customers/{id}/exposure`** — the aggregate figure + per-contract
  breakdown (id, status, outstanding principal / profit / late fees). Roles:
  `credit_officer`, `credit_manager`, `finance_officer`, `admin`, or the owning
  `customer`.
- **Only company-wide aggregation is implemented** — one sum across all
  contracts regardless of product / category / brand. `exposure_aggregation_level`
  exists for the future; any value other than `company_wide` **raises** rather
  than silently under-counting.
- No migration — two `config_parameters` rows (YAML-seeded), no schema change.

> **BUSINESS DECISION REQUIRED** (register, assessment BDR-07/08): the
> aggregation *level* — company-wide (what's running now) vs per product /
> category / brand / business unit — and the `max_customer_exposure_kwd`
> threshold itself. `8000` is a **clearly fictitious placeholder**; company-wide
> is the only implemented level, made explicit here now that it's live.

### P0-5 — Payment lifecycle & bank reconciliation (fixes finding S-5)

> **This closes the last of the five most-severe assessment findings
> (S-1 through S-5).**

Until now, recording a `Payment` was the end of the story: the customer's
obligation was satisfied and installments were allocated, but nothing ever
checked that the money actually landed in the company's bank account. S-5 is
that missing **payment → settlement → reconciliation** boundary.

**What is unchanged:** recording a payment still means the obligation was met
and installments were allocated — same behaviour, same numbers. Reconciliation
only *observes*. `reconciliation_status` defaults to `unreconciled` for every
existing and new payment and never blocks or alters an allocation, a receivable
or a closure.

New:

- **`Payment` gains two additive columns** — `reconciliation_status`
  (`unreconciled` default / `reconciled` / `exception`) and `gateway_reference`
  (nullable; a future real gateway's own transaction id, distinct from
  `external_reference`, which stays the per-contract idempotency key).
- **`BankStatementLine`** — one line of the company's bank statement. There is
  **no real bank feed**: lines are recorded one at a time via
  `POST /reconciliation/bank-lines` (`finance_officer` / `admin`), a mock
  adapter standing in for a future import.
- **Matching engine** (`app/services/reconciliation.py`, run via
  `POST /reconciliation/run`). For every not-yet-processed bank line, against the
  pool of `unreconciled` payments:
  1. **Exact reference** — `bank_reference` equals a payment's
     `external_reference` (or `gateway_reference` if set). One hit with a
     matching amount → reconciled. One hit, wrong amount → `amount_mismatch`
     exception (and that payment is flagged `exception`). Two+ hits →
     `duplicate_candidate`.
  2. **Fallback: amount + value date** — same amount and value date within a
     configurable tolerance window (`reconciliation_date_tolerance_days`,
     **placeholder `0` = same calendar day**). One hit → reconciled. Two+ →
     `duplicate_candidate`.
  3. **No candidate** → `no_match` exception.
  On a match: `Payment.reconciliation_status = reconciled` and
  `BankStatementLine.matched_payment_id` is linked.
- **Idempotent** — a line is "done" once it is matched **or** has an exception,
  so `POST /reconciliation/run` can be run any number of times: it never
  re-matches a line or duplicates an exception.
- **`ReconciliationException`** — a line the engine could not auto-reconcile
  (`no_match` / `amount_mismatch` / `duplicate_candidate`), `open` until
  resolved. `GET /reconciliation/exceptions?status=` lists them
  (`finance_officer` / `credit_manager` / `admin`).
- **Manual resolution reuses the generic maker-checker** (`ApprovalRequest`,
  `action_type = reconciliation.manual_match`):
  `POST /reconciliation/exceptions/{id}/request-match` (body: target payment id
  + reason) creates a pending approval; a **different**
  `finance_officer` / `credit_manager` / `admin` approves it via the existing
  `POST /approvals/{id}/approve`, which performs the match. The
  "approver ≠ requester" rule is **not** bypassed for this action type.
- **`GET /contracts/{id}/receivable`** gains an additive `reconciliation_summary`
  (count of this contract's payments by reconciliation status). Every existing
  figure on that endpoint is unchanged.
- **`GET /reconciliation/status`** (`finance_officer` / `admin`) — portfolio-wide
  counts of payments by status, open/resolved exceptions, and unmatched lines.
- **Migration `0009`** — the two `Payment` columns
  (`reconciliation_status` NOT NULL, server default `unreconciled`, backfilling
  every existing row) plus the `bank_statement_lines` and
  `reconciliation_exceptions` tables.

> **BUSINESS DECISION REQUIRED** (register, assessment BDR): the fallback
> **date-tolerance window** (`reconciliation_date_tolerance_days`, placeholder
> `0`), and what a `duplicate_candidate` / `amount_mismatch` should trigger
> operationally beyond "open an exception for a human". `finance_officer` was
> added to the approval-decider roles so they can approve
> `reconciliation.manual_match`; no test asserted they could not.

**Out of scope (unchanged):** a real payment gateway or bank feed (both stay
mocked / manual), accounting / GL posting of reconciled payments, the P0-1
ledger read-cutover, and any settlement-batch / T+N timing model.

---

## Accounting-event boundary (fills Gap Matrix G-07 / assessment §22)

The Gap Matrix's one confirmed *"missing entirely"* row was the General Ledger /
accounting boundary: financial events happened with no structured, postable
record for a downstream ERP. This is **not** a general ledger — it is the
**boundary**, the same mock-adapter pattern already used for the payment gateway
and the bank feed.

**Confirmed principle (built in):** accounting events are generated
**automatically and additively** from events that already happen — they never
change any existing business behaviour or response shape. Posting an event to the
ERP is downstream and recoverable; a failed post never means the payment /
settlement / activation didn't happen.

- **`AccountingEvent`** — `event_type`, `event_reference` (unique — the
  idempotency key, e.g. `payment-received-42`), `contract_id`, `customer_id`,
  `amount` (single signed figure), `currency` (`KWD`), `event_date`,
  `accounting_status` (`pending` / `posted` / `failed`), `external_gl_reference`,
  `error_message`, `retry_count`.
- **Hooks (additive only)** in the existing flows:
  | Trigger | Events |
  |---|---|
  | Delivery confirmation | `contract_activated` (sale price) + `down_payment_received` (down payment) |
  | Payment allocation | `payment_received` (whole payment) + `profit_recognized` (profit portion *this* allocation recognised, not the full schedule) |
  | Overdue job assesses a late fee | `late_fee_charged` |
  | Late-fee waiver approved | `late_fee_waived` |
  | Early settlement / cancellation / return | one matching event, `amount` = the closure's signed `financial_adjustment` |

  Every hook is idempotent — the unique `event_reference` means firing the same
  trigger twice (e.g. a replayed payment) never creates a duplicate.
- **Mock ERP adapter** (`app/services/erp_adapter.py`) — `GlProvider` interface;
  `MockGlProvider.post_event` always succeeds and returns a fake
  `MOCK-GL-{uuid}` reference. A real ERP client is a drop-in replacement. No
  retry / backoff / circuit-breaker — deferred until a real ERP exists.
- **`POST /jobs/post-accounting-events`** (`admin`) — on-demand posting job
  (like `/jobs/assess-overdue`, **not** a scheduler): hands every non-`posted`
  event to the adapter, records `posted` + reference or `failed` +
  `error_message` + `retry_count++`. Idempotent and safe to re-run.
- **`GET /accounting/events`** (`finance_officer`, `admin`) — list, filterable by
  `event_type` / `accounting_status` / `contract_id`.
- **Migration `0010`** — one new `accounting_events` table. No existing table or
  column changes.

> **BUSINESS DECISION REQUIRED** (register, assessment BDR-31): the actual
> **chart-of-accounts / debit-credit mapping** per `event_type` — Finance has not
> confirmed it. The model deliberately stores only one signed `amount`; the
> double-entry split is applied by the real `GlProvider` later, not here. Also
> unconfirmed: real-time vs batched posting (on-demand job for now). For a plain
> early settlement the closure records no `financial_adjustment` (the payoff was
> collected in full via `/settle`), so that event's amount is `0.00` and the
> money detail lives on the settlement `Payment` + ledger entries.

**Out of scope:** real ERP/GL integration, the chart-of-accounts mapping,
adapter resilience, write-off / recovery / ECL events (those actions don't
exist yet), any scheduled posting.

---

## Tech stack

**Backend** Python 3.11 · FastAPI · PostgreSQL · SQLAlchemy 2.x · Alembic · PyJWT · bcrypt · openpyxl + reportlab (report export) · Pytest · Docker Compose
**Frontend** ([`frontend/`](frontend/)) React 18 · Vite 5 · TypeScript · react-router · Vitest + React Testing Library

---

## Running with Docker (recommended)

```bash
docker compose up --build
```

This starts Postgres, runs `alembic upgrade head`, seeds the business-rule
parameters, creates the bootstrap `admin` user (`ADMIN_USERNAME` /
`ADMIN_PASSWORD`, default `admin`/`admin`), and serves the API on
**http://localhost:8000**.

- Swagger UI: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json
- Health: http://localhost:8000/health (open — no token)

Every other endpoint needs `Authorization: Bearer <token>` — see
[Authentication & RBAC](#authentication--rbac-step-5).

## Running locally without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# start only Postgres from compose
docker compose up -d db

export DATABASE_URL="postgresql+psycopg2://retail:retail@localhost:5544/retail_credit"
alembic upgrade head
python -m scripts.seed_config          # seed business-rule parameters
python -m scripts.create_admin        # create the bootstrap admin (env: ADMIN_USERNAME/ADMIN_PASSWORD)
uvicorn app.main:app --reload
```

## Running the frontend + backend together

The staff UI lives in [`frontend/`](frontend/) (React + Vite). It talks to the
backend through the Vite dev server's `/api` proxy, so **no CORS setup and no
backend change** is needed.

```bash
# terminal 1 — backend (either "docker compose up --build" or the local recipe above)
#   serving on http://localhost:8000

# terminal 2 — frontend
cd frontend
cp .env.example .env          # VITE_API_URL=http://localhost:8000 (the proxy target)
npm install
npm run dev                   # http://localhost:5173
```

Open **http://localhost:5173**, sign in with the bootstrap admin
(`admin` / `admin` by default), and walk the flow:
Create Customer → Create Product → New Application (see the assessment result) →
Generate Offer → Accept → Confirm Delivery → Record Payment.

```bash
cd frontend && npm test        # Vitest + React Testing Library
```

> **Token storage note:** the token is kept in `localStorage` — fine for this
> internal-tool step, but **not** the long-term approach; a later step should
> move to an httpOnly cookie + silent refresh.

---

## Migrations

Alembic is configured in [alembic.ini](alembic.ini); the DB URL is injected from
app settings in [alembic/env.py](alembic/env.py), and all models are imported via
[app/models/\_\_init\_\_.py](app/models/__init__.py) so autogenerate sees them.

```bash
alembic upgrade head                      # apply migrations
alembic revision --autogenerate -m "..."  # create a new migration
alembic downgrade -1                      # roll back one
```

Migrations:

- [`0001_initial`](alembic/versions/0001_initial.py) — customers, profiles, products, credit applications, assessment results, config parameters
- [`0002_offers_contracts_schedule`](alembic/versions/0002_offers_contracts_schedule.py) — installment offers, sales orders, installment contracts, payment schedules, installments
- [`0003_payments_late_fees`](alembic/versions/0003_payments_late_fees.py) — payments, payment allocations, late fee charges; `installments.principal_paid` / `profit_paid`
- [`0004_contract_closure`](alembic/versions/0004_contract_closure.py) — `contract_closures` (one per contract)
- [`0005_users_audit`](alembic/versions/0005_users_audit.py) — `users`, `audit_events`, nullable `customers.user_id`
- [`0006_collections_approvals`](alembic/versions/0006_collections_approvals.py) — `collection_cases` (+ partial unique open-case index), `collection_activities`, `approval_requests`
- [`0007_ledger_and_manual_review`](alembic/versions/0007_ledger_and_manual_review.py) — `ledger_entries` (write-only); `assessment_results.source` / `reviewed_by` / `notes`
- [`0008_affordability_recheck`](alembic/versions/0008_affordability_recheck.py) — widen `assessment_results.source` (P0-3)
- [`0009_bank_reconciliation`](alembic/versions/0009_bank_reconciliation.py) — `payments.reconciliation_status` (NOT NULL, server default `unreconciled`) + `payments.gateway_reference`; `bank_statement_lines`, `reconciliation_exceptions` (P0-5)
- [`0010_accounting_events`](alembic/versions/0010_accounting_events.py) — `accounting_events` (one new table, purely additive — Gap Matrix G-07)
- [`0011_product_stock`](alembic/versions/0011_product_stock.py) — `products.stock_quantity` / `products.reserved_quantity` (Step 10, existing rows backfilled to the placeholder default)

*(P0-4 and Steps 11 & 13 added no migration — Step 11's config value is a YAML-seeded `config_parameters` row; Step 13 is read-only report queries + export libraries.)*

---

## Business-rule configuration (not hardcoded)

**Choice: a DB-backed table (`config_parameters`), seeded from a version-controlled
YAML file ([config/business_rules.yaml](config/business_rules.yaml)).**

| | YAML file only | **DB table (chosen)** |
|---|---|---|
| Change a threshold | edit file + redeploy | update a row at runtime — since Step 6 via a maker-checker `PUT /config/parameters/{key}` → approval |
| Audit of what value produced a decision | none | `updated_at` per row **and** a `config_snapshot` stored on every assessment |
| Works in a multi-instance deployment | needs redeploy of all | single source of truth |
| Extra moving parts | none | one table + a seeding step |

The YAML file remains the version-controlled set of defaults and documentation.
On startup, any key missing from the table is inserted from YAML; existing rows
are never overwritten, so operational edits win.

The assessment engine reads every threshold through `ConfigService`
([app/services/config_service.py](app/services/config_service.py)) using named
keys. **No policy number appears in [app/services/assessment.py](app/services/assessment.py).**

> ⚠️ All default values are **fictitious placeholders** for demonstration. They
> are not derived from and must not be read as real Kuwait consumer-lending
> policy. See the header of `config/business_rules.yaml`.

Default (placeholder) parameters:

| key | value | meaning |
|-----|-------|---------|
| `minimum_monthly_income` | 300 | below → **rejected** |
| `maximum_debt_burden_ratio` | 0.40 | `(obligations + est. installment) / income` above this → **referred** |
| `installment_estimation_factor` | 1.0 | est. installment = `requested_amount * factor / tenor_months` (straight-line, no profit) |
| `risk_score_auto_approve_min` | 650 | score ≥ → approve-eligible |
| `risk_score_refer_min` | 600 | 600–649 → **referred**, < 600 → **rejected** |
| `tenor_profit_rate_table` | `{"6":0.04,"12":0.09,"18":0.135,"24":0.18,"36":0.30}` | **Step 2** — tenor (months) → total profit rate on financed principal; a `json`-typed parameter. A tenor with no entry is rejected at offer generation |
| `minimum_down_payment_pct` | 0.15 | **Step 2** — minimum down payment as a fraction of cash price |
| `offer_validity_days` | 7 | **Step 2** — days a presented offer stays acceptable |
| `late_fee_rate` | 0.02 | **Step 3** — **confirmed business rule** (not a placeholder): 2% of the overdue installment's own `principal + profit` |
| `late_fee_grace_period_days` | 10 | **Step 3, placeholder** — a fee is assessed only when `DPD > this` |
| `late_fee_once_per_installment` | `true` | **Step 3, placeholder** — Step 3 always assesses at most once per installment; recurring re-charge is **not built**, so this flag currently has no behavioural effect |
| `late_fee_max_per_contract` | 0 | **Step 3, placeholder — NOT WIRED UP.** Reserved for a future cap; `0` = no cap; nothing reads it |
| `early_settlement_profit_rebate_pct` | 0.5 | **Step 4, placeholder — not confirmed policy.** Fraction of remaining unearned profit **waived** on early settlement |
| `down_payment_refund_pct_cancellation` | 1.0 | **Step 4, placeholder — not confirmed policy.** Fraction of the down payment refunded on **pre-delivery cancellation** |
| `down_payment_refund_pct_return` | 0.0 | **Step 4, placeholder — not confirmed policy.** Fraction of the down payment refunded on **post-delivery return** |
| `ownership_transfers_on_delivery` | `true` | **Step 4, placeholder — not a legal position.** No logic branches on it; only echoed back in the return response |
| `settlement_quote_validity_days` | 3 | **Step 4, placeholder** — informational `quote_expiry`; `/settle` always regenerates & re-compares |
| `offer_affordability_gate_mode` | `block` | **P0-3, BUSINESS DECISION REQUIRED** — on a failed offer-time affordability re-check: `block` (422) or `warn_only` (record & proceed) |
| `max_customer_exposure_kwd` | 8000 | **P0-4, placeholder** — max total outstanding per customer (all non-closed contracts) + the new request's financed estimate; breach → `referred` |
| `exposure_aggregation_level` | `company_wide` | **P0-4, BUSINESS DECISION REQUIRED** — only `company_wide` implemented; per-category/brand/BU is a future value that raises if configured |
| `reconciliation_date_tolerance_days` | 0 | **P0-5, placeholder / BUSINESS DECISION REQUIRED** — fallback bank-line matching: `|payment date − value date|` allowed for an amount-only match. `0` = same calendar day |
| `default_initial_stock_quantity` | 10 | **Step 10, placeholder** — opening `stock_quantity` for a brand-new product and the value migration `0011` backfilled every existing product to. No real inventory feed yet |
| `dpd_report_buckets` | `[[1,30],[31,60],[61,90],[91,null]]` | **Step 11, DISPLAY GROUPING ONLY** — inclusive `[low, high]` days-past-due ranges for the Portfolio dashboard's aging distribution (`null` high = "and beyond"). **Not** a collections-action policy; the real DPD action thresholds are a separate unconfirmed decision |

The rate table is stored as a single JSON parameter, so the tenor→rate mapping
is edited as one unit (via `PUT /config/parameters/tenor_profit_rate_table` with
a JSON object body, or directly in the row). `ConfigService` gained
`get_json()` / a `json` value-type for this.

---

## Credit Assessment Engine

`app/services/assessment.py`. On `POST /applications/{id}/submit` the status moves
`draft → submitted → under_assessment`, then the engine runs three rules:

1. **minimum_income** — `monthly_income ≥ minimum_monthly_income` (else *rejected*)
2. **debt_burden_ratio** — `(existing_obligations + estimated_installment) / income ≤ max` (else *referred*)
3. **risk_band** — from the stubbed integer `risk_score` on the customer:
   `≥ auto_approve_min` → approve · `≥ refer_min` → *referred* · else *rejected* ·
   `null` → *referred* (no bureau integration exists yet — the field is set manually)

**Decision precedence: `rejected` > `referred` > `approved`.**

The result is persisted as an `AssessmentResult` row (audit trail — a new row per
run) containing the decision, the estimated installment, the DBR, the list of
**triggered** (failed) rules with human-readable reasons, and a snapshot of the
config values used.

---

## Installment Pricing / Profit Engine (Step 2)

[app/services/pricing.py](app/services/pricing.py). This is a **sale**, not a
loan — the markup is **profit**, fixed at contract time, and is never called
"interest".

**Amounts**

```
principal_financed     = cash_price − down_payment
profit_rate            = tenor_profit_rate_table[tenor]          (from config)
total_profit           = principal_financed × profit_rate        (whole of term)
installment_sale_price = cash_price + total_profit
amount_financed        = installment_sale_price − down_payment  = principal_financed + total_profit
```

**Declining-balance profit recognition**

Principal is repaid in equal monthly amounts, so the outstanding principal falls
linearly. Profit is recognised **in proportion to that outstanding principal**:
installment `i` of `N` carries weight `N − i + 1`, i.e.

```
profit_component[i] = total_profit × (N − i + 1) / (N(N+1)/2)
```

Early installments therefore carry more profit than later ones (the
reducing-balance shape); profit-per-installment **never increases** down the
schedule. Both columns use **cumulative rounding** — the rounded running totals
land exactly on `principal_financed` and `total_profit` at the final
installment, so a generated schedule reconciles with **zero rounding drift**.

Each `Installment` stores `principal_component` and `profit_component`
separately. The contract's `unearned_profit_balance` starts at `total_profit`
and is drawn down as profit components are paid (payment processing is the next
step).

The `tenor → rate` table, the down-payment minimum, and the offer validity
window are all read from `ConfigService` — nothing is hardcoded in the engine.

---

## Offer → Contract flow (Step 2)

```
approved CreditApplication
   └─ POST /applications/{id}/offer      → InstallmentOffer (status: presented, valid_until)
        └─ POST /offers/{id}/accept      → records down payment, then creates:
             ├─ SalesOrder               (application, product, sale_price, down_payment)
             └─ InstallmentContract      (tenor, total_profit, unearned_profit_balance; status: created)
                  └─ PaymentSchedule + N × Installment   (declining-balance breakdown, status: pending)
        └─ POST /contracts/{id}/confirm-delivery   → Contract status: created → active (+ activated_at)
```

`SalesOrder` and `InstallmentContract` are **separate linked tables** — *what was
sold* vs *how it is financed*. Accepting with `down_payment_confirmed: false` (or
omitted) changes nothing: the offer stays `presented` and no order/contract is
created.

---

## Payments, Allocation & Overdue (Step 3)

### Allocation waterfall

A payment is allocated by **two rules applied together**:

1. **Oldest installment first** — the oldest unpaid installment is settled *in
   full* before any amount reaches a newer one.
2. **Within an installment: Late Fee → Profit → Principal.**

Rule 1 outranks rule 2 — the oldest installment's **principal** is paid before
the next installment's **profit**. (`app/services/allocation.py` is a pure
function; the spanning-two-installments case is pinned in
[tests/test_allocation.py](tests/test_allocation.py).)

Each allocation:
- advances `Installment.principal_paid` / `profit_paid` and its status
  (`pending → partially_paid → paid`; an `overdue` installment stays `overdue`
  until fully paid);
- draws `InstallmentContract.unearned_profit_balance` down by the profit paid;
- settles `LateFeeCharge` rows (→ `paid`) for the late-fee portion;
- is recorded as a `PaymentAllocation` audit row.

Payments are **idempotent**: `external_reference` is unique per contract, and
replaying it returns the original result (`replayed: true`) without
re-allocating. An overpayment is applied as far as it can go; the remainder sits
on the payment as `unallocated_amount` (status `overpaid`).

### Overdue & late fees

`POST /jobs/assess-overdue` (manual trigger — **not** a real scheduled job;
accepts an optional `as_of` date for testing). For each past-due installment on
an `active` contract it:
- marks the installment `overdue` (if not fully paid);
- if `DPD > late_fee_grace_period_days` **and** no fee has been assessed yet,
  creates a `LateFeeCharge` = `late_fee_rate × (principal_component +
  profit_component)` of **that installment**, status `assessed`.

**Late fee ≠ profit.** It is a separate table (`LateFeeCharge`); it is never
added to `profit_component` or `unearned_profit_balance`.

Open / placeholder parameters, all clearly marked in
[config/business_rules.yaml](config/business_rules.yaml):

| Parameter | Status this step |
|-----------|------------------|
| `late_fee_grace_period_days` (10) | placeholder — configurable |
| `late_fee_once_per_installment` (`true`) | recurring re-charge **not built**; a fee is assessed at most once per installment regardless |
| `late_fee_max_per_contract` (0) | **not wired up** — reserved name only, nothing reads it |
| late-fee **waiver** workflow | not built — `LateFeeCharge.status` supports `waived` for a future maker-checker endpoint |

### Receivable

`GET /contracts/{id}/receivable`:

```
outstanding_principal          Σ (principal_component − principal_paid)
outstanding_profit             Σ (profit_component  − profit_paid)
outstanding_receivable         outstanding_principal + outstanding_profit   ← late fees NOT included
outstanding_late_fees          Σ LateFeeCharge.outstanding   (separate ledger)
total_installments_paid / total_installments_remaining
```

Per the open-decision note, the single Receivable figure is **principal + profit
only**; late fees are summed separately so the distinction stays visible.

---

## Contract closure — settlement / cancellation / return (Step 4)

Three ways a contract ends before normal maturity. **Every** path writes exactly
one [`ContractClosure`](app/models/closure.py) (with a `reason`) and sets the
contract to `closed`; a `closed` contract returns **409** on any of these again.

> Every formula below is driven by a **fictitious placeholder** config value —
> none is confirmed commercial or legal policy. See
> [config/business_rules.yaml](config/business_rules.yaml).

### Early settlement — contract must be `active`

`GET /contracts/{id}/settlement-quote` computes (charges nothing):

```
outstanding_principal   Σ unpaid principal_component
outstanding_late_fees   Σ unpaid LateFeeCharge            ← its own line, never merged into principal/profit
unearned_profit_total   contract.unearned_profit_balance
profit_rebate_amount    unearned_profit_total × early_settlement_profit_rebate_pct   (placeholder 0.5)
profit_still_charged    unearned_profit_total − profit_rebate_amount
final_payoff_amount     outstanding_principal + outstanding_late_fees + profit_still_charged
quote_expiry            now + settlement_quote_validity_days   (informational)
```

`POST /contracts/{id}/settle` `{amount, external_reference}` — **regenerates the
quote server-side** and rejects (422) if `amount` ≠ the fresh `final_payoff_amount`
(stale client quotes don't pass). On match: every remaining installment → `paid`,
unpaid late fees → `paid`, `unearned_profit_balance` → 0, Receivable → 0,
`ContractClosure(reason=early_settlement)`, contract → `closed`.

### Cancellation — contract must be `created` (pre-delivery)

`POST /contracts/{id}/cancel` — `down_payment_refund = down_payment_amount ×
down_payment_refund_pct_cancellation` (placeholder 1.0).
`ContractClosure(reason=cancellation, financial_adjustment = +refund)`.
If the contract is already `active`, **409** pointing at `/return`.

### Return — contract must be `active` (post-delivery)

`POST /contracts/{id}/return` — reuses the **settlement-quote shape** for the
principal / profit / late-fee side, plus `down_payment_refund_pct_return`
(placeholder 0.0):

```
net_adjustment = down_payment_refund − settlement_shape_payoff
```

`ContractClosure(reason=return, financial_adjustment = net_adjustment)`. The
response echoes `ownership_transfers_on_delivery` (placeholder `true`) so the
assumption in effect is visible — **no logic branches on it**. If the contract is
still `created`, **409** pointing at `/cancel`.

### `ContractClosure.financial_adjustment` sign convention

Signed **from the customer's point of view**: `> 0` → net cash owed **to** the
customer (refund / rebate); `< 0` → net cash the customer **still owes**;
`null` → no monetary adjustment recorded (plain early settlement — the payoff is
collected via `/settle` and the breakdown lives on the quote).

---

## Authentication & RBAC (Step 5)

Every endpoint except `POST /auth/login` and `GET /health` requires
`Authorization: Bearer <token>` (a **missing/invalid token → 401**). Sensitive
routes additionally check the caller's role (**wrong role → 403**), enforced by
one reusable dependency — `require_roles(...)` in
[app/core/auth.py](app/core/auth.py) — not per-route code.

### Getting a token

```bash
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/auth/login -H 'content-type: application/json' \
  -d '{"username": "admin", "password": "admin"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s $BASE/auth/me -H "Authorization: Bearer $TOKEN"          # sanity-check the token
# admin only — create other users:
curl -s -X POST $BASE/auth/register -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"username": "sara.sales", "password": "s3cret12", "role": "sales_employee"}'
```

Tokens are HS256, `access_token_expire_minutes` (default 30), carrying
`sub` (user id), `role`, `exp`. No refresh tokens / revocation this step.

### Roles

`admin` · `credit_officer` · `credit_manager` · `sales_employee` ·
`finance_officer` · `customer` · `collections_officer` *(Step 6)*
&nbsp; (`admin` is allowed everywhere.)

| Endpoint | Allowed roles |
|---|---|
| `POST /customers` | `sales_employee`, `admin` |
| `GET /customers?search=` *(Step 10)* | `sales_employee`, `credit_officer`, `credit_manager`, `finance_officer`, `admin` |
| `GET /products?search=` *(Step 10)* | same as above |
| `POST /products/{id}/stock-adjustment` *(Step 10)* | `finance_officer`, `admin` |
| `GET /customers/{id}/exposure` *(P0-4)* | `credit_officer`, `credit_manager`, `finance_officer`, `admin`, **or the owning `customer`** |
| `POST /applications`, `POST /applications/{id}/submit` | `sales_employee`, `customer`, `admin` |
| `POST /applications/{id}/review` *(P0-2)* | `credit_officer`, `credit_manager`, `admin` |
| `GET /applications?status=…` *(Step 9)* | `credit_officer`, `credit_manager`, `admin` |
| `GET /applications/{id}` | `sales_employee`, `credit_officer`, `credit_manager`, `admin`, **or the owning `customer`** |
| `POST /applications/{id}/offer` | `sales_employee`, `credit_officer`, `admin` |
| `POST /offers/{id}/accept` | `sales_employee`, `customer`, `admin` |
| `POST /contracts/{id}/confirm-delivery` | `sales_employee`, `admin` |
| `POST /contracts/{id}/payments` | `sales_employee`, `finance_officer`, `customer`, `admin` |
| `POST /jobs/assess-overdue` | `admin` |
| `GET /contracts/{id}/receivable`, `GET /contracts/{id}/settlement-quote` | `finance_officer`, `credit_manager`, `admin`, **or the owning `customer`** |
| `POST /contracts/{id}/settle` / `/cancel` / `/return` | `finance_officer`, `credit_manager`, `admin` |
| `GET /config/parameters` | `admin` |
| `PUT /config/parameters/{key}` | `admin` — but now *requests* a change (see maker-checker) |
| `GET /audit/events` | `admin`, `credit_manager` |
| `POST /auth/register` | `admin` |
| `POST /collections/cases/{id}/activities` *(Step 6)* | `collections_officer`, `admin` |
| `GET /collections/cases`, `GET /collections/cases/{id}` *(Step 6)* | `collections_officer`, `credit_manager`, `admin` (detail also the owning `customer`) |
| `POST /late-fees/{id}/request-waiver` *(Step 6)* | `finance_officer`, `credit_manager`, `admin` |
| `GET /approvals`, `POST /approvals/{id}/approve` / `/reject` *(Step 6; `finance_officer` added P0-5)* | `finance_officer`, `credit_manager`, `admin` |
| `POST /reconciliation/bank-lines`, `POST /reconciliation/run`, `GET /reconciliation/status` *(P0-5)* | `finance_officer`, `admin` |
| `GET /reconciliation/exceptions`, `POST /reconciliation/exceptions/{id}/request-match` *(P0-5)* | `finance_officer`, `credit_manager`, `admin` |
| `GET /accounting/events` *(G-07)* | `finance_officer`, `admin` |
| `POST /jobs/post-accounting-events` *(G-07)* | `admin` |
| `GET /reports/*` — contracts, profitability, 5 tab summaries *(Step 11)*, the Step 13 per-category sub-reports and `/reports/aging` | `finance_officer`, `credit_manager`, `admin` |
| other authenticated endpoints (`POST /products`, `GET /contracts/{id}`, `GET /offers/{id}`, `GET /customers/{id}`, `GET /products/{id}`, `GET /auth/me`) | any valid token |

**Ownership.** A `customer`-role user is linked to a `Customer` via
`customers.user_id` (set manually / by helper for now — no self-service signup).
For the "or the owning `customer`" rows, a customer accessing **someone else's**
record gets **403** (not 404).

### Audit trail

Every state-changing action writes one `AuditEvent`
([app/models/audit.py](app/models/audit.py)):

```
id · user_id (nullable — system/job) · action · entity_type · entity_id (string)
  · before_value (JSON|null) · after_value (JSON|null) · timestamp
```

`before`/`after` are **minimal snapshots** (e.g. `{"status": "draft"}` →
`{"status": "approved", "decision": "approved"}`), not field-by-field diffs.
Actions include `customer.created`, `application.created` / `application.submitted`,
`offer.generated` / `offer.accepted`, `contract.created` / `contract.delivered` /
`contract.settled` / `contract.cancelled` / `contract.returned`,
`payment.recorded`, `overdue.assessed`, `late_fee.assessed`, `config.updated`,
`user.registered` (Step 6 adds `collection_case.opened` / `.closed`,
`collection.activity_logged`, `approval.requested` / `.approved` / `.rejected`,
`late_fee.waived`).

`GET /audit/events?entity_type=installment_contract&entity_id=42` (also
`action=`, `limit=`) — **admin & credit_manager only**.

---

## Collections (Step 6)

Operational contact history on top of the Step 3 overdue mechanics —
**separate** from the maker-checker control below. Logging a call needs no
approval; waiving a fee does.

`CollectionCase` — **at most one `open` per contract** (partial unique index
`WHERE status = 'open'`, plus a service-layer check). Lifecycle is automatic,
hooked into existing services:

| Trigger | Effect |
|---|---|
| `POST /jobs/assess-overdue` marks an installment `overdue`, contract has no open case | opens a case (`opened_reason` = the triggering installment) — idempotent, re-running opens nothing new |
| a payment brings the contract's overdue-installment count to zero | closes the open case (`status → closed`, `closed_at` set) — hooked into the payment-application flow |

`CollectionActivity` — `activity_type` ∈ `call`/`sms`/`email`/`visit`/`promise_to_pay`/`other`.
`promised_amount` / `promised_date` / `promise_status` (`pending`/`kept`/`broken`)
are only populated for `promise_to_pay`; **null for every other type**
(`promise_to_pay` without an amount + date → 422).

| Endpoint | Roles |
|---|---|
| `POST /collections/cases/{id}/activities` | `collections_officer`, `admin` |
| `GET /collections/cases` (filter `status`, `contract_id`) | `collections_officer`, `credit_manager`, `admin` |
| `GET /collections/cases/{id}` (+ activity history) | same, **or the owning `customer`** |

## Maker-checker approval workflow (Step 6)

Generic `ApprovalRequest` (`action_type`, `entity_type`, `entity_id`,
`requested_by`, `payload` JSON, `status` `pending`/`approved`/`rejected`,
`decided_by`, `decided_at`, `decision_notes`).

**Core rule, enforced in [app/services/approvals.py](app/services/approvals.py)
(not just convention): `decided_by` must never equal `requested_by`.** Approving
or rejecting your own request → **409**, whatever your role (including `admin`).

Actions that run through it:

- **Late-fee waiver** — `POST /late-fees/{id}/request-waiver` `{reason}`
  (`finance_officer`, `credit_manager`, `admin`) creates a pending request and
  changes **nothing**. On approval → `LateFeeCharge.status = waived` (which
  removes it from the receivable's late-fee balance).
- **Config parameter change** — see the behaviour change below.
- **Reconciliation manual match** *(P0-5)* —
  `POST /reconciliation/exceptions/{id}/request-match` `{payment_id, reason}`
  (`action_type=reconciliation.manual_match`). On approval → the bank line is
  linked to the payment, the payment is `reconciled`, and the exception is
  `resolved`.

| Endpoint | Roles |
|---|---|
| `GET /approvals` (filter `status`, `action_type`) | `finance_officer`, `credit_manager`, `admin` |
| `POST /approvals/{id}/approve` | `finance_officer`, `credit_manager`, `admin` — 409 if you are the requester; on success executes the action |
| `POST /approvals/{id}/reject` `{reason}` | `finance_officer`, `credit_manager`, `admin` — action never executes |

*(`finance_officer` was added to the decider roles in P0-5 so they can approve
`reconciliation.manual_match`; no test asserted they could not decide. The
maker ≠ checker rule is unchanged.)*

### ⚠️ Deliberate behaviour change: config updates are now two-step

**Step 5:** `PUT /config/parameters/{key}` applied the new value immediately
(200) and wrote a `config.updated` audit event.

**Step 6 onward:** the same `PUT` (still `admin`-only to request) returns **202**
with a **pending `ApprovalRequest`** (`action_type=config.update`, the proposed
value in `payload`). **Nothing changes** until a *different* `credit_manager` or
`admin` calls `POST /approvals/{id}/approve` — at which point `ConfigService`
applies the value and the existing `config.updated` audit event fires, now with
an `approval_request_id`. The internal `ConfigService.set(...)` path (used by
seeding and by tests via the `set_config` fixture) is unchanged.

---

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/customers` | Create a customer **and** profile in one call |
| `GET` | `/customers/{id}` | Fetch a customer with profile |
| `GET` | `/customers/{id}/exposure` | **P0-4** — aggregate outstanding across all non-closed contracts + per-contract breakdown (`credit_officer`/`credit_manager`/`finance_officer`/`admin`, or the owning `customer`) |
| `GET` | `/customers?search=` | **Step 10** — partial, case-insensitive match on `name` OR `national_id`. Compact rows (id, name, national_id, status, risk_score) |
| `POST` | `/products` | Create a product (cash price only). **Step 10:** opening `stock_quantity` defaults from `default_initial_stock_quantity` unless given explicitly |
| `GET` | `/products/{id}` | Fetch a product |
| `GET` | `/products?search=` | **Step 10** — partial match on `name` OR `category`; **omit `search` → every product** (Inventory screen needs the full list). Includes `stock_quantity`/`reserved_quantity`/`available_quantity` |
| `POST` | `/products/{id}/stock-adjustment` | **Step 10** — body `{delta, reason}` (`finance_officer`/`admin`); 422 if it would drop `stock_quantity` below `reserved_quantity`; writes an `AuditEvent`. Not maker-checker gated |
| `POST` | `/applications` | Create an application (`channel` required: `online` \| `branch`); starts as `draft` |
| `POST` | `/applications/{id}/submit` | `draft → submitted → under_assessment` → run assessment → `approved`/`rejected`/`referred` |
| `POST` | `/applications/{id}/review` | **P0-2** — manual verification of a `referred` application (`credit_officer`/`credit_manager`/`admin`). Body `{decision, reason}` → `approved`/`rejected`/`draft` |
| `GET` | `/applications?status=…` | **Step 9** — compact list for the review queue (id, customer_id, product_id, requested_amount, status, `submitted_at`). `credit_officer`/`credit_manager`/`admin`. Deliberately narrow — not a general listing surface |
| `GET` | `/applications/{id}` | Application with current status + assessment result/reasons |
| `POST` | `/applications/{id}/offer` | **Step 2** — price an **approved** application → `InstallmentOffer`. Body: `{down_payment_amount, tenor_months?}`. Supersedes any prior open offer. **P0-3:** re-checks affordability against the real peak installment → **422** if it breaches `max_dbr` and `offer_affordability_gate_mode=block`. **Step 10:** also **422** if the product's `available_quantity <= 0` |
| `GET` | `/offers/{id}` | **Step 2** — offer with pricing + schedule preview |
| `POST` | `/offers/{id}/accept` | **Step 2** — body `{down_payment_confirmed, down_payment_reference?, down_payment_amount?}`. On `true` → creates Sales Order + Contract + Schedule. **Step 10:** deducts one unit of `stock_quantity` (the contract-creation deduction-point default) |
| `GET` | `/contracts/{id}` | **Step 2** — contract with sales order + installments (+ paid amounts & late fees from Step 3) |
| `POST` | `/contracts/{id}/confirm-delivery` | **Step 2** — Contract `created` → `active` |
| `POST` | `/contracts/{id}/payments` | **Step 3** — record a payment. Body `{amount, external_reference}`. Idempotent per `external_reference`; runs the allocation waterfall |
| `GET` | `/contracts/{id}/receivable` | **Step 3** — outstanding principal / profit / late fees (kept separate) + installment counts |
| `POST` | `/jobs/assess-overdue` | **Step 3** — manual trigger. Body `{as_of?}`. Marks overdue installments, assesses late fees |
| `GET` | `/contracts/{id}/settlement-quote` | **Step 4** — early-payoff quote (computes only). 409 if not `active` / already `closed` |
| `POST` | `/contracts/{id}/settle` | **Step 4** — body `{amount, external_reference}`. Re-checks amount vs fresh quote, then closes (`reason=early_settlement`) |
| `POST` | `/contracts/{id}/cancel` | **Step 4** — pre-delivery only. Body `{notes?}`. 409 if `active` (→ `/return`) or `closed`. **Step 10:** releases the deducted unit back to `stock_quantity` |
| `POST` | `/contracts/{id}/return` | **Step 4** — post-delivery only. Body `{notes?}`. 409 if `created` (→ `/cancel`) or `closed`. **Step 10:** releases the deducted unit back to `stock_quantity` |
| `GET` | `/config/parameters` | List business-rule parameters |
| `PUT` | `/config/parameters/{key}` | **Step 6** — *request* a change → **202** + pending `ApprovalRequest` (no longer applies immediately) |
| `POST` | `/auth/login` | **Step 5** — username + password → JWT (open, no token) |
| `POST` | `/auth/register` | **Step 5** — create a user (**admin only**) |
| `GET` | `/auth/me` | **Step 5** — current token's user id / username / role |
| `GET` | `/audit/events` | **Step 5** — audit log, filterable (**admin / credit_manager**) |
| `GET` | `/collections/cases` · `GET /collections/cases/{id}` | **Step 6** — cases + activity history |
| `POST` | `/collections/cases/{id}/activities` | **Step 6** — log a call / SMS / visit / promise-to-pay |
| `POST` | `/late-fees/{id}/request-waiver` | **Step 6** — body `{reason}` → pending `ApprovalRequest` |
| `GET` | `/approvals` | **Step 6** — list, filter `status` / `action_type` |
| `POST` | `/approvals/{id}/approve` · `/approvals/{id}/reject` | **Step 6** — decide (409 if you are the requester); approve executes the action |
| `POST` | `/reconciliation/bank-lines` | **P0-5** — mock bank-feed import, one line: `{bank_reference, amount, value_date}` (`finance_officer`/`admin`) |
| `POST` | `/reconciliation/run` | **P0-5** — match unprocessed bank lines against `unreconciled` payments; idempotent. Returns `{lines_processed, matched, exceptions_created}` |
| `GET` | `/reconciliation/exceptions` | **P0-5** — list, filter `status` (`open`/`resolved`) (`finance_officer`/`credit_manager`/`admin`) |
| `POST` | `/reconciliation/exceptions/{id}/request-match` | **P0-5** — body `{payment_id, reason}` → pending `ApprovalRequest` (`reconciliation.manual_match`); a *different* approver performs the match |
| `GET` | `/reconciliation/status` | **P0-5** — portfolio counts: payments by reconciliation status, open/resolved exceptions, unmatched lines (`finance_officer`/`admin`) |
| `GET` | `/contracts/{id}/receivable` | *(P0-5)* now also returns `reconciliation_summary` — this contract's payments counted by reconciliation status (all other figures unchanged) |
| `GET` | `/accounting/events` | **G-07** — list accounting events, filter `event_type` / `accounting_status` / `contract_id` (`finance_officer`/`admin`) |
| `POST` | `/jobs/post-accounting-events` | **G-07** — on-demand: post every non-`posted` event via the mock ERP adapter; idempotent (**admin**) |
| `GET` | `/reports/contracts` | **Step 11** — general contract list; filters `status`/`customer_id`/`product_id`/`date_from`/`date_to`, `limit`/`offset`, `?format=csv` (`finance_officer`/`credit_manager`/`admin`) |
| `GET` | `/reports/profitability` | **Step 11** — contractual / recognized / unearned profit, by tenor & by category; filters `date_from`/`date_to`/`product_id` |
| `GET` | `/reports/summary/{executive,operations,portfolio,collections,credit-risk}` | **Step 11** — one server-side aggregate per Executive-Dashboard tab |
| `GET` | `/customers?search=` · `/products?search=` · `/collections/cases` | **Step 11/13** — `?format=csv\|xlsx\|pdf` export on the existing directory / case-list endpoints; `/collections/cases` also gains `date_from`/`date_to` (on `opened_at`) |
| `GET` | `/reports/customers/by-risk` · `/reports/customers/by-exposure` | **Step 13** — customer sub-reports (risk band via the assessment thresholds; full ranked exposure list via the P0-4 calc) |
| `GET` | `/reports/products/by-availability` · `/reports/products/by-category` | **Step 13** — product sub-reports (available vs sold-out; per-category counts + stock totals) |
| `GET` | `/reports/contracts/by-status` · `/reports/contracts/by-channel` | **Step 13** — contract sub-reports (status counts; origination-channel counts) |
| `GET` | `/reports/collections/{status-summary,promise-performance,late-fees-summary}` | **Step 13** — collections sub-reports |
| `GET` | `/reports/aging` | **Step 13** — overdue installments by DPD bucket; `?bucket=<index>` drills into one bucket's installment list |
| — | *all `/reports/*`* | **Step 13** — `?format=csv\|xlsx\|pdf` on every report endpoint; unknown format → 422 |

### Example

> **Auth:** get a token first and pass `-H "$AUTH"` on every call below
> (`admin` works for all of them). The `POST /products` step also needs it.
>
> ```bash
> BASE=http://localhost:8000
> TOKEN=$(curl -s -X POST $BASE/auth/login -H 'content-type: application/json' \
>   -d '{"username":"admin","password":"admin"}' \
>   | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
> AUTH="Authorization: Bearer $TOKEN"
> ```

```bash
BASE=http://localhost:8000

# 1. customer + profile
CUST=$(curl -s -X POST $BASE/customers -H "$AUTH" -H 'content-type: application/json' -d '{
  "name": "Sara N.", "national_id": "299010112345",
  "phone": "+96550001111", "email": "sara@example.com",
  "risk_score": 700,
  "profile": {"employer_name": "ACME", "employment_type": "full_time",
              "monthly_income": 5000, "existing_monthly_obligations": 200,
              "address_line": "1 Salem St", "city": "Kuwait City"}
}' | tee /dev/stderr | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 2. product (cash price only)
PROD=$(curl -s -X POST $BASE/products -H 'content-type: application/json' -d '{
  "name": "55\" TV", "category": "electronics", "cash_price": 1200,
  "installment_eligible": true
}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 3. application (draft)
APP=$(curl -s -X POST $BASE/applications -H 'content-type: application/json' -d "{
  \"customer_id\": $CUST, \"product_id\": $PROD,
  \"requested_amount\": 1200, \"requested_tenor_months\": 12, \"channel\": \"online\"
}" | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 4. submit -> triggers assessment (needs to land on "approved" to continue)
curl -s -X POST $BASE/applications/$APP/submit | python -m json.tool

# 5. read back status + reasons
curl -s $BASE/applications/$APP | python -m json.tool

# --- Step 2: offer -> contract ---

# 6. generate a priced offer (down payment >= 15% of cash price)
OFFER=$(curl -s -X POST $BASE/applications/$APP/offer -H 'content-type: application/json' \
  -d '{"down_payment_amount": 300}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 7. inspect the offer + declining-balance schedule preview
curl -s $BASE/offers/$OFFER | python -m json.tool

# 8. accept with down payment confirmed -> Sales Order + Contract + Schedule
CONTRACT=$(curl -s -X POST $BASE/offers/$OFFER/accept -H 'content-type: application/json' \
  -d '{"down_payment_confirmed": true, "down_payment_reference": "DP-REF-123"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["contract_id"])')

# 9. confirm delivery -> Contract status "active"
curl -s -X POST $BASE/contracts/$CONTRACT/confirm-delivery | python -m json.tool

# --- Step 3: payments, overdue, receivable ---

# 10. record a payment (idempotency key required); runs the allocation waterfall
curl -s -X POST $BASE/contracts/$CONTRACT/payments -H 'content-type: application/json' \
  -d '{"amount": 87.46, "external_reference": "PAY-0001"}' | python -m json.tool

# 11. the outstanding Receivable (principal + profit; late fees shown separately)
curl -s $BASE/contracts/$CONTRACT/receivable | python -m json.tool

# 12. assess overdue installments / late fees (as_of lets you simulate a run date)
curl -s -X POST $BASE/jobs/assess-overdue -H 'content-type: application/json' \
  -d '{"as_of": "2026-12-15"}' | python -m json.tool

# --- Step 4: early settlement (or cancel / return) ---

# 13. get an early-payoff quote (nothing is charged)
PAYOFF=$(curl -s $BASE/contracts/$CONTRACT/settlement-quote -H "$AUTH" | tee /dev/stderr \
  | python -c 'import sys,json;print(json.load(sys.stdin)["final_payoff_amount"])')

# 14. settle for exactly the quoted amount -> ContractClosure, status "closed"
curl -s -X POST $BASE/contracts/$CONTRACT/settle -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"amount\": $PAYOFF, \"external_reference\": \"SETTLE-0001\"}" | python -m json.tool

# 15. the audit trail for this contract (admin / credit_manager)
curl -s "$BASE/audit/events?entity_type=installment_contract&entity_id=$CONTRACT" \
  -H "$AUTH" | python -m json.tool

# (alternatively, before delivery:  POST /contracts/$CONTRACT/cancel
#  or, after delivery instead of settling:  POST /contracts/$CONTRACT/return )
```

---

## Tests

```bash
pip install -r requirements.txt
pytest
```

The suite runs against **SQLite by default** (no DB server needed); the models
use only portable column types, so the same ORM/service code is exercised. To run
against Postgres:

```bash
export TEST_DATABASE_URL="postgresql+psycopg2://retail:retail@localhost:5544/retail_credit_test"
pytest
```

**Step 1** — assessment:

- `test_application_approved_under_default_config`
- `test_rejected_when_income_below_configured_minimum`
- `test_referred_when_dbr_threshold_breached`
- `test_changing_min_income_flips_outcome` / `test_changing_max_dbr_flips_outcome` —
  identical application, one config value changed, different decision
- plus customer/profile separation, application lifecycle, status guards, precedence, config API

**Step 2** — pricing & offer flow ([tests/test_pricing.py](tests/test_pricing.py),
[tests/test_offer_flow.py](tests/test_offer_flow.py)):

- `test_schedule_reconciles_exactly` — schedule sums to `amount_financed` +
  `total_profit` with zero drift, for 12 / 18 / 24 / 36-month tenors
- `test_profit_recognition_declines_over_time` — profit-per-installment never
  increases, and `profits[0] > profits[-1]`, for two tenors
- `test_full_flow_offer_to_active_contract` — approved application → offer →
  accept with down payment → Sales Order + Contract + 12 installments → confirm
  delivery → Contract `active`
- `test_offer_requires_approved_application` — rejected application → offer 409s
- `test_rate_table_config_change_changes_total_profit` — editing the tenor→rate
  table changes the resulting offer's `total_profit` (rate table isn't hardcoded)
- plus down-payment-minimum enforcement, unsupported tenor, and
  accept-without-confirmation creating nothing

**Step 3** — payments, allocation & overdue ([tests/test_allocation.py](tests/test_allocation.py),
[tests/test_payments_flow.py](tests/test_payments_flow.py),
[tests/test_overdue.py](tests/test_overdue.py)):

- allocation: single full payment, partial payment, and the
  **payment-spans-two-installments** case proving oldest-first beats
  profit-before-principal
- `test_idempotent_replay_does_not_double_allocate` — replaying an
  `external_reference` returns the original and doesn't re-allocate
- overdue: inside grace → no fee; past grace → **exactly 2%** of that
  installment's total; running the job twice doesn't double-charge
- `test_grace_period_config_change_changes_whether_fee_triggers`
- `test_late_fee_is_paid_before_profit_and_principal` + receivable stays
  reconciled (principal + profit drop by exactly what was allocated)

**Step 4** — closure ([tests/test_closure.py](tests/test_closure.py)):

- `test_settlement_quote_reconciles_on_partially_paid_contract` — `principal +
  late fees + profit_still_charged == final_payoff`, and `rebate + still_charged
  == unearned_profit_total`
- `test_settle_with_exact_quoted_amount_closes_and_zeroes_receivable`
- `test_settle_with_wrong_amount_is_rejected` (422, contract untouched)
- `test_rebate_pct_config_change_changes_quoted_payoff`
- `test_cancel_before_delivery_computes_refund_and_closes` /
  `test_cancel_after_delivery_returns_409_pointing_at_return`
- `test_return_after_delivery_computes_adjustment_and_closes` (echoes
  `ownership_transfers_on_delivery`) / `test_return_before_delivery_returns_409_pointing_at_cancel`
- `test_closed_contract_cannot_be_closed_again` (409 on settle/cancel/return/quote)
- `test_exactly_one_closure_per_contract`

**Step 5** — auth, RBAC & audit ([tests/test_auth.py](tests/test_auth.py),
[tests/test_rbac.py](tests/test_rbac.py), [tests/test_audit.py](tests/test_audit.py)):

- login succeeds / wrong password → 401 / unknown user → 401; bad token → 401
- `POST /auth/register` is admin-only (403 for others, 401 unauthenticated)
- no token → 401 and valid-token-wrong-role → 403, grouped by role pattern
- correct role succeeds — Step 1–4 flows re-run with tokens (wiring intact)
- `customer` can read their own application / contract receivable, **someone
  else's → 403**
- `contract.settled` / `config.updated` / `overdue.assessed` write a matching
  `AuditEvent`; `GET /audit/events` filters and rejects non-admin/manager
- (all existing Step 1–4 tests now run through an admin-authenticated client)

**Step 6** — collections & maker-checker ([tests/test_collections.py](tests/test_collections.py),
[tests/test_approvals.py](tests/test_approvals.py)):

- overdue assessment opens **exactly one** case per contract, even run repeatedly
- a payment that clears all overdue installments **closes** the case
- `promise_to_pay` stores the promise fields; other activity types leave them
  null (and `promise_to_pay` without amount + date → 422)
- RBAC: `sales_employee` can't log a collections activity (403), a
  `collections_officer` can
- you cannot approve/reject **your own** waiver or config request — 409, even as
  `admin`; a *different* eligible user can, and it executes (charge → `waived`,
  or the config value actually changes); rejecting leaves it unchanged
- `sales_employee` cannot approve/reject/list (403)
- **regression**: `PUT /config/parameters` no longer changes the value
  immediately (`test_direct_put_does_not_change_value_without_approval`); the two
  Step 5 config-direct-update tests were rewritten for the new flow

**P0-2** — manual review ([tests/test_manual_review.py](tests/test_manual_review.py)):
a `credit_officer` approves a `referred` app which then proceeds through offer
generation; reject; `return_for_info` → `draft` → resubmit; reviewing a
non-`referred` app → 409; `sales_employee` → 403; the review writes an audit
event.

**P0-1** — immutable ledger ([tests/test_ledger.py](tests/test_ledger.py)): for
full-repayment, delinquency-then-repayment, and early-settlement,
`Σ LedgerEntry` reconciles **exactly** to the existing balance figures
(`Σ profit_recognized + Σ profit_rebated == Σ Installment.profit_paid`, etc.);
and a bogus ledger row does **not** change what `GET .../receivable` returns
(reads are not cut over).

**P0-3** — affordability ([tests/test_affordability.py](tests/test_affordability.py)):
changing only the rate table moves the initial `estimated_installment` (proving
the table, not a flat proxy, drives it); different tenors give different
estimates and can flip the decision; an unpriceable tenor falls back to
`flat_factor`; an affordable offer proceeds and records a passing re-check; a
small down payment that breaches the real DBR is **blocked (422)** even though
the application passed the initial estimate; `warn_only` lets the same offer
through but still records the failure; a blocked offer writes an audit event.

**P0-4** — exposure ([tests/test_exposure.py](tests/test_exposure.py)):
`compute_exposure` with 0 / 1 / N contracts sums to the per-contract Receivable
totals; a customer with no other contracts is unaffected; existing balance +
new request over `max_customer_exposure_kwd` → `referred`; **`closed` contracts
are excluded**; changing `max_customer_exposure_kwd` flips an otherwise-identical
application; `GET /customers/{id}/exposure` matches a manual 2-contract sum;
RBAC + owning-customer.

**P0-5** — bank reconciliation ([tests/test_reconciliation.py](tests/test_reconciliation.py)):
exact-reference auto-reconcile; fallback amount + value-date match; `no_match`
opens an `open` exception; `amount_mismatch` also flags the payment `exception`;
`gateway_reference` is matched when set; the tolerance window is config-driven;
**`POST /reconciliation/run` twice never re-matches or duplicates an exception**;
manual match needs a *different* approver (409 for the requester); a second
approver reconciles the payment and resolves the exception; a pending request
blocks a second; `GET /reconciliation/status` counts a mixed scenario;
`GET /contracts/{id}/receivable` gains `reconciliation_summary` with every
existing figure unchanged; RBAC.

**G-07** — accounting-event boundary ([tests/test_accounting_events.py](tests/test_accounting_events.py)):
delivery emits `contract_activated` (sale price) + `down_payment_received`; a
payment emits `payment_received` + `profit_recognized` for *that allocation's*
profit (not the full schedule); a late-fee charge and its waiver each emit one
event; settlement / cancellation / return each emit one event with the correct
signed amount; **replaying a payment or re-running the overdue job never
duplicates an event**; the posting job moves `pending` → `posted` with a
`MOCK-GL-…` reference and running it twice never re-posts; RBAC.

**Step 10** — search + stock ([tests/test_inventory.py](tests/test_inventory.py)):
customer search matches name or national ID; product search matches name or
category, and returns the stock fields; **omitting `search` on `/products`
returns every product**; a positive stock adjustment increases
`stock_quantity`; a negative one that would drop below `reserved_quantity` is
**rejected (422)** and nothing changes; every adjustment writes an `AuditEvent`
findable via the existing audit endpoint; accepting an offer deducts one unit;
cancellation and return each release it back; **offer generation on an
out-of-stock product is rejected (422)**; a plain end-to-end contract still
works with stock present; RBAC (search + adjustment).

**Step 11** — reporting ([tests/test_reports.py](tests/test_reports.py)):
`/reports/contracts` filters by status and by date range; **profitability totals
reconcile** — `recognized + unearned == contractual` for a known contract,
before and after a payment, and per tenor bucket; each of the 5 summary
endpoints returns the right shape and the right aggregates from a seeded
scenario (portfolio DPD bucketing verified by backdating one installment;
credit-risk bands reuse the assessment thresholds); CSV export on
`/reports/contracts`, `/customers`, `/products` and `/collections/cases` returns
a well-formed CSV (correct headers, correct row count); all summary + report
endpoints are role-gated.

**Step 13** — report sub-categories + export ([tests/test_report_subcategories.py](tests/test_report_subcategories.py)):
each new by-X / summary endpoint groups correctly from a seeded scenario
(customers by risk / exposure, products by availability / category, contracts by
status / channel, collections status / promise / late-fees); the **Aging report
buckets match a hand-computed grouping** for installments backdated to DPD 10 /
45 / 120, and `?bucket=<i>` returns only that bucket (out-of-range → 422); all
three export formats produce a non-empty correctly-shaped file (CSV headers +
row count, a valid `.xlsx` zip container, a `%PDF-` document), CSV shape checked
on several more endpoints, the directory endpoints gained xlsx/pdf, unknown
format → 422; every new endpoint is role-gated.

### Frontend (Steps 7, 9, 10, 11 & 13) — `cd frontend && npm test`

Vitest + React Testing Library, API mocked at `fetch`:

- **`src/test/login.test.tsx`** — sign in with valid credentials → lands on the
  dashboard, token in `localStorage`; wrong password → error, stays on login
- **`src/test/application-assessment.test.tsx`** — submit an application →
  the screen renders the decision, `debt_burden_ratio` (4dp), and each
  triggered-rule reason; an approved app shows "no rules triggered" + the
  "generate an offer" link
- **`src/test/offer-schedule.test.tsx`** — the schedule table renders each
  installment and shows **profit declining row-over-row** with principal flat,
  and totals the columns
- **`src/test/review-queue.test.tsx`** *(Step 9)* — the queue lists referred
  applications; the review form calls `POST /applications/{id}/review` with the
  exact `{decision, reason}` payload and then links to the offer
- **`src/test/exposure.test.tsx`** *(Step 9)* — the exposure panel renders the
  per-contract breakdown and totals from a mocked response
- **`src/test/reconciliation.test.tsx`** *(Step 9)* — adding a bank line then
  running matching updates the displayed status counts
- **`src/test/approvals.test.tsx`** *(Step 9)* — a row whose `requested_by` is
  the current user has its decide buttons disabled; a different user's row does not
- **`src/test/contract-closure.test.tsx`** *(Step 9)* — Cancel shows only while
  `created`, Return only while `active`, and neither once a `closure` is present
- **`src/test/config.test.tsx`** *(Step 9)* — editing a parameter shows the
  pending-approval message, not an immediate "saved"
- **`src/test/nav-roles.test.tsx`** *(Step 9)* — nav items are role-gated
  (`sales_employee` / `finance_officer` / `admin` spot-checks)
- **`src/test/inventory.test.tsx`** *(Step 10)* — a positive adjustment updates
  the table immediately; a negative one below `reserved_quantity` is rejected
  and shows the error, table unchanged; the recent-adjustments panel reflects
  the existing audit endpoint; `sales_employee` does not see "Inventory" in nav
- **`src/test/dashboard.test.tsx`** *(Step 11)* — the 5 tabs render `MetricTile`s
  from mocked summary responses and switch (Portfolio DPD table, Credit-Risk
  top-exposure list); the flow panels stay below; a non-privileged role sees no
  tabs but keeps the panels
- **`src/test/reports.test.tsx`** *(Step 11)* — Reports Center runs a filtered
  Contracts report (URL carries `status=active`) and a Profitability report
  (totals reconcile on screen); "Export CSV" hits the endpoint with
  `format=csv`; the Customers/Products/Collections categories render a link to
  the existing screen, not a duplicate search box
- **`src/test/reports-subcategories.test.tsx`** *(Step 13)* — all six category
  cards render with their sub-report pill nav; selecting a by-X sub-report runs
  it and shows the grouped rows; the export group offers CSV / Excel / PDF and
  each hits the endpoint with the right `format`; the Aging report shows buckets
  and drills into one; the three links-out are preserved for the literal
  full-list views

---

## Project layout

```
app/
  core/        config (env + JWT settings), database, date helpers,
               security (bcrypt + JWT), auth (RBAC dependencies)
  models/      Customer, CustomerProfile, Product, CreditApplication,
               AssessmentResult, ConfigParameter,
               InstallmentOffer, SalesOrder, InstallmentContract,
               PaymentSchedule, Installment,
               Payment, PaymentAllocation, LateFeeCharge, ContractClosure,
               User, AuditEvent,
               CollectionCase, CollectionActivity, ApprovalRequest,
               LedgerEntry (P0-1, write-only),
               BankStatementLine, ReconciliationException (P0-5),
               AccountingEvent (G-07)
  schemas/     Pydantic request/response models
  services/    config_service (externalised rules), assessment (Step 1 engine),
               pricing (Step 2 declining-balance engine), offers (offer→contract),
               allocation (Step 3 pure waterfall), payments, overdue, receivable,
               closure (Step 4 settlement / cancellation / return),
               audit, users, collections (Step 6), approvals (Step 6),
               ledger (P0-1 dual-write helper), exposure (P0-4),
               reconciliation (P0-5 matching engine),
               accounting (G-07 event generation + posting job),
               erp_adapter (G-07 mock GL boundary),
               reports (Step 11 aggregates + Step 13 sub-reports/aging
                        + csv/xlsx/pdf export), errors
  api/         auth, customers (+ exposure P0-4, + search/export Step 10-13), products
               (+ search/stock-adjustment/export Step 10-13),
               applications (+ manual review P0-2, + list Step 9), offers (+ contracts),
               payments (+ receivable + jobs), closure, config, audit,
               collections (+ export/date filters Step 11-13), approvals,
               reconciliation (P0-5), accounting (G-07),
               reports (Step 11 + Step 13) routers
  main.py      FastAPI app + startup seeding (config params + bootstrap admin)
alembic/       migrations (0001_initial … 0011_product_stock)
config/        business_rules.yaml  (fictitious placeholder defaults, Steps 1–4)
scripts/       seed_config.py, create_admin.py
tests/         backend pytest suite

frontend/      React + Vite staff web app (Steps 7, 9, 10, 11 & 13)
  src/api/     fetch wrapper (token + 401 handling, + downloadFile for CSV/xlsx/pdf), types
  src/auth/    AuthContext, RequireAuth route guard
  src/components/  Shell, ScheduleTable, AssessmentPanel, StatusBadge,
                   MetricTile (Step 11, + icon Step 13), ui
  src/pages/   Login, Dashboard (5-tab Executive Dashboard, Step 11
                        + icon/density polish Step 13),
               CreateCustomer, CreateProduct,
               NewApplication, Offer, Contract (+ closure actions),
               Customer (+ exposure), ReviewQueue, Reconciliation,
               Approvals, Config, AuditLog  (Step 9),
               CustomerDirectory, ProductDirectory, Snapshot,
               Collections (+ case detail), Inventory  (Step 10),
               Reports (Reports Center — 6 categories, per-category
                        sub-reports, csv/xlsx/pdf export; Steps 11 & 13)
  src/styles/  tokens.css (the colour system) + app.css
  src/test/    Vitest + RTL
```
