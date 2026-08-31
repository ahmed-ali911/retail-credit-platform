# Retail Credit & Installment Sales Platform

**Steps 1–7 — a FastAPI backend (application → assessment → offer → contract → payments → collections → maker-checker, behind JWT auth + RBAC + audit) and a React staff web app for the core flow.** Plus post-[assessment](docs/enterprise-assessment.md) P0 fixes: referred → manual verification (P0-2), an immutable financial ledger in dual-write (P0-1), affordability re-check at offer time (P0-3), and company-wide customer exposure aggregation (P0-4).

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

### Explicitly out of scope (later steps)

**Backend:** maker-checker on contract settlement / cancellation / return,
collections escalation rules, SMS/email actually being sent, promise-to-pay
follow-up reminders, external IdP / OAuth, refresh tokens, actual refund payment
execution, ECL / provisioning, an **actual scheduled job** (assess-overdue is
still manually triggered).

**Frontend (Step 7):** Collections UI, maker-checker approval UI, settlement /
cancellation / return UI, config-parameter management UI, audit-log viewer, a
customer-facing self-service portal, visual polish, mobile responsiveness,
i18n / Arabic UI (backend and this UI are English-only for now). Token storage
is `localStorage` — acceptable for an internal tool this step, **to be
reconsidered** (httpOnly cookie / silent refresh) in a later step.

## Post-assessment P0 fixes (P0-1, P0-2, P0-3, P0-4)

Following the enterprise review in
[docs/enterprise-assessment.md](docs/enterprise-assessment.md):

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

---

## Tech stack

**Backend** Python 3.11 · FastAPI · PostgreSQL · SQLAlchemy 2.x · Alembic · PyJWT · bcrypt · Pytest · Docker Compose
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

*(P0-4 added no migration — the exposure config is two YAML-seeded `config_parameters` rows.)*

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
| `GET /customers/{id}/exposure` *(P0-4)* | `credit_officer`, `credit_manager`, `finance_officer`, `admin`, **or the owning `customer`** |
| `POST /applications`, `POST /applications/{id}/submit` | `sales_employee`, `customer`, `admin` |
| `POST /applications/{id}/review` *(P0-2)* | `credit_officer`, `credit_manager`, `admin` |
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
| `GET /approvals`, `POST /approvals/{id}/approve` / `/reject` *(Step 6)* | `credit_manager`, `admin` |
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

Two actions run through it this step:

- **Late-fee waiver** — `POST /late-fees/{id}/request-waiver` `{reason}`
  (`finance_officer`, `credit_manager`, `admin`) creates a pending request and
  changes **nothing**. On approval → `LateFeeCharge.status = waived` (which
  removes it from the receivable's late-fee balance).
- **Config parameter change** — see the behaviour change below.

| Endpoint | Roles |
|---|---|
| `GET /approvals` (filter `status`, `action_type`) | `credit_manager`, `admin` |
| `POST /approvals/{id}/approve` | `credit_manager`, `admin` — 409 if you are the requester; on success executes the action |
| `POST /approvals/{id}/reject` `{reason}` | `credit_manager`, `admin` — action never executes |

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
| `POST` | `/products` | Create a product (cash price only) |
| `GET` | `/products/{id}` | Fetch a product |
| `POST` | `/applications` | Create an application (`channel` required: `online` \| `branch`); starts as `draft` |
| `POST` | `/applications/{id}/submit` | `draft → submitted → under_assessment` → run assessment → `approved`/`rejected`/`referred` |
| `POST` | `/applications/{id}/review` | **P0-2** — manual verification of a `referred` application (`credit_officer`/`credit_manager`/`admin`). Body `{decision, reason}` → `approved`/`rejected`/`draft` |
| `GET` | `/applications/{id}` | Application with current status + assessment result/reasons |
| `POST` | `/applications/{id}/offer` | **Step 2** — price an **approved** application → `InstallmentOffer`. Body: `{down_payment_amount, tenor_months?}`. Supersedes any prior open offer. **P0-3:** re-checks affordability against the real peak installment → **422** if it breaches `max_dbr` and `offer_affordability_gate_mode=block` |
| `GET` | `/offers/{id}` | **Step 2** — offer with pricing + schedule preview |
| `POST` | `/offers/{id}/accept` | **Step 2** — body `{down_payment_confirmed, down_payment_reference?, down_payment_amount?}`. On `true` → creates Sales Order + Contract + Schedule |
| `GET` | `/contracts/{id}` | **Step 2** — contract with sales order + installments (+ paid amounts & late fees from Step 3) |
| `POST` | `/contracts/{id}/confirm-delivery` | **Step 2** — Contract `created` → `active` |
| `POST` | `/contracts/{id}/payments` | **Step 3** — record a payment. Body `{amount, external_reference}`. Idempotent per `external_reference`; runs the allocation waterfall |
| `GET` | `/contracts/{id}/receivable` | **Step 3** — outstanding principal / profit / late fees (kept separate) + installment counts |
| `POST` | `/jobs/assess-overdue` | **Step 3** — manual trigger. Body `{as_of?}`. Marks overdue installments, assesses late fees |
| `GET` | `/contracts/{id}/settlement-quote` | **Step 4** — early-payoff quote (computes only). 409 if not `active` / already `closed` |
| `POST` | `/contracts/{id}/settle` | **Step 4** — body `{amount, external_reference}`. Re-checks amount vs fresh quote, then closes (`reason=early_settlement`) |
| `POST` | `/contracts/{id}/cancel` | **Step 4** — pre-delivery only. Body `{notes?}`. 409 if `active` (→ `/return`) or `closed` |
| `POST` | `/contracts/{id}/return` | **Step 4** — post-delivery only. Body `{notes?}`. 409 if `created` (→ `/cancel`) or `closed` |
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

### Frontend (Step 7) — `cd frontend && npm test`

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
               LedgerEntry (P0-1, write-only)
  schemas/     Pydantic request/response models
  services/    config_service (externalised rules), assessment (Step 1 engine),
               pricing (Step 2 declining-balance engine), offers (offer→contract),
               allocation (Step 3 pure waterfall), payments, overdue, receivable,
               closure (Step 4 settlement / cancellation / return),
               audit, users, collections (Step 6), approvals (Step 6),
               ledger (P0-1 dual-write helper), exposure (P0-4), errors
  api/         auth, customers (+ exposure, P0-4), products,
               applications (+ manual review, P0-2), offers (+ contracts),
               payments (+ receivable + jobs), closure, config, audit,
               collections, approvals routers
  main.py      FastAPI app + startup seeding (config params + bootstrap admin)
alembic/       migrations (0001_initial … 0008_affordability_recheck)
config/        business_rules.yaml  (fictitious placeholder defaults, Steps 1–4)
scripts/       seed_config.py, create_admin.py
tests/         backend pytest suite

frontend/      React + Vite staff web app (Step 7)
  src/api/     fetch wrapper (token + 401 handling), response types
  src/auth/    AuthContext, RequireAuth route guard
  src/components/  Shell, ScheduleTable, AssessmentPanel, StatusBadge, ui
  src/pages/   Login, Dashboard, CreateCustomer, CreateProduct,
               NewApplication, Offer, Contract
  src/styles/  tokens.css (the colour system) + app.css
  src/test/    Vitest + RTL
```
