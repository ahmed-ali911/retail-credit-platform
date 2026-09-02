"""Step 12 — directory filters (search optional + status) and the
profitability report's `level` drill-down."""
import pytest

from tests.helpers import (
    active_contract,
    approved_application,
    make_application,
    make_customer,
    make_product,
)

APPROX = dict(abs=0.02)


# --------------------------------------------------------------------------- #
# A — Customer & Product directories
# --------------------------------------------------------------------------- #
def test_customer_directory_returns_all_without_a_search_term(client):
    make_customer(client, national_id="D-1", name="Anna")
    make_customer(client, national_id="D-2", name="Bora")

    rows = client.get("/customers").json()
    assert {r["national_id"] for r in rows} == {"D-1", "D-2"}


def test_customer_status_filter(client):
    make_customer(client, national_id="S-ACT", name="Active One", status="Active")
    make_customer(client, national_id="S-INA", name="Inactive One", status="Inactive")

    active = client.get("/customers?status=active").json()
    assert {r["national_id"] for r in active} == {"S-ACT"}
    inactive = client.get("/customers?status=inactive").json()
    assert {r["national_id"] for r in inactive} == {"S-INA"}
    assert len(client.get("/customers?status=all").json()) == 2
    assert client.get("/customers?status=bogus").status_code == 422


def test_customer_search_still_works_with_status(client):
    make_customer(client, national_id="C-A", name="Zoe Active", status="Active")
    make_customer(client, national_id="C-B", name="Zoe Inactive", status="Inactive")
    rows = client.get("/customers?search=zoe&status=active").json()
    assert [r["national_id"] for r in rows] == ["C-A"]


def test_product_directory_still_returns_all_by_default(client):
    make_product(client, name="Alpha")
    make_product(client, name="Beta")
    names = {p["name"] for p in client.get("/products").json()}
    assert {"Alpha", "Beta"} <= names
    # products have no status field yet -> only status=all is supported
    assert client.get("/products?status=all").status_code == 200
    assert client.get("/products?status=inactive").status_code == 422


def test_directory_role_gating_unchanged(client_as):
    assert client_as("customer").get("/customers").status_code == 403
    assert client_as("sales_employee").get("/customers").status_code == 200


# --------------------------------------------------------------------------- #
# B — Profitability drill-down levels
# --------------------------------------------------------------------------- #
def _contract_for(client, *, national_id, cash_price, category="appliances"):
    customer = make_customer(client, national_id=national_id, risk_score=750,
                             monthly_income=9000, existing_obligations=100)
    product = make_product(client, name=f"P-{national_id}", cash_price=cash_price)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=cash_price, requested_tenor_months=12)
    submitted = client.post(f"/applications/{app['id']}/submit").json()
    assert submitted["status"] == "approved", submitted
    offer = client.post(f"/applications/{app['id']}/offer",
                        json={"down_payment_amount": round(cash_price * 0.2, 2)}).json()
    acc = client.post(f"/offers/{offer['id']}/accept", json={
        "down_payment_confirmed": True,
        "down_payment_reference": f"DP-{national_id}",
    }).json()
    client.post(f"/contracts/{acc['contract_id']}/confirm-delivery")
    return {"customer": customer, "product": product, "contract_id": acc["contract_id"],
            "total_profit": offer["total_profit"]}


def test_profitability_levels_scope_and_sum_correctly(client):
    a = _contract_for(client, national_id="PL-A", cash_price=1200)
    b = _contract_for(client, national_id="PL-B", cash_price=3000)

    portfolio = client.get("/reports/profitability").json()
    assert portfolio["level"] == "portfolio"
    assert portfolio["contracts_counted"] == 2
    assert portfolio["total_contractual_profit"] == pytest.approx(
        a["total_profit"] + b["total_profit"], **APPROX
    )

    # scoped to customer A -> only A's contract
    cust = client.get(
        f"/reports/profitability?level=customer&customer_id={a['customer']['id']}"
    ).json()
    assert cust["scope"] == {"level": "customer", "customer_id": a["customer"]["id"]}
    assert cust["contracts_counted"] == 1
    assert cust["total_contractual_profit"] == pytest.approx(a["total_profit"], **APPROX)

    # scoped to product B
    prod = client.get(
        f"/reports/profitability?level=product&product_id={b['product']['id']}"
    ).json()
    assert prod["contracts_counted"] == 1
    assert prod["total_contractual_profit"] == pytest.approx(b["total_profit"], **APPROX)

    # scoped to category (both are "appliances")
    cat = client.get("/reports/profitability?level=category&category=appliances").json()
    assert cat["contracts_counted"] == 2

    # the identity still holds at every level
    for r in (portfolio, cust, prod, cat):
        assert r["total_recognized_profit"] + r["total_unearned_profit"] == pytest.approx(
            r["total_contractual_profit"], **APPROX
        )


def test_profitability_missing_required_param_is_422(client):
    assert client.get("/reports/profitability?level=category").status_code == 422
    assert client.get("/reports/profitability?level=product").status_code == 422
    assert client.get("/reports/profitability?level=customer").status_code == 422
    assert client.get("/reports/profitability?level=nonsense").status_code == 422
    # portfolio needs nothing
    assert client.get("/reports/profitability?level=portfolio").status_code == 200


def test_profitability_level_export_carries_the_scope(client):
    _contract_for(client, national_id="PX-1", cash_price=1500)
    resp = client.get(
        "/reports/profitability?level=customer&customer_id=1&format=csv"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "dimension" in resp.text
