"""Step 14 — computed reference codes (no migration, no stored column)."""
import pytest

from app.core.references import format_reference, parse_reference
from tests.helpers import (
    active_contract,
    approved_application,
    make_application,
    make_customer,
    make_product,
)


# --------------------------------------------------------------------------- #
# the utility
# --------------------------------------------------------------------------- #
def test_format_reference_per_entity_type():
    assert format_reference("Customer", 4) == "CU-000004"
    assert format_reference("Product", 12) == "PR-000012"
    assert format_reference("CreditApplication", 7) == "AP-000007"
    assert format_reference("InstallmentOffer", 3) == "OF-000003"
    assert format_reference("SalesOrder", 3) == "SO-000003"
    assert format_reference("InstallmentContract", 12) == "CN-000012"
    assert format_reference("Payment", 88) == "PY-000088"
    assert format_reference("CollectionCase", 1) == "CC-000001"
    assert format_reference("InstallmentContract", 1234567) == "CN-1234567"  # no truncation


def test_format_reference_unknown_type_raises():
    with pytest.raises(ValueError):
        format_reference("Widget", 1)


def test_parse_reference_round_trips():
    assert parse_reference("CN-000012") == ("InstallmentContract", 12)
    assert parse_reference("cu-000004") == ("Customer", 4)
    assert parse_reference("PY-88") == ("Payment", 88)
    assert parse_reference("not-a-code") is None
    assert parse_reference("XX-000001") is None
    assert parse_reference("12") is None


# --------------------------------------------------------------------------- #
# reference_code on API responses (alongside the existing numeric id)
# --------------------------------------------------------------------------- #
def test_customer_response_carries_reference_code(client):
    c = make_customer(client, national_id="REF-CU")
    assert c["reference_code"] == format_reference("Customer", c["id"])
    assert "id" in c  # numeric id NOT removed

    got = client.get(f"/customers/{c['id']}").json()
    assert got["reference_code"] == c["reference_code"]

    row = client.get("/customers?search=REF-CU").json()[0]
    assert row["reference_code"] == c["reference_code"]


def test_product_response_carries_reference_code(client):
    p = make_product(client, name="RefFridge")
    assert p["reference_code"] == format_reference("Product", p["id"])
    row = client.get("/products?search=reffridge").json()[0]
    assert row["reference_code"] == p["reference_code"]


def test_application_and_offer_and_contract_reference_codes(client):
    ctx = active_contract(client, national_id="REF-FLOW")
    cid = ctx["contract_id"]

    contract = client.get(f"/contracts/{cid}").json()
    assert contract["reference_code"] == format_reference("InstallmentContract", cid)
    # nested sales order also gets a code
    so = contract["sales_order"]
    assert so["reference_code"] == format_reference("SalesOrder", so["id"])
    assert so["application_reference"] == format_reference(
        "CreditApplication", so["application_id"]
    )

    app_id = ctx["application"]["id"]
    application = client.get(f"/applications/{app_id}").json()
    assert application["reference_code"] == format_reference("CreditApplication", app_id)

    offer = client.get(f"/offers/{ctx['offer']['id']}").json()
    assert offer["reference_code"] == format_reference(
        "InstallmentOffer", ctx["offer"]["id"]
    )


def test_reference_code_present_in_actual_http_json_responses(client):
    """Step 14 bug fix — regression guard.

    Live testing found `reference_code` missing from real API responses even
    though `format_reference()` and the Pydantic `computed_field` were both
    correct in isolation. The gap was never in the schema code: it was that
    the running server had been built/started before the Step 14 change and
    never restarted, so it kept serving a stale process. No unit test can
    catch a stale deployment — what a test *can* do is assert against the
    actual HTTP JSON body (via the real TestClient request/response cycle,
    not a Pydantic model constructed in-process) so a future regression of
    this shape — code correct, but not actually reaching the response body
    for any reason — fails loudly here first.
    """
    customer = make_customer(client, national_id="E2E-REF")
    body = client.get(f"/customers/{customer['id']}").json()
    assert body["reference_code"] == f"CU-{customer['id']:06d}"

    ctx = active_contract(client, national_id="E2E-REF-2")
    contract_body = client.get(f"/contracts/{ctx['contract_id']}").json()
    assert contract_body["reference_code"] == f"CN-{ctx['contract_id']:06d}"

    app_body = client.get(f"/applications/{ctx['application']['id']}").json()
    assert app_body["reference_code"] == f"AP-{ctx['application']['id']:06d}"


def test_payment_and_collection_case_reference_codes(client):
    ctx = active_contract(client, national_id="REF-PAY")
    cid = ctx["contract_id"]
    total = ctx["schedule"][0]["total"]

    res = client.post(
        f"/contracts/{cid}/payments",
        json={"amount": total, "external_reference": "REF-PAY-1"},
    ).json()
    pay = res["payment"]
    assert pay["reference_code"] == format_reference("Payment", pay["id"])
    assert pay["contract_reference"] == format_reference("InstallmentContract", cid)

    client.post("/jobs/assess-overdue", json={"as_of": "2027-06-01"})
    case = client.get("/collections/cases").json()[0]
    assert case["reference_code"] == format_reference("CollectionCase", case["id"])
    assert case["contract_reference"] == format_reference(
        "InstallmentContract", case["contract_id"]
    )
