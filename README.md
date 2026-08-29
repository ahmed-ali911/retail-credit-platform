# Retail Credit & Installment Sales Platform

**Step 1 — Scaffolding, Customer, Application Origination, Credit Assessment.**

This is a **retail installment-sale** platform, not a cash-loan system. The
company never disburses cash. A customer buys a product on credit terms; a
receivable is created; the customer pays it off in installments.

```
Customer → Product Purchase → Credit Assessment → Installment Sale → Receivable → Collections → Closure
                              ^^^^^^^^^^^^^^^^^^^
                              this repo stops here (Step 1)
```

Everything from **Offer Management / Sales Order / Installment Contract**
onward is deliberately **not** built yet.

---

## What's in Step 1

| Area | Included |
|------|----------|
| Project scaffolding | FastAPI layout, Docker Compose Postgres, Alembic, externalised config |
| Customer | `Customer` + separate `CustomerProfile` (two tables, not merged) |
| Product | Minimal: `id, name, category, cash_price, installment_eligible`. **Cash price only** — no installment price / margin / amortization |
| Application origination | `CreditApplication` as its own entity (never merged with a future Sales Order / Contract) |
| Credit Assessment Engine | Rules-based, every threshold read from config, returns `approved` / `rejected` / `referred` + audit reasons |

### Explicitly out of scope
Offer Management, Sales Order, Installment Contract, Payment Schedule, Payments,
Receivables, Collections, Late Fees, ECL, and any external integration
(bureau / payment gateway / ERP).

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

The initial schema is [alembic/versions/0001_initial.py](alembic/versions/0001_initial.py).

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
| `installment_estimation_factor` | 1.0 | est. installment = `requested_amount * factor / tenor_months` (straight-line, no profit — pricing is a later step) |
| `risk_score_auto_approve_min` | 650 | score ≥ → approve-eligible |
| `risk_score_refer_min` | 600 | 600–649 → **referred**, < 600 → **rejected** |

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

# 4. submit -> triggers assessment
curl -s -X POST $BASE/applications/$APP/submit | python -m json.tool

# 5. read back status + reasons
curl -s $BASE/applications/$APP | python -m json.tool
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

Coverage includes the four required cases:

- `test_application_approved_under_default_config`
- `test_rejected_when_income_below_configured_minimum`
- `test_referred_when_dbr_threshold_breached`
- `test_changing_min_income_flips_outcome` / `test_changing_max_dbr_flips_outcome` —
  identical application, one config value changed, different decision (proves the
  rules are externalised, not hardcoded)

plus customer/profile separation, application lifecycle, status-transition guards,
precedence, and the config API.

---

## Project layout

```
app/
  core/        config (env settings), database engine/session
  models/      Customer, CustomerProfile, Product, CreditApplication,
               AssessmentResult, ConfigParameter
  schemas/     Pydantic request/response models
  services/    config_service (externalised rules), assessment (the engine)
  api/         customers, products, applications, config routers
  main.py      FastAPI app + startup config seeding
alembic/       migrations (0001_initial)
config/        business_rules.yaml  (fictitious placeholder defaults)
scripts/       seed_config.py
tests/
```
