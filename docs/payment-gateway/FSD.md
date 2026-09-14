# Functional Specification Document — Mock Payment Gateway

**Status:** Implemented. Every endpoint, status, and rule below is
code-verified against the current `app/` and `mock-payment-gateway/app/`
trees — file/function references are exact, not paraphrased.

---

## 1. Overview & BRD Traceability

This feature adds a simulated hosted-checkout payment channel
(`mock-payment-gateway/`, a genuinely separate FastAPI service) and the
retail-credit-api-side plumbing that treats it exactly like a real external
provider: a `PaymentIntent` is opened, a checkout session is requested over
HTTP, and the only way this app's own records change is a signed webhook
callback it independently verifies.

### 1.1 BRD → FSD → Story → Acceptance Criteria → Test Case

| BRD | FSD section | Story (illustrative) | Acceptance criteria | Test case(s) |
|---|---|---|---|---|
| BR-1 (auditable lifecycle) | §2, §4 | PG-101 "As Collections, I need every payment attempt tracked before it affects a balance" | Creating an intent and opening checkout never changes `outstanding_receivable` | `test_checkout_moves_intent_to_pending`, `test_failed_payment_still_does_not_update_balances` |
| BR-2 (configurable allocation point) | §4.2 | PG-102 "As Finance, I need to control whether allocation happens on CAPTURED or SETTLED without a code change" | Changing `payment_gateway_final_allocation_status` changes which webhook status triggers allocation | `app/services/gateway_webhooks.py::_final_allocation_status` (reads `ConfigService`, no hard-coded status) |
| BR-3 (no trace before final status) | §4.2 | PG-103 | A FAILED/CANCELLED/EXPIRED intent produces zero Payment rows and zero balance change | `test_failed_payment_never_touches_the_contract_balance`, `test_failed_payment_still_does_not_update_balances` |
| BR-4 (reuse the allocation engine) | §4.3 | PG-104 "As Engineering, I need one allocation implementation, not two" | A settled gateway payment produces the identical `PaymentAllocation` shape a staff payment does, via the same function call | `test_settled_payment_creates_payment_via_existing_engine_and_allocates` |
| BR-5 (reversal via compensating records) | §4.4 | PG-105 | A REVERSED/REFUNDED webhook restores installment balances without editing the original rows | `test_reversal_restores_balances_via_compensating_records`, `test_refunded_status_also_triggers_reversal_with_refund_event` |
| BR-6 (signed, replay-safe, idempotent webhooks) | §7 | PG-106 | Invalid signature → rejected; stale timestamp → rejected; duplicate `gateway_event_id` → acknowledged, not reprocessed | `test_invalid_signature_is_rejected`, `test_stale_webhook_is_rejected`, `test_duplicate_webhook_is_acknowledged_without_reprocessing`, `test_settlement_updates_balance_only_once_even_with_duplicate_webhook` |
| BR-7 (Promise-to-Pay correctness) | §4.5 | PG-107 | An INITIATED-only payment never marks a promise kept; a SETTLED one covering the promised amount does | `test_merely_initiated_payment_does_not_mark_promise_kept`, `test_settled_payment_marks_eligible_promise_to_pay_kept` |
| BR-8 (reconciliation engine) | §5, [reconciliation.md](reconciliation.md) | PG-108 | Every one of the 8 outcomes (MATCHED…UNRESOLVED) is produced by the matching rules, and a resolution requires a different approver | `test_reconciliation_matched_marks_payment_reconciled`, `test_reconciliation_amount_mismatch_resolved_via_maker_checker`, `test_reconciliation_missing_in_internal_system`, `test_reconciliation_missing_in_gateway_sweep`, `test_duplicate_batch_reference_is_rejected` |
| BR-9 (simulated outcomes) | §3.2 | PG-109 | The hosted checkout page offers all 9 outcomes; each fires the correct webhook sequence | `mock-payment-gateway/tests/test_gateway.py` (one test per outcome) |

---

## 2. Functional Flow & Statuses

### 2.1 PaymentIntent status graph

Source of truth: `app/models/payment_gateway.py::PAYMENT_INTENT_TRANSITIONS`.
Any transition not listed here — including a status "changing" to itself —
is refused by `can_transition()`.

```
INITIATED   -> PENDING, CANCELLED, EXPIRED
PENDING     -> AUTHORIZED, FAILED, CANCELLED, EXPIRED
AUTHORIZED  -> CAPTURED, FAILED, EXPIRED
CAPTURED    -> SETTLED, FAILED
SETTLED     -> REVERSED, PARTIALLY_REFUNDED, REFUNDED, CHARGEBACK
PARTIALLY_REFUNDED -> REFUNDED
FAILED / CANCELLED / EXPIRED / REVERSED / REFUNDED / CHARGEBACK -> (terminal)
```

`INITIATED` is set the moment `POST /payments/intents` creates the row (no
gateway involved yet); `PENDING` is set the moment
`POST /payments/intents/{id}/checkout` successfully opens a session with the
gateway. Every status from `AUTHORIZED` onward arrives **only** via a
verified webhook — this app never sets them itself.

### 2.2 End-to-end sequence

See [payment-flow.md](payment-flow.md) for the full Mermaid sequence
diagrams (successful settlement, a failed payment, a duplicate webhook,
and a reversal).

### 2.3 Webhook processing pipeline

`app/services/gateway_webhooks.py::process_webhook`, in order:

1. **Idempotency** — a `WebhookEvent` row already exists for this
   `gateway_event_id`? Bump `retry_count`, return the existing row
   unchanged (HTTP 200, `processing_status` unchanged from the first
   delivery — normally `PROCESSED`).
2. **Signature** — HMAC-SHA256 over `"{timestamp}." + raw_body`
   (`app/services/webhook_security.py::verify_signature`). Invalid → new
   `WebhookEvent` row, `REJECTED_SIGNATURE`, HTTP 401.
3. **Staleness** — the signature's own `t=` timestamp older than
   `gateway_webhook_max_age_seconds` (300s default)? →
   `REJECTED_STALE`, HTTP 400.
4. **Transition validity** — look up the target `PaymentIntent`
   (`payment_reference`), parse the claimed status, check
   `can_transition(current, target)`. Not found → `FAILED` (HTTP 200,
   acknowledged so the gateway does not retry forever — this app has
   already logged the problem). Not a valid transition → the event is
   recorded but not applied — `REJECTED_OUT_OF_ORDER`, HTTP 200.
5. **Apply** — append a `GatewayTransaction` row (never edits a previous
   one — the full gateway-reported history for one intent accumulates),
   set `intent.status`, then:
   - if the new status is the *configured* final-allocation status →
     `_apply_final_allocation` (§4.3);
   - elif the new status is REVERSED/REFUNDED/CHARGEBACK →
     `_apply_reversal` (§4.4);
   - else (including PARTIALLY_REFUNDED) → no financial side effect.
   `WebhookEvent.processing_status = PROCESSED`, HTTP 200.
6. **Unhandled exception at step 5** (a genuine bug, not a business-rule
   rejection — those never raise) → the whole DB transaction rolls back,
   a fresh `WebhookEvent` row is written `FAILED` with the exception text,
   HTTP 500 — the gateway's own retry logic (§7.3) will redeliver.

---

## 3. Screen / UI Behaviour

### 3.1 Customer Payment Screen (`frontend/src/components/GatewayPaymentCard.tsx`, mounted on the Contract page)

Staff-operated, producing the identical customer-facing checkout hand-off a
self-service portal would. Shows, from `GET /contracts/{id}/payment-options`:
next installment amount + due date, overdue amount, late fees outstanding,
total outstanding, minimum acceptable (partial) payment. A purpose selector
(current installment / overdue / full outstanding / partial + amount) is
disabled per-option when the underlying `can_pay_*` flag is false. "Start
gateway payment" creates the intent, opens checkout, and surfaces the
gateway's `checkout_url` as an external link (opens the SEPARATE
hosted-checkout page — this app never renders it). "Refresh status" re-polls
`GET /payments/{reference}/status` and shows the transaction timeline
(one row per `GatewayTransaction`). A payment-intent history table lists
past intents for the contract.

### 3.2 Demo Gateway Checkout (`mock-payment-gateway/app/checkout_page.py`)

Server-rendered HTML on the SEPARATE gateway service. "Demo Gateway"
branding (deliberately generic, no resemblance to any real provider), the
merchant/payment reference, the customer's name masked at display time
(`A**** A*******`), amount + KWD, contract reference, description, an
expiry countdown, and the payment method labelled generically "Demo Debit
Network". Nine buttons, one per simulated outcome — see
[payment-flow.md](payment-flow.md) §2 for exactly which webhook sequence
each one fires. A "Return to merchant" link works without picking an
outcome. Every button is disabled once the session is consumed or expired.

### 3.3 Payment Operations Dashboard (`frontend/src/pages/PaymentOperationsPage.tsx`, `/payments`)

Cross-contract list of every `PaymentIntent`, filterable by status, with
summary tiles grouping statuses into settled / in-flight / did-not-complete
/ reversed-like. Data source: `GET /payments/intents` (new in this
feature — filters: `status`, `contract_id`, `limit`).

### 3.4 Gateway Reconciliation Screen (`frontend/src/pages/GatewayReconciliationPage.tsx`, `/payments/reconciliation`)

Distinct from the pre-existing Bank Reconciliation screen (`/reconciliation`)
— see [reconciliation.md](reconciliation.md) for the reuse-decision
writeup. "Pull today's batch" fetches the gateway's own feed
(`GET /gateway/settlement-batches/generate` on the separate service) and
imports it (`POST /payments/settlement-batches/pull`). Lists imported
batches and reconciliation items, filterable by outcome/status. An open
item's "Resolve" form (reason + optional comments) creates a maker-checker
`ApprovalRequest` — the actual resolution only happens once a *different*
user approves it via the existing `/approvals` screen.

### 3.5 Collections Timeline (`frontend/src/pages/CollectionsPage.tsx`, case detail)

The case detail view gained a "Related payments" table — every `Payment`
(staff or gateway-sourced) against the case's contract, with its channel,
amount, status, and timestamp. A case reopened by a reversal
(`app/services/payment_reversal.py::reverse_settled_payment`) shows its own
`opened_reason` naming the reversed payment's reference.

---

## 4. Business Rules & Calculations

### 4.1 Amount validation (`app/services/payment_intents.py::_resolve_requested_amount`)

| Purpose | Resolved amount | Rejected when |
|---|---|---|
| `current_installment` | The oldest live installment's `total_due` | No live installment exists |
| `overdue_amount` | Sum of `principal_outstanding + profit_outstanding` across overdue installments | Overdue amount is zero |
| `full_outstanding` | `outstanding_receivable + outstanding_late_fees` | — |
| `partial` | The caller-supplied amount | Below `payment_minimum_partial_amount` (config, default 5.00) |

Every purpose is additionally rejected (`DomainError`, HTTP 422) if the
resolved amount exceeds `total_outstanding` — mirrors the pre-existing
`POST /contracts/{id}/payments` overpayment guard exactly (same message
format).

### 4.2 Final-allocation status (BR-2)

`ConfigService(db).get(KEY_PAYMENT_GATEWAY_FINAL_STATUS)` — seeded
`"SETTLED"` in `config/business_rules.yaml`, changeable via
`POST /config/{key}` (existing config-update maker-checker flow — no new
mechanism). Nothing else in this feature hard-codes "SETTLED" as a literal;
`_apply_final_allocation` is called only when the just-applied status
equals whatever this reads.

### 4.3 Allocation reuse (BR-4)

`_apply_final_allocation` (`app/services/gateway_webhooks.py`) calls
`app/services/payments.py::record_payment(db, contract, amount=…,
external_reference=intent.payment_reference, actor_id=<system.gateway>,
source=PaymentSource.gateway, payment_intent_id=intent.id)` — the exact
function `POST /contracts/{id}/payments` calls, extended additively with
`source`/`payment_intent_id` (defaulted to the pre-existing behaviour for
every staff call site). `record_payment`'s own idempotent-replay check
(unique on `contract_id` + `external_reference`) is what makes this safe
even if this function were ever reached twice for the same intent — it
cannot, but the safety net was worth keeping.
`external_reference = intent.payment_reference` (e.g. `PI-000042`) is the
idempotency key, following this codebase's existing convention.

If the triggering `GatewayTransaction.gateway_fee` is set, one
`AccountingEventType.gateway_fee_recognized` event is emitted
(`event_reference = "gateway-fee-{transaction.id}"`).

### 4.4 Reversal (BR-5) — `app/services/payment_reversal.py::reverse_settled_payment`

1. Locate the original gateway-sourced `Payment` for this intent (idempotent
   no-op if already reversed).
2. For each of its `PaymentAllocation` rows with
   `reversed_by_allocation_id IS NULL`: create a NEW compensating
   `PaymentAllocation` (same installment, negated `late_fee_amount` /
   `profit_amount` / `principal_amount`), on a NEW `Payment` row
   (`external_reference = "{original}-REV"`, `status = reversed`).
   Set `original_allocation.reversed_by_allocation_id` to the new row's id.
3. Decrement the installment's `principal_paid`/`profit_paid` by the
   reversed amounts; restore `contract.unearned_profit_balance`; reverse
   late-fee `amount_paid` most-recently-assessed-charge-first (a documented
   best-effort approximation — see the function's docstring — since
   per-charge attribution of an original payment is not stored).
4. Write negative-amount `LedgerEntry` rows
   (`related_action = LedgerRelatedAction.reversal`, same `entry_type`s as
   the original) — existing sum-based reports (e.g. profitability) net these
   out automatically, no special-casing needed there.
5. Set `original.status = PaymentStatus.reversed`.
6. Emit `AccountingEventType.payment_reversed` (REVERSED/CHARGEBACK) or
   `refund_completed` (REFUNDED).
7. `services/collections.py::open_case_if_needed` — reopens (as a NEW case
   row; the previously-closed one stays intact history) with
   `opened_reason` naming the reversed payment reference.

`PARTIALLY_REFUNDED` never reaches this function (see BDR-PG-01).

### 4.5 Promise-to-Pay (BR-7)

No new code — `services/collections.py::evaluate_promises_after_payment`
sums `Payment.amount` since the promise was logged and marks it `kept` once
that sum reaches `promised_amount`. Because a `Payment` row is only ever
created at the configured final-allocation status, this pre-existing
function automatically never sees an `INITIATED`-only attempt — the
correctness of BR-7 falls directly out of BR-3, not a separate rule.

### 4.6 Collections case close/reopen

No new code for close-on-full-payment either — the pre-existing
`close_case_if_cleared` (no overdue installments left) fires identically
whether the settling `Payment` came from staff or the gateway.

---

## 5. Data & Integration Requirements

### 5.1 Retail-credit-api domain model

| Table | Purpose | Key columns |
|---|---|---|
| `payment_intents` | One customer attempt to pay | `payment_reference` (unique), `idempotency_key` (unique), `status`, `requested_amount`, `payment_purpose`, `gateway_session_id`, `expires_at` |
| `gateway_transactions` | This app's append-only record of what the gateway reported | `payment_intent_id`, `gateway_transaction_reference`, `gateway_status`, `authorized_amount`/`captured_amount`/`settled_amount`/`gateway_fee`, three timestamp columns |
| `webhook_events` | Every inbound webhook, verified or not | `gateway_event_id` (unique), `payload_hash` (sha256, no raw payload stored), `signature_valid`, `processing_status`, `retry_count` |
| `settlement_batches` | One imported daily settlement feed | `batch_reference` (unique), `settlement_date`, gross/fee/net totals |
| `gateway_reconciliation_items` | One line-level comparison outcome | `outcome`, `status`, `matched_payment_id`/`matched_intent_id`, `variance_amount`, resolution fields |
| `payments.source` / `payments.payment_intent_id` | Extends the pre-existing `Payment` table | `source` (staff/gateway), nullable FK to the originating intent |
| `payment_allocations.reversed_by_allocation_id` | Extends the pre-existing allocation table | Self-referencing FK, NULL until reversed |

Migrations: `alembic/versions/0014_payment_gateway.py`,
`0015_gateway_settlement.py` — both additive only (see each file's own
docstring for the exact column/table list and the downgrade path).

### 5.2 Mock-payment-gateway's own domain model (separate database)

`CheckoutSession`, `GatewayTransactionRecord` (this gateway's own view of
one transaction — deliberately allowed to disagree with the merchant's
until a webhook lands), `WebhookDeliveryLog` (the gateway's outbox — unlike
`webhook_events` above, this side DOES keep the full payload, since it is
the sender and a byte-identical resend is what makes
`POST /gateway/webhooks/{event_id}/retry` meaningful).

### 5.3 Cross-service HTTP contract

| Call | Direction | Endpoint |
|---|---|---|
| Open a checkout session | retail-credit-api → gateway | `POST /gateway/checkout-sessions` |
| Fetch one transaction | retail-credit-api → gateway | `GET /gateway/transactions/{reference}` |
| Pull the daily settlement feed | retail-credit-api → gateway | `GET /gateway/settlement-batches/generate` |
| Deliver a payment-status webhook | gateway → retail-credit-api | `POST /integrations/mock-gateway/webhooks` |

### 5.4 Retail-credit-api endpoints (this feature)

```
GET  /contracts/{id}/payment-options
GET  /payments/intents                       (list/filter — Operations Dashboard)
POST /payments/intents
GET  /payments/intents/{id}
POST /payments/intents/{id}/checkout
GET  /payments/{reference}/status
POST /integrations/mock-gateway/webhooks     (unauthenticated — HMAC-verified instead)
POST /payments/settlement-batches
POST /payments/settlement-batches/pull
GET  /payments/settlement-batches
GET  /payments/settlement-batches/{id}
GET  /payments/reconciliation-items
POST /payments/reconciliation-items/{id}/resolve
```

### 5.5 Mock-payment-gateway endpoints

```
POST /gateway/checkout-sessions
GET  /gateway/checkout/{token}                (HTML — Demo Gateway page)
POST /gateway/checkout/{token}/simulate
GET  /gateway/transactions/{reference}
POST /gateway/webhooks/{event_id}/retry
GET  /gateway/settlement-batches/generate
GET  /health
```

---

## 6. Validations, Errors & Edge Cases

| Case | Behaviour | Status |
|---|---|---|
| Duplicate `idempotency_key` on intent creation | Returns the EXISTING intent, `replayed=true`, no new audit event | 201 |
| `amount` exceeds total outstanding | Rejected before any row is written | 422 |
| Partial amount below the configured minimum | Rejected | 422 |
| Checkout requested for a non-INITIATED intent | Rejected | 409 |
| Webhook: unknown `payment_reference` | Recorded as `FAILED`, acknowledged (no infinite gateway retry for something that will never resolve) | 200 |
| Webhook: bad signature | New `WebhookEvent` row, `REJECTED_SIGNATURE` | 401 |
| Webhook: stale timestamp | `REJECTED_STALE` | 400 |
| Webhook: transition not in the closed graph (replay, out-of-order, or a same-status "duplicate" outside the idempotency check) | `REJECTED_OUT_OF_ORDER`, intent status unchanged | 200 |
| Webhook: duplicate `gateway_event_id` | Existing row's `retry_count` incremented, original `processing_status` preserved | 200 |
| Unhandled exception applying a webhook | Full rollback, fresh `WebhookEvent` row `FAILED` with the exception text | 500 (gateway retries) |
| Reconciliation: duplicate `batch_reference` | Rejected before any item is created | 409 |
| Reconciliation: two items in one batch share a `gateway_transaction_reference` | Second one classified `DUPLICATE` | 201 (batch import still succeeds; the item is the exception) |
| Resolving an already-resolved reconciliation item | Rejected | 409 |
| Approving your own reconciliation-resolution request | Rejected (generic maker-checker rule, reused unchanged) | 409 |

---

## 7. Security, Audit & Performance

### 7.1 Signing scheme (Stripe-style, both sides implement it independently)

```
signed_payload = f"{timestamp}." + raw_body
signature      = hex(HMAC-SHA256(secret, signed_payload))
header         = "X-Gateway-Signature: t=<timestamp>,v1=<signature>"
```

`app/services/webhook_security.py` (verify side) /
`mock-payment-gateway/app/security.py` (sign side). The shared secret
(`GATEWAY_WEBHOOK_SECRET` / `WEBHOOK_SECRET`) is read from the environment
on both sides, never committed — see both `.env.example` files. Folding the
timestamp into the signed material is what makes replay protection
possible without a nonce store.

### 7.2 Audit trail

Every `WebhookEvent` row IS the audit trail for inbound webhooks — no raw
payload is stored (`payload_hash`, sha256, tamper-evidence only), matching
this codebase's existing "no sensitive payloads at rest" posture. Every
state-changing action additionally writes an `AuditEvent`
(`services/audit.py::record_event`, the same mechanism every other module
uses) — webhook-triggered actions are attributed to the non-interactive
`system.gateway` account (`app/services/users.py::ensure_system_actor_id`),
never `None`, so `AuditEvent.user_id` is always populated.

### 7.3 Reliability

A redelivered webhook (the gateway's own retry, or the manual
`POST /gateway/webhooks/{event_id}/retry`) is safe by construction — the
idempotency check runs before any financial code. `WebhookDeliveryLog` on
the gateway side and `WebhookEvent.retry_count` on this side together give
full delivery-attempt visibility without a dedicated dead-letter-queue
table (this codebase has no DLQ infrastructure anywhere else either — the
`FAILED` processing status IS the dead-letter signal, queryable exactly
like every other exception-style table in this app, e.g.
`ReconciliationException`).

### 7.4 Performance

No caching, batching, or async fan-out was added — webhook processing is a
single synchronous request/response, matching every other write endpoint in
this codebase. This is appropriate for the traffic this platform is built
for (a single retail business, not a payment aggregator) and was not
identified as a requirement anywhere in the brief.
