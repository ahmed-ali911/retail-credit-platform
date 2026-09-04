import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CustomerPage } from "../pages/CustomerPage";
import { mockFetch, renderWithProviders } from "./helpers";

describe("Customer exposure panel", () => {
  it("renders the per-contract breakdown from the mocked API", async () => {
    mockFetch([
      {
        method: "GET",
        url: "/customers/9/exposure",
        json: {
          customer_id: 9,
          aggregation_level: "company_wide",
          total_outstanding: 3100.5,
          contracts: [
            {
              contract_id: 21,
              status: "active",
              outstanding_principal: 1800,
              outstanding_profit: 150.5,
              outstanding_late_fees: 0,
              outstanding_total: 1950.5,
            },
            {
              contract_id: 22,
              status: "active",
              outstanding_principal: 1100,
              outstanding_profit: 50,
              outstanding_late_fees: 0,
              outstanding_total: 1150,
            },
          ],
        },
      },
      {
        method: "GET",
        url: /\/reports\/contracts/,
        json: { items: [], total: 0, limit: 200, offset: 0, totals: { row_count: 0 } },
      },
      {
        method: "GET",
        url: "/customers/9",
        json: {
          id: 9,
          name: "Dana Q",
          national_id: "ID-9",
          reference_code: "CU-000009",
          phone: null,
          email: null,
          status: "active",
          risk_score: 700,
          created_at: "2026-01-01T00:00:00Z",
          profile: {
            id: 1,
            customer_id: 9,
            monthly_income: 5000,
            existing_monthly_obligations: 200,
          },
        },
      },
    ]);

    renderWithProviders(<CustomerPage />, {
      user: { role: "credit_officer" },
      path: "/customers/9",
      routePath: "/customers/:customerId",
    });

    expect(await screen.findByTestId("exposure-total")).toHaveTextContent("3,100.50");
    expect(screen.getByTestId("exposure-row-21")).toHaveTextContent("1,950.50");
    expect(screen.getByTestId("exposure-row-22")).toHaveTextContent("1,150.00");
    // each contract row links (by its reference code) into the contract screen
    expect(screen.getByRole("link", { name: "CN-000021" })).toHaveAttribute(
      "href",
      "/contracts/21",
    );
  });
});
