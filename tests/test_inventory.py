"""Step 10 — customer/product search + minimal product stock tracking."""
from tests.helpers import (
    active_contract,
    approved_application,
    created_contract,
    make_application,
    make_customer,
    make_product,
)


# --------------------------------------------------------------------------- #
# customer + product search
# --------------------------------------------------------------------------- #
def test_customer_search_matches_name_or_national_id(client):
    make_customer(client, national_id="NIN-AAA", name="Layla Hassan")
    make_customer(client, national_id="NIN-BBB", name="Omar Said")

    by_name = client.get("/customers?search=layla").json()
    assert [c["national_id"] for c in by_name] == ["NIN-AAA"]

    by_nid = client.get("/customers?search=bbb").json()
    assert [c["name"] for c in by_nid] == ["Omar Said"]
    assert set(by_nid[0]) == {"id", "name", "national_id", "status", "risk_score"}


def test_customer_search_is_role_gated(client_as):
    assert client_as("customer").get("/customers?search=x").status_code == 403
    assert client_as("sales_employee").get("/customers?search=x").status_code == 200


def test_product_search_returns_stock_fields(client):
    make_product(client, name="Big Fridge", cash_price=900)
    rows = client.get("/products?search=fridge").json()
    assert len(rows) == 1
    p = rows[0]
    assert p["stock_quantity"] == 10  # config-driven placeholder default
    assert p["reserved_quantity"] == 0
    assert p["available_quantity"] == 10


def test_product_search_matches_category(client):
    make_product(client, name="Sofa")  # category "appliances" in the helper
    assert len(client.get("/products?search=appliance").json()) >= 1


def test_product_list_with_no_search_returns_every_product(client):
    make_product(client, name="Alpha")
    make_product(client, name="Beta")
    rows = client.get("/products").json()
    names = {p["name"] for p in rows}
    assert {"Alpha", "Beta"} <= names


def test_product_search_is_role_gated(client_as):
    assert client_as("customer").get("/products").status_code == 403
    assert client_as("sales_employee").get("/products").status_code == 200


# --------------------------------------------------------------------------- #
# stock adjustment
# --------------------------------------------------------------------------- #
def test_positive_adjustment_increases_stock(client):
    product = make_product(client)
    res = client.post(
        f"/products/{product['id']}/stock-adjustment",
        json={"delta": 5, "reason": "New pallet arrived"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["stock_quantity"] == 15
    assert res.json()["available_quantity"] == 15


def test_negative_adjustment_below_reserved_is_rejected(client, db):
    product = make_product(client, stock_quantity=3)
    # simulate a reservation directly
    from app.models.product import Product

    row = db.get(Product, product["id"])
    row.reserved_quantity = 2
    db.commit()

    res = client.post(
        f"/products/{product['id']}/stock-adjustment",
        json={"delta": -2, "reason": "write-down"},  # -> stock 1 < reserved 2
    )
    assert res.status_code == 422
    assert db.get(Product, product["id"]).stock_quantity == 3  # unchanged


def test_adjustment_writes_an_audit_event(client):
    product = make_product(client)
    client.post(
        f"/products/{product['id']}/stock-adjustment",
        json={"delta": -1, "reason": "damaged in transit"},
    )
    events = client.get(
        f"/audit/events?entity_type=Product&action=stock_adjustment"
    ).json()
    assert len(events) == 1
    ev = events[0]
    assert ev["entity_id"] == str(product["id"])
    assert ev["before_value"]["stock_quantity"] == 10
    assert ev["after_value"] == {
        "stock_quantity": 9,
        "delta": -1,
        "reason": "damaged in transit",
    }


def test_sales_employee_cannot_adjust_stock(client, client_as):
    product = make_product(client)
    res = client_as("sales_employee").post(
        f"/products/{product['id']}/stock-adjustment",
        json={"delta": 1, "reason": "nope"},
    )
    assert res.status_code == 403


# --------------------------------------------------------------------------- #
# the deduction / release lifecycle
# --------------------------------------------------------------------------- #
def test_accepting_an_offer_deducts_one_unit(client):
    ctx = created_contract(client, national_id="ST-DEDUCT")
    product = client.get(f"/products/{ctx['product']['id']}").json()
    assert product["stock_quantity"] == 9  # 10 - 1 at contract creation


def test_cancellation_releases_the_unit(client):
    ctx = created_contract(client, national_id="ST-CANCEL")
    pid = ctx["product"]["id"]
    assert client.get(f"/products/{pid}").json()["stock_quantity"] == 9
    client.post(f"/contracts/{ctx['contract_id']}/cancel")
    assert client.get(f"/products/{pid}").json()["stock_quantity"] == 10


def test_return_releases_the_unit(client):
    ctx = active_contract(client, national_id="ST-RETURN")
    pid = ctx["product"]["id"]
    assert client.get(f"/products/{pid}").json()["stock_quantity"] == 9
    client.post(f"/contracts/{ctx['contract_id']}/return")
    assert client.get(f"/products/{pid}").json()["stock_quantity"] == 10


def test_offer_generation_rejects_an_out_of_stock_product(client):
    ctx = approved_application(client, national_id="ST-OOS")
    pid = ctx["product"]["id"]
    # drive stock to zero
    client.post(
        f"/products/{pid}/stock-adjustment",
        json={"delta": -10, "reason": "clearance"},
    )
    res = client.post(
        f"/applications/{ctx['application']['id']}/offer",
        json={"down_payment_amount": 300},
    )
    assert res.status_code == 422
    assert "out of stock" in res.json()["detail"].lower()


def test_existing_flow_still_works_with_stock_present(client):
    # a plain end-to-end contract creation is unaffected
    ctx = active_contract(client, national_id="ST-OK")
    assert ctx["contract_id"]
