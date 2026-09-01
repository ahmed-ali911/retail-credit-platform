import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { InventoryPage } from "../pages/InventoryPage";
import { mockFetch, renderWithProviders } from "./helpers";

function product(overrides: Record<string, unknown> = {}) {
  return {
    id: 3,
    name: "Fridge",
    category: "appliances",
    cash_price: 900,
    installment_eligible: true,
    stock_quantity: 10,
    reserved_quantity: 2,
    available_quantity: 8,
    ...overrides,
  };
}

describe("Inventory adjustment screen", () => {
  it("a positive adjustment increases stock_quantity in the table immediately", async () => {
    mockFetch([
      { method: "GET", url: "/products", json: [product()] },
      { method: "GET", url: /\/audit\/events/, json: [] },
      {
        method: "POST",
        url: "/products/3/stock-adjustment",
        json: product({ stock_quantity: 15, available_quantity: 13 }),
      },
    ]);

    renderWithProviders(<InventoryPage />, { user: { role: "finance_officer" } });

    expect(await screen.findByTestId("inv-stock-3")).toHaveTextContent("10");

    const user = userEvent.setup();
    await user.click(screen.getByTestId("inv-adjust-3"));
    await user.type(screen.getByLabelText(/delta/i), "5");
    await user.type(screen.getByLabelText(/reason/i), "New pallet arrived");
    await user.click(screen.getByRole("button", { name: /apply adjustment/i }));

    await waitFor(() =>
      expect(screen.getByTestId("inv-stock-3")).toHaveTextContent("15"),
    );
  });

  it("a negative adjustment below reserved_quantity is rejected and shows the error", async () => {
    mockFetch([
      { method: "GET", url: "/products", json: [product()] },
      { method: "GET", url: /\/audit\/events/, json: [] },
      {
        method: "POST",
        url: "/products/3/stock-adjustment",
        status: 422,
        json: {
          detail:
            "Adjustment would drop stock_quantity to 1, below the reserved quantity of 2.",
        },
      },
    ]);

    renderWithProviders(<InventoryPage />, { user: { role: "finance_officer" } });
    await screen.findByTestId("inv-stock-3");

    const user = userEvent.setup();
    await user.click(screen.getByTestId("inv-adjust-3"));
    await user.type(screen.getByLabelText(/delta/i), "-9");
    await user.type(screen.getByLabelText(/reason/i), "write-down");
    await user.click(screen.getByRole("button", { name: /apply adjustment/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /below the reserved quantity of 2/i,
    );
    // unchanged
    expect(screen.getByTestId("inv-stock-3")).toHaveTextContent("10");
  });

  it("shows adjustments in the recent-adjustments panel from the audit endpoint", async () => {
    mockFetch([
      { method: "GET", url: "/products", json: [product()] },
      {
        method: "GET",
        url: /\/audit\/events\?entity_type=Product&action=stock_adjustment/,
        json: [
          {
            id: 55,
            user_id: 1,
            action: "stock_adjustment",
            entity_type: "Product",
            entity_id: "3",
            before_value: { stock_quantity: 10 },
            after_value: { stock_quantity: 9, delta: -1, reason: "damaged" },
            timestamp: "2026-04-01T00:00:00Z",
          },
        ],
      },
    ]);

    renderWithProviders(<InventoryPage />, { user: { role: "admin" } });

    expect(await screen.findByTestId("inv-event-55")).toHaveTextContent("damaged");
    expect(screen.getByTestId("inv-event-55")).toHaveTextContent("-1");
  });

  it("a sales_employee cannot see Inventory in the nav", async () => {
    const { Shell } = await import("../components/Shell");
    renderWithProviders(<Shell />, { user: { role: "sales_employee" } });
    expect(screen.getByRole("navigation")).not.toHaveTextContent("Inventory");
  });
});
