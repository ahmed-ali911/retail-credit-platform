# Payment Flow — Mock Payment Gateway

Companion to [FSD.md](FSD.md) §2. Four sequence diagrams covering the
scenarios the brief calls out by name, plus the full table of which
simulated checkout outcome fires which webhook sequence.

All diagrams show three real, separately-deployed participants: the
browser (customer or staff), **retail-credit-api**, and the separate
**mock-payment-gateway** service. Nothing here is simplified for the
diagram — this is the literal call sequence in the code.

---

## 1. Successful payment and settlement

```mermaid
sequenceDiagram
    actor Customer as Customer / Staff
    participant App as retail-credit-api
    participant GW as mock-payment-gateway

    Customer->>App: GET /contracts/{id}/payment-options
    App-->>Customer: next installment, overdue, total outstanding, minimum
    Customer->>App: POST /payments/intents (purpose, amount)
    App-->>Customer: PaymentIntent (status=INITIATED)
    Customer->>App: POST /payments/intents/{id}/checkout
    App->>GW: POST /gateway/checkout-sessions
    GW-->>App: checkout_url, expires_at
    App-->>Customer: checkout_url (status=PENDING)

    Customer->>GW: GET /gateway/checkout/{token}  (Demo Gateway page)
    Customer->>GW: POST /gateway/checkout/{token}/simulate (outcome=success_settlement)

    GW->>App: POST /integrations/mock-gateway/webhooks (status=AUTHORIZED, signed)
    App->>App: verify signature, check transition PENDING->AUTHORIZED, apply
    App-->>GW: 200 {processing_status: PROCESSED}

    GW->>App: POST /integrations/mock-gateway/webhooks (status=CAPTURED, signed)
    App->>App: verify, check AUTHORIZED->CAPTURED, apply
    App-->>GW: 200

    GW->>App: POST /integrations/mock-gateway/webhooks (status=SETTLED, signed)
    App->>App: verify, check CAPTURED->SETTLED, apply
    Note over App: status == configured final-allocation status
    App->>App: payments.record_payment() — the SAME engine<br/>POST /contracts/{id}/payments uses
    App->>App: allocate(), update installment balances,<br/>evaluate_promises_after_payment(),<br/>close_case_if_cleared(), emit payment_received<br/>+ profit_recognized + gateway_fee_recognized
    App-->>GW: 200

    GW-->>Customer: 303 redirect to return_url
```

## 2. Failed payment

A decline never has to pass through AUTHORIZED — the gateway can report a
straight `PENDING -> FAILED`, exactly like a real authorization decline.

```mermaid
sequenceDiagram
    actor Customer
    participant App as retail-credit-api
    participant GW as mock-payment-gateway

    Customer->>App: POST /payments/intents, then .../checkout
    App-->>Customer: checkout_url (status=PENDING)
    Customer->>GW: POST .../simulate (outcome=insufficient_funds)

    GW->>App: POST /integrations/mock-gateway/webhooks (status=FAILED, failure_code, signed)
    App->>App: verify, check PENDING->FAILED, apply
    Note over App: FAILED is not the final-allocation status —<br/>no record_payment() call, no balance change,<br/>no accounting event, no collections effect
    App-->>GW: 200 {processing_status: PROCESSED}
```

## 3. Duplicate webhook

The gateway's own `duplicate_webhook` demo outcome deliberately resends the
final SETTLED event a second time with the SAME `gateway_event_id`.

```mermaid
sequenceDiagram
    participant GW as mock-payment-gateway
    participant App as retail-credit-api

    GW->>App: POST /webhooks (gateway_event_id=evt_1, status=SETTLED, signed)
    App->>App: no existing WebhookEvent for evt_1 — proceed
    App->>App: record_payment() — Payment #1 created, balance reduced once
    App-->>GW: 200 {processing_status: PROCESSED}

    GW->>App: POST /webhooks (gateway_event_id=evt_1, status=SETTLED, signed)  [redelivery]
    App->>App: existing WebhookEvent found for evt_1
    App->>App: retry_count += 1 — NOTHING ELSE RUNS
    App-->>GW: 200 {processing_status: PROCESSED}
    Note over App: record_payment()'s own idempotent-replay guard<br/>(unique on external_reference) is a second line of<br/>defence, but this idempotency check is what<br/>actually stops it — it never runs record_payment() twice
```

## 4. Reversal (settle, then reverse)

```mermaid
sequenceDiagram
    actor Ops as Gateway operator (simulated)
    participant GW as mock-payment-gateway
    participant App as retail-credit-api

    Note over GW,App: ... AUTHORIZED, CAPTURED, SETTLED already delivered<br/>and applied (as in diagram 1) ...

    Ops->>GW: (settle_then_reverse outcome fires this automatically)
    GW->>App: POST /webhooks (status=REVERSED, signed)
    App->>App: verify, check SETTLED->REVERSED, apply
    App->>App: payment_reversal.reverse_settled_payment()
    Note over App: compensating PaymentAllocation rows (negated amounts),<br/>installment/contract balances restored,<br/>negative LedgerEntry rows (related_action=reversal),<br/>original Payment.status -> reversed,<br/>emit payment_reversed accounting event,<br/>open_case_if_needed() reopens Collections case
    App-->>GW: 200 {processing_status: PROCESSED}
```

---

## 5. Every simulated outcome, and the webhook sequence it fires

(`mock-payment-gateway/app/main.py::simulate_outcome`, one clause per
outcome — this table is exhaustive.)

| Outcome (checkout page button) | Webhook sequence fired | Demonstrates |
|---|---|---|
| `success_settlement` | AUTHORIZED → CAPTURED → SETTLED | The full happy path |
| `success_authorization_only` | AUTHORIZED | A hold with no capture yet |
| `insufficient_funds` | FAILED (`failure_code=insufficient_funds`) | A straight decline |
| `customer_cancel` | CANCELLED | Customer abandons the page |
| `timeout` | EXPIRED | Session times out |
| `delayed_settlement` | AUTHORIZED → CAPTURED immediately, SETTLED after `DELAYED_SETTLEMENT_SECONDS` (a background timer) | A next-day-batch-style delayed settlement, compressed |
| `duplicate_webhook` | AUTHORIZED → CAPTURED → SETTLED → SETTLED again (same `gateway_event_id`) | Idempotent duplicate handling (diagram 3) |
| `invalid_signature` | AUTHORIZED, deliberately signed with the wrong payload | `REJECTED_SIGNATURE` |
| `settle_then_reverse` | AUTHORIZED → CAPTURED → SETTLED → REVERSED | Reversal (diagram 4) |

An **out-of-order / replayed** webhook (e.g. a stray AUTHORIZED arriving
after SETTLED) is not one of the buttons above — it is produced in tests by
crafting the HTTP call directly
(`tests/test_payment_gateway.py::test_out_of_order_webhook_is_rejected_without_moving_status_backwards`),
since a real gateway could in principle redeliver an older event out of
order even though this mock gateway's own sender never does so by itself.
