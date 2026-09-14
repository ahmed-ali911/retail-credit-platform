# Business Requirements Document — Mock Payment Gateway

**Status:** Implemented (code-verified — every section below traces to the
actual running code, not a design intention). Several figures are marked
**BUSINESS DECISION REQUIRED** where this implementation had to pick a
default to keep moving; those defaults are configurable, not hard-coded, and
are called out explicitly rather than presented as confirmed policy.

---

## 1. Business Objective & Problem Statement

The platform (a Kuwait-style retail installment-sale business — see the
repo root [README](../../README.md)) currently records a payment only
through `POST /contracts/{id}/payments`: a staff member types in an amount
and an external reference, as if a payment had already happened somewhere
else (in a branch, over the phone). There is no channel through which a
customer can pay their own installment, and no simulated integration with
any payment provider at all.

This feature adds a **mock** online payment channel — modelled on how a
Kuwait retail business would integrate with a real hosted-checkout gateway
(the customer journey resembles KNET's redirect-and-return pattern, without
copying KNET's branding, logo, or proprietary interface) — so that:

- a customer's payment can be initiated, authorized, captured, and settled
  through a simulated external provider, with the SAME clear separation a
  real integration would need between "the customer clicked pay" and "the
  money has actually and irreversibly moved";
- the company's own financial records (installment balances, collections
  cases, accounting events) are only ever updated once that money has
  genuinely settled, verified by a signed callback from the provider — never
  on the customer's say-so, never on an assumption;
- the same event can be reversed later (a chargeback, a refund, an
  operational reversal) without ever deleting or silently overwriting the
  original financial history;
- the company can reconcile what the gateway says it settled against what
  this system recorded internally, on a schedule that mirrors how real
  payment-provider settlement files work.

**This is a training/portfolio implementation.** It does not claim to be an
official Alghanim, Xcite, or KNET product, does not use their branding, and
is entirely simulated — no real card number, CVV, bank credential, or real
payment token is ever requested, transmitted, or stored anywhere in this
codebase (see [mock-payment-gateway/README.md](../../mock-payment-gateway/README.md)).

## 2. Stakeholders

| Stakeholder | Interest |
|---|---|
| Customer | A working, transparent way to pay an installment online, with a clear timeline of what happened to their money. |
| Collections | A payment gateway event must correctly and promptly drive case status and Promise-to-Pay tracking — never leave a case open after money has cleared, never close one on an unverified promise. |
| Finance / Accounting | Every settled payment, reversal, and gateway fee is represented as a postable accounting event (reusing the existing G-07 accounting-event boundary); a daily reconciliation process surfaces any gap between the gateway's numbers and this system's own before it becomes a real variance. |
| Credit / Risk | The existing exposure, ECL, and receivable calculations must reflect a gateway-sourced payment identically to a staff-entered one — no separate, parallel code path that could drift out of sync with the tested allocation engine. |
| Engineering | The gateway must be a genuinely separate, independently deployable service — this app must never reach into the gateway's own database, and vice versa; every cross-service interaction is over HTTP, signed and verified. |

## 3. Current State & Pain Points

Before this feature:

- `Payment` rows only ever came from a trusted staff member's manual entry
  (`POST /contracts/{id}/payments` → `app/services/payments.py::record_payment`).
  There was no `source` distinction because there was only one source.
- There was no concept of an intermediate, unsettled payment attempt at
  all — a `Payment` row's mere existence already meant "this money is
  allocated." A self-service channel needs that distinction (a customer can
  click "Pay" and then abandon the browser tab, or the provider can decline
  the attempt) — none of that could previously be represented.
- Bank reconciliation (`app/models/reconciliation.py`, from the P0-5 fix)
  only ever compared recorded `Payment` rows against the company's own bank
  statement — a real payment-gateway settlement feed is a structurally
  different document (batch totals, a gateway transaction fee, a gateway's
  own transaction reference) that the existing matching rules do not model.

## 4. Business Requirements

BR-1. A customer-initiated payment must go through an explicit, auditable
lifecycle (`PaymentIntent` → gateway checkout → webhook-verified status)
before it can affect any balance.

BR-2. The financial allocation point (when a payment actually reduces a
contract's outstanding balance) must be **configurable**, not hard-coded —
seeded to `SETTLED`, with `CAPTURED` available as an alternative policy,
pending Finance/Credit sign-off. See
`config_parameter` key `payment_gateway_final_allocation_status`
(`config/business_rules.yaml`).

BR-3. A payment that only reaches `INITIATED`, `PENDING`, `FAILED`,
`CANCELLED`, or `EXPIRED` must leave **zero** trace in the contract's
balance, the collections case, or the accounting-event ledger.

BR-4. The allocation logic itself must not be duplicated — a gateway
payment that reaches the configured final status must be applied through
the SAME allocation engine (`app/services/allocation.py::allocate`, called
via `app/services/payments.py::record_payment`) that the existing
staff-entered payment endpoint uses. **Confirmed with the business owner
before implementation** — see the reuse-decision note in
`app/services/gateway_webhooks.py`.

BR-5. A later reversal (REVERSED / REFUNDED / CHARGEBACK) must undo the
allocation via compensating records — never edit or delete the original
`Payment` / `PaymentAllocation` rows.

BR-6. Every inbound webhook must be independently verified (HMAC
signature), replay-protected (a timestamp window), and idempotent (the same
gateway event processed twice must produce the financial effect exactly
once).

BR-7. A Promise-to-Pay is only `kept` once the total SETTLED amount reaches
the promised amount by the promised date — a payment that was merely
`INITIATED` before the deadline must never count.

BR-8. The gateway's own daily settlement feed must be reconciled against
this system's internal records on a defined schedule, with a closed
taxonomy of match/exception outcomes and a maker-checker-gated manual
resolution path for anything that does not auto-match.

BR-9. The mock gateway must expose enough simulated failure/edge-case
outcomes (decline, cancel, timeout, delayed settlement, duplicate webhook,
invalid signature, settle-then-reverse) that every downstream business rule
above can be demonstrated and tested without a real payment network.

## 5. Success Criteria / KPIs

These are the acceptance criteria this implementation was actually held to
(see [FSD.md](FSD.md) §6 for the BRD→FSD→test traceability table):

- A payment that only reaches `INITIATED`/`FAILED`/`CANCELLED`/`EXPIRED`
  produces **zero** change to `outstanding_receivable` (test-verified).
- The configured final-allocation status is the **only** status that
  creates a `Payment` row (test-verified: `test_settled_payment_creates_
  payment_via_existing_engine_and_allocates`).
- A duplicate webhook delivery (same `gateway_event_id`) is acknowledged
  (HTTP 200) without a second `Payment`/allocation being created
  (test-verified).
- A reversal restores the exact pre-settlement installment balances via
  compensating `PaymentAllocation` rows, and reopens the collections case
  (test-verified).
- A reconciliation batch import correctly classifies every one of the eight
  outcomes the brief specifies (test-verified, one test per outcome family).
- `docker compose up --build` alone still brings up the complete, working
  three-service stack (verified live against the running containers, not
  just unit tests — see the checkpoint reports in the project's commit
  history for the actual command transcripts).

## 6. Assumptions & Constraints

- **No real payment data, ever.** The mock gateway never collects, stores,
  or transmits a card number, CVV, bank account/IBAN, OTP, or any other
  real payment credential — every "payment method" is a labelled button on
  a simulated page (`mock-payment-gateway/app/checkout_page.py`).
- **Single currency (KWD)** throughout, matching the rest of this platform.
- **No real scheduler.** Daily settlement-batch generation and import are
  manually triggered (`GET /gateway/settlement-batches/generate`,
  `POST /payments/settlement-batches/pull`), consistent with every other
  "mock adapter, manually triggered" boundary already in this codebase
  (the existing bank-reconciliation ingestion, `/jobs/assess-overdue`,
  `/jobs/post-accounting-events`).
- **Partial refunds are recorded, not automated.** The gateway's webhook
  payload has no partial-refund-amount field yet, so `PARTIALLY_REFUNDED`
  is logged but triggers no financial effect — see BDR-PG-01 below.
- **The mock gateway is a genuinely separate deployable** (own FastAPI app,
  own SQLite store, own Dockerfile) — this is a constraint the user set
  explicitly, not an implementation convenience.

### Open Business Decisions (BDR-PG-*)

| ID | Decision needed | Current default | Where it lives |
|---|---|---|---|
| BDR-PG-01 | Should the platform automate a partial-refund amount against specific installments, and if so, what webhook payload field carries it? | Not automated — `PARTIALLY_REFUNDED` is recorded (WebhookEvent + audit trail) but no allocation change happens. | `app/services/gateway_webhooks.py::process_webhook` (see the comment at the PARTIALLY_REFUNDED branch) |
| BDR-PG-02 | Allocate on `CAPTURED` or `SETTLED`? | `SETTLED` | `config_parameter` `payment_gateway_final_allocation_status` |
| BDR-PG-03 | Minimum partial payment amount | KWD 5.00 | `config_parameter` `payment_minimum_partial_amount` |
| BDR-PG-04 | Checkout session expiry window | 15 minutes | `config_parameter` `payment_intent_expiry_minutes` |
| BDR-PG-05 | Whether a partially-kept promise (a payment that covers *some* but not all of a promised amount) should get its own `partially_kept` status, and under what threshold | Off by default — `PromiseStatus.partially_kept` exists in the model but is never set unless `promise_partial_credit_enabled` is turned on | `config_parameter` `promise_partial_credit_enabled` |
| BDR-PG-06 | The demo gateway fee rate shown on settlement (1.5%) | Illustrative only, not a real acquirer rate | `mock-payment-gateway/app/main.py::_DEMO_FEE_RATE` |
| BDR-PG-07 | Chart-of-accounts / GL mapping per accounting event type | Not modelled — every `AccountingEvent` carries one signed `amount`, no debit/credit split (same open item as the pre-existing G-07 boundary) | `app/models/accounting.py` docstring |

## 7. Out of Scope

- Any real payment network integration (KNET, Visa/Mastercard, a real bank
  API).
- Multi-currency payments.
- A customer-facing login/self-service portal — the existing frontend is a
  staff web app (see `frontend/src/components/Shell.tsx`); the "Customer
  Payment Screen" is a staff-operable card on the Contract page that
  produces the exact same customer-facing checkout experience a real
  self-service portal would hand off to.
- Automated/scheduled settlement-batch generation (cron-style) — this is a
  manually-triggered action in this demo, consistent with every other mock
  adapter in the codebase.
- A real chart-of-accounts / GL posting split (BDR-PG-07, inherited from the
  pre-existing accounting-event boundary, not introduced by this feature).
