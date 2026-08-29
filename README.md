# Retail Credit & Installment Sales Platform

**Steps 1–3 — Customer & Assessment → priced Offer → active Contract → payments, allocation, receivable & late fees.**

This is a **retail installment-sale** platform, not a cash-loan system. The
company never disburses cash. A customer buys a product on credit terms; a
receivable is created; the customer pays it off in installments.

```
Customer → Product Purchase → Credit Assessment → Installment Sale → Receivable → Collections → Closure
           └──────────────── Step 1 ────────────┘ └─── Step 2 ───┘ └── Step 3 ──┘
                                                  offer→contract    payments, allocation,
                                                                    overdue, late fees
                                                  (no real payment gateway / collections workflow yet)
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

### Explicitly out of scope (later steps)
Real payment gateway integration, Collections workflow (contact logging,
Promise-to-Pay, Collection Case), Early Settlement / rebate, Cancellation / Return,
**late-fee waiver execution** endpoint (the `waived` status exists but no
maker-checker flow), ECL / provisioning, and an **actual scheduled job** (the
assess-overdue endpoint is manually triggered).

---

## Tech stack

Python 3.11 · FastAPI · PostgreSQL · SQLAlchemy 2.x · Alembic · Pytest · Docker Compose

---

## Running with Docker (recommended)

```bash
docker compose up --build
```

This starts Postgres, runs `alembic upgrade head`, seeds the business-rule
parameters, and serves the API on **http://localhost:8000**.

- Swagger UI: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json
- Health: http://localhost:8000/health

## Running locally without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# start only Postgres from compose
docker compose up -d db

export DATABASE_URL="postgresql+psycopg2://retail:retail@localhost:5432/retail_credit"
alembic upgrade head
python -m scripts.seed_config          # seed business-rule parameters
uvicorn app.main:app --reload
```

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

---

## Business-rule configuration (not hardcoded)

**Choice: a DB-backed table (`config_parameters`), seeded from a version-controlled
YAML file ([config/business_rules.yaml](config/business_rules.yaml)).**

| | YAML file only | **DB table (chosen)** |
|---|---|---|
| Change a threshold | edit file + redeploy | update a row at runtime / via `PUT /config/parameters/{key}` |
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

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/customers` | Create a customer **and** profile in one call |
| `GET` | `/customers/{id}` | Fetch a customer with profile |
| `POST` | `/products` | Create a product (cash price only) |
| `GET` | `/products/{id}` | Fetch a product |
| `POST` | `/applications` | Create an application (`channel` required: `online` \| `branch`); starts as `draft` |
| `POST` | `/applications/{id}/submit` | `draft → submitted → under_assessment` → run assessment → `approved`/`rejected`/`referred` |
| `GET` | `/applications/{id}` | Application with current status + assessment result/reasons |
| `POST` | `/applications/{id}/offer` | **Step 2** — price an **approved** application → `InstallmentOffer`. Body: `{down_payment_amount, tenor_months?}` (tenor defaults to the application's). Supersedes any prior open offer |
| `GET` | `/offers/{id}` | **Step 2** — offer with pricing + schedule preview |
| `POST` | `/offers/{id}/accept` | **Step 2** — body `{down_payment_confirmed, down_payment_reference?, down_payment_amount?}`. On `true` → creates Sales Order + Contract + Schedule |
| `GET` | `/contracts/{id}` | **Step 2** — contract with sales order + installments (+ paid amounts & late fees from Step 3) |
| `POST` | `/contracts/{id}/confirm-delivery` | **Step 2** — Contract `created` → `active` |
| `POST` | `/contracts/{id}/payments` | **Step 3** — record a payment. Body `{amount, external_reference}`. Idempotent per `external_reference`; runs the allocation waterfall |
| `GET` | `/contracts/{id}/receivable` | **Step 3** — outstanding principal / profit / late fees (kept separate) + installment counts |
| `POST` | `/jobs/assess-overdue` | **Step 3** — manual trigger. Body `{as_of?}`. Marks overdue installments, assesses late fees |
| `GET` | `/config/parameters` | List business-rule parameters |
| `PUT` | `/config/parameters/{key}` | Update a business-rule parameter |

### Example

```bash
BASE=http://localhost:8000

# 1. customer + profile
CUST=$(curl -s -X POST $BASE/customers -H 'content-type: application/json' -d '{
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
export TEST_DATABASE_URL="postgresql+psycopg2://retail:retail@localhost:5432/retail_credit_test"
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

---

## Project layout

```
app/
  core/        config (env settings), database engine/session, date helpers
  models/      Customer, CustomerProfile, Product, CreditApplication,
               AssessmentResult, ConfigParameter,
               InstallmentOffer, SalesOrder, InstallmentContract,
               PaymentSchedule, Installment,
               Payment, PaymentAllocation, LateFeeCharge
  schemas/     Pydantic request/response models
  services/    config_service (externalised rules), assessment (Step 1 engine),
               pricing (Step 2 declining-balance engine), offers (offer→contract),
               allocation (Step 3 pure waterfall), payments, overdue, receivable, errors
  api/         customers, products, applications, offers (+ contracts),
               payments (+ receivable + jobs), config routers
  main.py      FastAPI app + startup config seeding
alembic/       migrations (0001_initial, 0002_offers_contracts_schedule,
               0003_payments_late_fees)
config/        business_rules.yaml  (fictitious placeholder defaults + Step 3 rules)
scripts/       seed_config.py
tests/
```
