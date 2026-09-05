import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ContractPage } from "../pages/ContractPage";
import { mockFetch, renderWithProviders } from "./helpers";

let fetchMock: ReturnType<typeof mockFetch>;

const CONTRACT = {
  id: 5,
  reference_code: "CN-000005",
  sales_order_id: 5,
  tenor_months: 12,
  total_profit: 81,
  unearned_profit_balance: 81,
  status: "active",
  created_at: "2026-01-01T00:00:00Z",
  activated_at: "2026-02-01T00:00:00Z",
  sales_order: {
    id: 5,
    reference_code: "SO-000005",
    application_id: 7,
    application_reference: "AP-000007",
    product_id: 3,
    product_reference: "PR-000003",
    offer_id: 5,
    sale_price: 1281,
    down_payment_amount: 300,
    created_at: "2026-01-01T00:00:00Z",
  },
  installments: [],
  late_fee_charges: [],
  closure: null,
};

function quote(overrides: Record<string, unknown>) {
  return {
    contract_id: 5,
    outstanding_principal: 900,
    outstanding_late_fees: 0,
    unearned_profit_total: 81,
    profit_rebate_pct: 0,
    profit_rebate_amount: 0,
    profit_still_charged: 81,
    final_payoff_amount: 981,
    quote_expiry: "2026-06-01T00:00:00Z",
    is_deviation: false,
    ...overrides,
  };
}

function render(handlers: Parameters<typeof mockFetch>[0]) {
  fetchMock = mockFetch([
    { method: "GET", url: /\/contracts\/5\/receivable/, status: 403, json: { detail: "no" } },
    { method: "GET", url: /\/applications\/7/, json: { id: 7, channel: "branch", created_by: "system", created_at: "2026-01-01T00:00:00Z" } },
    ...handlers,
    { method: "GET", url: /\/contracts\/5$/, json: CONTRACT },
  ]);
  return renderWithProviders(<ContractPage />, {
    user: { role: "finance_officer" },
    path: "/contracts/5",
    routePath: "/contracts/:contractId",
  });
}

describe("Contract settlement — flexible profit rebate (BDR #7)", () => {
  it("no rebate: quote is not a deviation and settlement confirms immediately", async () => {
    render([
      { method: "GET", url: /\/contracts\/5\/settlement-quote/, json: quote({}) },
      {
        method: "POST",
        url: /\/contracts\/5\/settle/,
        json: { contract_id: 5, status: "closed", quote: quote({}), closure: { reason: "early_settlement" }, pending_approval: null },
      },
    ]);
    const user = userEvent.setup();

    await user.click(await screen.findByTestId("get-quote"));
    const panel = await screen.findByTestId("settlement-quote");
    expect(within(panel).queryByTestId("rebate-deviation-note")).not.toBeInTheDocument();
    expect(within(panel).getByTestId("confirm-settlement")).toHaveTextContent("Confirm settlement");

    await user.type(screen.getByLabelText(/settlement reference/i), "REF-1");
    await user.click(screen.getByTestId("confirm-settlement"));
    expect(await screen.findByText(/settled and closed/i)).toBeInTheDocument();
  });

  it("a requested rebate flags a deviation and routes settlement through approval", async () => {
    render([
      {
        method: "GET",
        url: /\/contracts\/5\/settlement-quote\?requested_rebate_pct=0\.4/,
        json: quote({
          profit_rebate_pct: 0.4,
          profit_rebate_amount: 32.4,
          profit_still_charged: 48.6,
          final_payoff_amount: 948.6,
          is_deviation: true,
        }),
      },
      {
        method: "POST",
        url: /\/contracts\/5\/settle/,
        json: {
          contract_id: 5,
          status: "pending_approval",
          quote: quote({ is_deviation: true }),
          closure: null,
          pending_approval: { id: 99, action_type: "contract.settlement_rebate", status: "pending" },
        },
      },
    ]);
    const user = userEvent.setup();

    await user.type(await screen.findByTestId("rebate-pct"), "40");
    await user.click(screen.getByTestId("get-quote"));

    const note = await screen.findByTestId("rebate-deviation-note");
    expect(note).toHaveTextContent(/requires approval from a different user/i);
    expect(screen.getByTestId("confirm-settlement")).toHaveTextContent(/needs approval/i);

    await user.type(screen.getByLabelText(/settlement reference/i), "REF-2");
    await user.click(screen.getByTestId("confirm-settlement"));

    expect(
      await screen.findByText(/a different approver must approve it in Approvals/i),
    ).toBeInTheDocument();
    // the deviation settle call carried the rebate fraction
    const settleCall = fetchMock.mock.calls.find(
      (c) =>
        (c[1] as RequestInit | undefined)?.method === "POST" &&
        String(c[0]).endsWith("/contracts/5/settle"),
    );
    expect(JSON.parse((settleCall![1] as RequestInit).body as string)).toMatchObject({
      requested_rebate_pct: 0.4,
    });
  });
});
