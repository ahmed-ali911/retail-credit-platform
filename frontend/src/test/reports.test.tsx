import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReportsPage } from "../pages/ReportsPage";
import { mockFetch, renderWithProviders } from "./helpers";

beforeEach(() => {
  // downloadFile() uses these — jsdom doesn't implement them
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:mock"),
    revokeObjectURL: vi.fn(),
  });
  HTMLAnchorElement.prototype.click = vi.fn();
});

describe("Reports Center", () => {
  it("runs a filtered Contracts report and renders the results", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: /\/reports\/contracts/,
        json: {
          items: [
            {
              contract_id: 7,
              status: "active",
              customer_id: 1,
              customer_name: "Ada L",
              product_id: 2,
              product_name: "Fridge",
              category: "appliances",
              tenor_months: 12,
              installment_sale_price: 1281,
              created_at: "2026-05-01T00:00:00Z",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
          totals: { row_count: 1, installment_sale_price: 1281 },
        },
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText(/status/i), "active");
    await user.click(screen.getByRole("button", { name: /run report/i }));

    expect(await screen.findByTestId("contract-report-row-7")).toHaveTextContent("Ada L");
    const url = fetchMock.mock.calls.map((c) => String(c[0])).find((u) => u.includes("/reports/contracts"));
    expect(url).toContain("status=active");
  });

  it("Contracts 'Export CSV' hits the endpoint with format=csv", async () => {
    const fetchMock = mockFetch([
      { method: "GET", url: /\/reports\/contracts\?.*format=csv/, status: 200, json: undefined },
      { method: "GET", url: /\/reports\/contracts/, json: { items: [], total: 0, limit: 50, offset: 0 } },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "admin" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /export csv/i }));

    await waitFor(() => {
      const csvCall = fetchMock.mock.calls
        .map((c) => String(c[0]))
        .find((u) => u.includes("format=csv"));
      expect(csvCall).toBeTruthy();
    });
  });

  it("runs the Profitability report and shows reconciling totals", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/reports\/profitability/,
        json: {
          contracts_counted: 2,
          total_contractual_profit: 162,
          total_recognized_profit: 40,
          total_unearned_profit: 122,
          by_tenor: { "12": { contractual_profit: 162, recognized_profit: 40, unearned_profit: 122, contracts: 2 } },
          by_category: { appliances: { contractual_profit: 162, recognized_profit: 40, unearned_profit: 122, contracts: 2 } },
        },
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "credit_manager" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Profitability" }));
    await user.click(screen.getByRole("button", { name: /run report/i }));

    expect(await screen.findByTestId("prof-contractual")).toHaveTextContent("162.00");
    expect(screen.getByTestId("prof-recognized")).toHaveTextContent("40.00");
    expect(screen.getByTestId("prof-unearned")).toHaveTextContent("122.00");
  });

  it("Customers/Products/Collections categories link to the existing screen, not a duplicate", async () => {
    mockFetch([{ method: "GET", url: /./, json: {} }]);
    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();

    await user.click(screen.getByRole("tab", { name: "Customers" }));
    const link = await screen.findByTestId("reports-link-Customers");
    expect(link).toHaveAttribute("href", "/customers");
    // no search box / results table is rendered here (that's the real screen's job)
    expect(screen.queryByLabelText(/search by name/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Collections" }));
    expect(await screen.findByTestId("reports-link-Collections")).toHaveAttribute(
      "href",
      "/collections",
    );
  });
});
