import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContractPage } from "../pages/ContractPage";
import { mockFetch, renderWithProviders } from "./helpers";

function contract(overrides: Record<string, unknown>) {
  return {
    id: 5,
    sales_order_id: 5,
    tenor_months: 12,
    total_profit: 81,
    unearned_profit_balance: 81,
    status: "created",
    created_at: "2026-01-01T00:00:00Z",
    activated_at: null,
    sales_order: {
      id: 5,
      application_id: 5,
      product_id: 1,
      offer_id: 5,
      sale_price: 1281,
      down_payment_amount: 300,
      created_at: "2026-01-01T00:00:00Z",
    },
    installments: [],
    late_fee_charges: [],
    closure: null,
    ...overrides,
  };
}

function renderContract(c: Record<string, unknown>) {
  mockFetch([
    { method: "GET", url: "/contracts/5/receivable", status: 403, json: { detail: "no" } },
    { method: "GET", url: "/contracts/5", json: c },
  ]);
  return renderWithProviders(<ContractPage />, {
    user: { role: "finance_officer" },
    path: "/contracts/5",
    routePath: "/contracts/:contractId",
  });
}

describe("Contract closure actions", () => {
  it("shows Cancel only while status is 'created'", async () => {
    renderContract(contract({ status: "created" }));
    expect(await screen.findByTestId("cancel-contract")).toBeInTheDocument();
    expect(screen.queryByTestId("return-product")).not.toBeInTheDocument();
    expect(screen.queryByTestId("get-quote")).not.toBeInTheDocument();
  });

  it("shows Return and settlement quote only while status is 'active'", async () => {
    renderContract(contract({ status: "active", activated_at: "2026-02-01T00:00:00Z" }));
    expect(await screen.findByTestId("return-product")).toBeInTheDocument();
    expect(screen.getByTestId("get-quote")).toBeInTheDocument();
    expect(screen.queryByTestId("cancel-contract")).not.toBeInTheDocument();
  });

  it("hides all closure actions and shows the closure once one exists", async () => {
    renderContract(
      contract({
        status: "closed",
        closure: {
          id: 1,
          contract_id: 5,
          reason: "cancellation",
          financial_adjustment: 300,
          closed_at: "2026-03-01T00:00:00Z",
          notes: "Pre-delivery cancellation.",
        },
      }),
    );
    expect(await screen.findByTestId("closure-info")).toBeInTheDocument();
    expect(screen.getByTestId("closure-adjustment")).toHaveTextContent("300.00");
    expect(screen.queryByTestId("cancel-contract")).not.toBeInTheDocument();
    expect(screen.queryByTestId("return-product")).not.toBeInTheDocument();
    expect(screen.queryByTestId("get-quote")).not.toBeInTheDocument();
  });
});
