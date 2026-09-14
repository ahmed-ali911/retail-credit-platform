"""Mock Payment Gateway — demo seed data + a narrated walkthrough of the six
demo scenarios the brief calls for: paying 100 successfully, paying 50
partially, failing a 100 payment, a duplicate settlement webhook, reversing
a settled payment, and a reconciliation amount mismatch.

Unlike scripts/create_admin.py and scripts/seed_config.py (which run
in-process against the database), this is a REAL HTTP CLIENT script: it
drives the actual running stack exactly like a browser/Postman would,
because the payment-gateway journey genuinely crosses two separate running
services (retail-credit-api and mock-payment-gateway) — there is no
in-process shortcut for "the gateway posts a signed webhook back to the
app," that only happens over a real socket. Run this AFTER
`docker compose up --build` (or the equivalent local run), once both
services are reachable.

    python -m scripts.seed_demo_gateway

Env vars (all optional, default to a local docker-compose run):
    RETAIL_API_BASE_URL   default http://localhost:8000
    GATEWAY_PUBLIC_BASE_URL  default http://localhost:8100
    ADMIN_USERNAME / ADMIN_PASSWORD  default admin / admin
    PROMISE_DUE_IN_DAYS   default 14

Idempotency: checks for a customer with national_id "DEMO-CUST-001" up
front. If one already exists this script assumes the demo was already
seeded and stops immediately — it does not attempt to resume or re-run
individual scenario steps (a fresh demo environment is the supported case;
see docs/payment-gateway/ for how to reset one).

A note on "DEMO-CUST-001" / "DEMO-CONTRACT-001": this platform generates its
own sequential reference codes (CN-000001, ...) from real primary keys, so a
contract cannot literally be forced to display as "DEMO-CONTRACT-001". The
customer's `national_id` is set to "DEMO-CUST-001" (a real, natural business
identifier) and the resulting contract's actual reference code is printed
clearly at the end — that is "this demo's contract".

A note on the illustrative amounts in the brief ("Monthly installment KWD
100", "Overdue KWD 50", "Total outstanding KWD 600"): the contract's
schedule is produced by the real pricing engine (app/services/pricing.py),
never hand-set, so the actual figures will be close to, not exactly, those
numbers — printed plainly rather than faked. The 100/50 amounts used in the
scenario walkthrough below ARE exact, because those are explicit *partial*
payments the script itself requests.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

import httpx

RETAIL_API_BASE_URL = os.environ.get("RETAIL_API_BASE_URL", "http://localhost:8000")
GATEWAY_PUBLIC_BASE_URL = os.environ.get("GATEWAY_PUBLIC_BASE_URL", "http://localhost:8100")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
PROMISE_DUE_IN_DAYS = int(os.environ.get("PROMISE_DUE_IN_DAYS", "14"))

DEMO_NATIONAL_ID = "DEMO-CUST-001"


def _step(title: str) -> None:
    print(f"\n=== {title} ===")


def _api(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    resp = client.request(method, f"{RETAIL_API_BASE_URL}{path}", **kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _login(client: httpx.Client) -> str:
    resp = client.post(
        f"{RETAIL_API_BASE_URL}/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _build_demo_contract(client: httpx.Client) -> dict:
    _step("Creating demo customer, product, application, offer, contract")
    customer = _api(client, "POST", "/customers", json={
        "name": "Demo Customer (Mock Payment Gateway)",
        "national_id": DEMO_NATIONAL_ID,
        "phone": "+96500000100",
        "email": "demo.customer@example.com",
        "risk_score": 700,
        "profile": {
            "employer_name": "Demo Employer",
            "employment_type": "full_time",
            "monthly_income": 6000,
            "existing_monthly_obligations": 150,
            "address_line": "1 Demo Street",
            "city": "Kuwait City",
            "contact_phone": "+96500000100",
        },
    })
    product = _api(client, "POST", "/products", json={
        "name": "Demo Refrigerator", "category": "appliances",
        "cash_price": 1400, "installment_eligible": True,
    })
    application = _api(client, "POST", "/applications", json={
        "customer_id": customer["id"], "product_id": product["id"],
        "requested_amount": 1400, "requested_tenor_months": 12, "channel": "branch",
    })
    submitted = _api(client, "POST", f"/applications/{application['id']}/submit")
    if submitted["status"] != "approved":
        raise RuntimeError(
            f"Demo application did not auto-approve (status={submitted['status']!r}) — "
            "the demo customer's risk profile above must land in the 'approved' band."
        )
    offer = _api(client, "POST", f"/applications/{application['id']}/offer",
                 json={"down_payment_amount": 300})
    accepted = _api(client, "POST", f"/offers/{offer['id']}/accept", json={
        "down_payment_confirmed": True, "down_payment_reference": "DEMO-DP-001",
    })
    contract_id = accepted["contract_id"]
    _api(client, "POST", f"/contracts/{contract_id}/confirm-delivery")
    contract = _api(client, "GET", f"/contracts/{contract_id}")
    print(f"Demo contract: {contract['reference_code']} (id={contract_id})")
    print(f"Demo customer: national_id={DEMO_NATIONAL_ID} (id={customer['id']})")
    return contract


def _make_overdue_and_promise(client: httpx.Client, contract: dict) -> None:
    _step("Backdating so one installment is overdue, then logging a Promise to Pay")
    first_due = date.fromisoformat(contract["installments"][0]["due_date"])
    as_of = (first_due + timedelta(days=6)).isoformat()
    result = _api(client, "POST", "/jobs/assess-overdue", json={"as_of": as_of})
    print(f"Overdue assessment (as_of={as_of}): {result['installments_marked_overdue']} "
          f"installment(s) marked overdue, {result['collection_cases_opened']} case(s) opened.")

    cases = _api(client, "GET", "/collections/cases",
                 params={"contract_id": contract["id"], "status": "open"})
    if not cases:
        print("No open case — skipping the Promise to Pay (nothing to promise against).")
        return
    case_id = cases[0]["id"]
    promised_on = (date.today() + timedelta(days=PROMISE_DUE_IN_DAYS)).isoformat()
    _api(client, "POST", f"/collections/cases/{case_id}/activities", json={
        "activity_type": "promise_to_pay",
        "notes": "Demo promise — will pay KWD 100 by the promised date.",
        "promised_amount": 100.0, "promised_date": promised_on,
    })
    print(f"Promise to Pay logged on case #{case_id}: KWD 100 due {promised_on}.")


def _open_intent(client: httpx.Client, contract_id: int, *, amount: float, idem: str) -> dict:
    return _api(client, "POST", "/payments/intents", json={
        "contract_id": contract_id, "payment_purpose": "partial",
        "amount": amount, "idempotency_key": idem,
    })


def _checkout(client: httpx.Client, intent_id: int) -> dict:
    return _api(client, "POST", f"/payments/intents/{intent_id}/checkout")


def _simulate(client: httpx.Client, checkout_url: str, outcome: str) -> None:
    resp = client.post(f"{checkout_url}/simulate", data={"outcome": outcome},
                        follow_redirects=False)
    if resp.status_code not in (200, 303):
        raise RuntimeError(f"gateway simulate({outcome}) failed: {resp.status_code} {resp.text}")


def _await_status(client: httpx.Client, reference: str, *, tries: int = 10, delay: float = 0.5) -> dict:
    last = None
    for _ in range(tries):
        last = _api(client, "GET", f"/payments/{reference}/status")
        if last["status"] not in ("PENDING", "AUTHORIZED", "CAPTURED"):
            return last
        time.sleep(delay)
    return last or {}


def _scenario_pay_amount(client, contract, *, amount, idem, outcome, label) -> dict:
    _step(label)
    intent = _open_intent(client, contract["id"], amount=amount, idem=idem)
    checkout = _checkout(client, intent["id"])
    _simulate(client, checkout["checkout_url"], outcome)
    final = _await_status(client, intent["payment_reference"])
    print(f"{intent['payment_reference']}: requested {amount} -> {final.get('status')}")
    return final


def _scenario_duplicate_webhook(client, contract) -> None:
    _scenario_pay_amount(
        client, contract, amount=25.0, idem="DEMO-PAY-DUPLICATE",
        outcome="duplicate_webhook",
        label="Scenario: duplicate success webhook (must not double-allocate)",
    )


def _scenario_reversal(client, contract) -> dict | None:
    _step("Scenario: settle then reverse a payment")
    intent = _open_intent(client, contract["id"], amount=30.0, idem="DEMO-PAY-REVERSE")
    checkout = _checkout(client, intent["id"])
    _simulate(client, checkout["checkout_url"], "settle_then_reverse")
    final = _await_status(client, intent["payment_reference"])
    print(f"{intent['payment_reference']}: settled then reversed -> final status {final.get('status')}")
    return final


def _scenario_reconciliation_mismatch(client, settled_status: dict | None) -> None:
    _step("Scenario: reconciliation amount mismatch")
    if not settled_status or not settled_status.get("transactions"):
        print("No settled transaction available to build a mismatch batch from — skipping.")
        return
    settled_txn = next(
        (t for t in settled_status["transactions"] if t["gateway_status"] == "SETTLED"), None
    )
    if settled_txn is None:
        print("No SETTLED transaction found — skipping.")
        return

    wrong_gross = round(float(settled_txn["settled_amount"]) + 5.00, 2)
    today = date.today().isoformat()
    batch_reference = f"DEMO-BATCH-MISMATCH-{today}"
    try:
        result = _api(client, "POST", "/payments/settlement-batches", json={
            "batch_reference": batch_reference,
            "settlement_date": today,
            "currency": "KWD",
            "items": [{
                "gateway_transaction_reference": settled_txn["gateway_transaction_reference"],
                "merchant_reference": settled_status["payment_reference"],
                "settlement_date": today,
                "gross_amount": wrong_gross,
                "gateway_fee": float(settled_txn["gateway_fee"] or 0),
                "net_amount": wrong_gross - float(settled_txn["gateway_fee"] or 0),
                "currency": "KWD",
                "gateway_status": "SETTLED",
            }],
        })
    except httpx.HTTPStatusError as exc:
        print(f"Could not import the deliberately-mismatched batch: {exc.response.text}")
        return
    print(
        f"Imported {batch_reference}: {result['matched']} matched, "
        f"{result['exceptions']} exception(s) — deliberately overstated the gateway's "
        f"reported gross amount by KWD 5.00 against {settled_status['payment_reference']} "
        "to produce an AMOUNT_MISMATCH for the Gateway Reconciliation screen."
    )


def main() -> None:
    with httpx.Client(timeout=30.0) as client:
        token = _login(client)
        client.headers["Authorization"] = f"Bearer {token}"

        existing = _api(client, "GET", "/customers", params={"search": DEMO_NATIONAL_ID})
        if existing:
            print(
                f"Demo customer '{DEMO_NATIONAL_ID}' already exists (id={existing[0]['id']}) — "
                "assuming the demo is already seeded. Nothing to do."
            )
            return

        contract = _build_demo_contract(client)
        _make_overdue_and_promise(client, contract)

        options = _api(client, "GET", f"/contracts/{contract['id']}/payment-options")
        print(f"\nPayment options for {contract['reference_code']}: "
              f"overdue={options['overdue_amount']} total_outstanding={options['total_outstanding']}")

        settled_100 = _scenario_pay_amount(
            client, contract, amount=100.0, idem="DEMO-PAY-100-SUCCESS",
            outcome="success_settlement", label="Scenario: pay 100 successfully",
        )
        _scenario_pay_amount(
            client, contract, amount=50.0, idem="DEMO-PAY-50-PARTIAL",
            outcome="success_settlement", label="Scenario: pay 50 partially",
        )
        _scenario_pay_amount(
            client, contract, amount=20.0, idem="DEMO-PAY-100-FAIL",
            outcome="insufficient_funds", label="Scenario: a payment fails (insufficient funds)",
        )
        _scenario_duplicate_webhook(client, contract)
        _scenario_reversal(client, contract)
        _scenario_reconciliation_mismatch(client, settled_100)

        _step("Done")
        print(f"Demo contract: {contract['reference_code']} — open it in the frontend at "
              f"/contracts/{contract['id']} to see the Pay via Payment Gateway card, "
              "or visit /payments and /payments/reconciliation for the operations views.")


if __name__ == "__main__":
    main()
