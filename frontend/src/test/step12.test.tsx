import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CustomerDirectoryPage } from "../pages/CustomerDirectoryPage";
import { ProductDirectoryPage } from "../pages/ProductDirectoryPage";
import { ReportsPage } from "../pages/ReportsPage";
import { mockFetch, renderWithProviders } from "./helpers";

beforeEach(() => {
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:mock"),
    revokeObjectURL: vi.fn(),
  });
  HTMLAnchorElement.prototype.click = vi.fn();
});

const CUSTOMER = (id: number, name: string, status: string) => ({
  id,
  name,
  national_id: `N${id}`,
  status,
  risk_score: 700,
});

describe("Step 12 — Customer Directory", () => {
  it("loads the full list on page load with no search term", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: /\/customers/,
        json: [CUSTOMER(1, "Anna", "Active"), CUSTOMER(2, "Bora", "Inactive")],
      },
    ]);

    renderWithProviders(<CustomerDirectoryPage />, { user: { role: "sales_employee" } });

    expect(await screen.findByTestId("customer-row-1")).toHaveTextContent("Anna");
    expect(screen.getByTestId("customer-row-2")).toBeInTheDocument();
    // the first call carries no ?search=
    expect(String(fetchMock.mock.calls[0][0])).not.toContain("search=");
  });

  it("the status filter re-queries with ?status=", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: /\/customers\?status=active/,
        json: [CUSTOMER(1, "Anna", "Active")],
      },
      {
        method: "GET",
        url: /\/customers/,
        json: [CUSTOMER(1, "Anna", "Active"), CUSTOMER(2, "Bora", "Inactive")],
      },
    ]);

    renderWithProviders(<CustomerDirectoryPage />, { user: { role: "credit_manager" } });
    await screen.findByTestId("customer-row-2");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText(/^status$/i), "active");

    await waitFor(() => {
      expect(screen.queryByTestId("customer-row-2")).not.toBeInTheDocument();
    });
    expect(
      fetchMock.mock.calls.map((c) => String(c[0])).some((u) => u.includes("status=active")),
    ).toBe(true);
  });
});

describe("Step 12 — Product Directory", () => {
  it("loads all products on page load (regression check)", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: /\/products/,
        json: [
          { id: 1, name: "Fridge", category: "appliances", cash_price: 900, installment_eligible: true, stock_quantity: 5, reserved_quantity: 0, available_quantity: 5 },
        ],
      },
    ]);

    renderWithProviders(<ProductDirectoryPage />, { user: { role: "finance_officer" } });
    expect(await screen.findByTestId("product-row-1")).toHaveTextContent("Fridge");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/products");
    expect(String(fetchMock.mock.calls[0][0])).not.toContain("search=");
  });
});

describe("Step 12 — Profitability drill-down", () => {
  const PROFIT = (level: string, scope: Record<string, unknown>, count: number) => ({
    level,
    scope: { level, ...scope },
    contracts_counted: count,
    total_contractual_profit: 100,
    total_recognized_profit: 40,
    total_unearned_profit: 60,
    by_tenor: {},
    by_category: {},
  });

  it("switches level to Customer, shows the picker, and re-runs scoped", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: /\/reports\/profitability\?.*level=customer/,
        json: PROFIT("customer", { customer_id: 7 }, 2),
      },
      {
        method: "GET",
        url: /\/reports\/profitability/,
        json: PROFIT("portfolio", {}, 9),
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Profitability" }));

    await user.selectOptions(screen.getByLabelText(/^level$/i), "customer");
    // the customer picker appears
    const custField = await screen.findByLabelText(/customer #/i);
    await user.type(custField, "7");
    await user.click(screen.getByRole("button", { name: /run report/i }));

    expect(await screen.findByTestId("prof-level")).toHaveTextContent("customer");
    expect(screen.getByTestId("prof-count")).toHaveTextContent("2");
    const url = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .find((u) => u.includes("level=customer"));
    expect(url).toContain("customer_id=7");
  });

  it("Category level exports with the scope in the query", async () => {
    const fetchMock = mockFetch([
      { method: "GET", url: /\/reports\/profitability\?.*format=csv/, json: undefined },
      { method: "GET", url: /\/reports\/profitability/, json: PROFIT("portfolio", {}, 1) },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "admin" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Profitability" }));
    await user.selectOptions(screen.getByLabelText(/^level$/i), "category");
    await user.selectOptions(screen.getByLabelText(/^category$/i), "furniture");
    await user.click(screen.getByRole("button", { name: /export csv/i }));

    await waitFor(() => {
      const u = fetchMock.mock.calls
        .map((c) => String(c[0]))
        .find((x) => x.includes("format=csv"));
      expect(u).toContain("level=category");
      expect(u).toContain("category=furniture");
    });
  });
});
