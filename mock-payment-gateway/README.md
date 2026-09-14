# Mock Payment Gateway

A **genuinely separate** FastAPI service that simulates a hosted-checkout
payment provider for the Retail Credit and Installment Sales Platform demo.

This is an entirely simulated training/portfolio project. It is **not**
affiliated with, endorsed by, or claiming to be any real bank or payment
network (KNET or otherwise) — the branding ("Demo Gateway", "Demo Debit
Network") is deliberately generic and original. **No real card numbers,
CVVs, bank credentials, or real payment tokens are ever requested, stored,
or processed.**

## Why a separate service

retail-credit-api treats this gateway exactly like it would treat a real
external payment provider:

- It never reads or writes this service's database directly.
- It creates a checkout session over HTTP (`POST /gateway/checkout-sessions`)
  and later receives a **signed webhook** reporting the outcome — it cannot
  reach in and flip a payment's status on its own say-so.
- This service owns its own SQLite file (`app/database.py`) — a deliberate,
  complete data-isolation boundary from the main app's Postgres database.

## Running it

Via docker compose (from the repo root) — starts alongside `db` and `api`:

```
docker compose up --build
```

The gateway is then reachable at `http://localhost:8100`, and retail-credit-api
is configured (via `GATEWAY_BASE_URL` in docker-compose.yml) to call it at
`http://mock-payment-gateway:8100` over the compose network.

Standalone (no docker), for iterating on this service alone:

```
cd mock-payment-gateway
pip install -r requirements.txt
cp .env.example .env   # then edit DEFAULT_WEBHOOK_URL to point at your local api
uvicorn app.main:app --reload --port 8100
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/gateway/checkout-sessions` | Called by retail-credit-api to open a hosted checkout session |
| GET | `/gateway/checkout/{token}` | The hosted "Demo Gateway" checkout page (HTML) |
| POST | `/gateway/checkout/{token}/simulate` | The customer's one click — fires the signed webhook(s) for the chosen outcome |
| GET | `/gateway/transactions/{reference}` | This gateway's own view of one transaction (by merchant or gateway reference) |
| POST | `/gateway/webhooks/{event_id}/retry` | Manually redeliver a previously-sent webhook event |
| GET | `/gateway/settlement-batches/generate` | This gateway's own daily settlement feed — retail-credit-api pulls this and imports it (`POST /payments/settlement-batches/pull`) |
| GET | `/health` | Liveness check |

See the repo root [`docs/payment-gateway/`](../docs/payment-gateway/) for
the full BRD/FSD, sequence diagrams, and the reconciliation-engine
reuse-decision writeup.

## Simulated outcomes

The checkout page offers nine buttons, each firing a different signed
webhook sequence back to retail-credit-api's
`/integrations/mock-gateway/webhooks`:

| Outcome | Webhook sequence |
|---|---|
| `success_settlement` | AUTHORIZED → CAPTURED → SETTLED |
| `success_authorization_only` | AUTHORIZED |
| `insufficient_funds` | FAILED |
| `customer_cancel` | CANCELLED |
| `timeout` | EXPIRED |
| `delayed_settlement` | AUTHORIZED → CAPTURED immediately, SETTLED after `DELAYED_SETTLEMENT_SECONDS` |
| `duplicate_webhook` | AUTHORIZED → CAPTURED → SETTLED → SETTLED redelivered with the **same** `gateway_event_id` |
| `invalid_signature` | AUTHORIZED sent with a deliberately corrupted signature |
| `settle_then_reverse` | AUTHORIZED → CAPTURED → SETTLED → REVERSED |

## Security

Outbound webhooks are HMAC-signed (`app/security.py`) using the shared
`WEBHOOK_SECRET` — the same scheme retail-credit-api verifies in
`app/services/webhook_security.py` on the other side:

```
signed_payload = f"{timestamp}." + raw_body
signature      = hex(HMAC-SHA256(secret, signed_payload))
header         = "X-Gateway-Signature: t=<timestamp>,v1=<signature>"
```

`WEBHOOK_SECRET` must never be committed for real use — see `.env.example`.
