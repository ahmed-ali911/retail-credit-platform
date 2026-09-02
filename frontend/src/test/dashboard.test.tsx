import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DashboardPage } from "../pages/DashboardPage";
import { mockFetch, renderWithProviders } from "./helpers";

const SUMMARIES = [
  {
    method: "GET",
    url: "/reports/summary/executive",
    json: {
      total_customers: 12,
      active_contracts: 5,
      total_outstanding_receivable: 9876.5,
      total_profit_recognized: 210.25,
      approval_rate: 0.75,
      decisions_considered: 8,
    },
  },
  {
    method: "GET",
    url: "/reports/summary/operations",
    json: {
      payments_today_count: 3,
      payments_today_amount: 450,
      applications_submitted_today: 2,
      overdue_installments: 4,
      open_reconciliation_exceptions: 1,
      as_of: "2026-09-02",
    },
  },
  {
    method: "GET",
    url: "/reports/summary/portfolio",
    json: {
      contracts_by_status: { created: 1, active: 5, closed: 2 },
      dpd_distribution: {
        current: 3,
        buckets: { "1-30": 1, "31-60": 1, "61-90": 0, "91+": 0 },
        as_of: "2026-09-02",
      },
      average_contract_size: 1281,
    },
  },
  {
    method: "GET",
    url: "/reports/summary/collections",
    json: {
      open_cases: 2,
      promise_to_pay_kept: 1,
      promise_to_pay_broken: 0,
      late_fees_charged_count: 3,
      late_fees_charged_amount: 45,
      late_fees_waived_count: 1,
      late_fees_waived_amount: 15,
    },
  },
  {
    method: "GET",
    url: "/reports/summary/credit-risk",
    json: {
      customers_by_risk_band: { low: 6, medium: 3, high: 2, unscored: 1 },
      risk_band_thresholds: { low_min: 650, medium_min: 600 },
      top_customers_by_exposure: [
        { customer_id: 4, name: "Big Spender", total_outstanding: 5000 },
      ],
      rejection_rate: 0.125,
      referral_rate: 0.25,
      decisions_considered: 8,
    },
  },
];

describe("Executive Dashboard tabs", () => {
  it("renders tiles for a privileged role and switches tabs", async () => {
    mockFetch(SUMMARIES);
    renderWithProviders(<DashboardPage />, { user: { role: "finance_officer" } });

    // Executive tab (default)
    expect(await screen.findByText("Total customers")).toBeInTheDocument();
    expect(screen.getByText("9,876.50")).toBeInTheDocument();
    expect(screen.getAllByTestId("metric-tile").length).toBeGreaterThanOrEqual(5);

    // switch to Portfolio -> DPD table
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Portfolio" }));
    await waitFor(() =>
      expect(screen.getByTestId("dpd-1-30")).toHaveTextContent("1"),
    );

    // switch to Credit & Risk -> top-exposure list
    await user.click(screen.getByRole("tab", { name: "Credit & Risk" }));
    expect(await screen.findByTestId("top-exp-4")).toHaveTextContent("Big Spender");
  });

  it("no longer renders the Start-a-new-flow / Open-a-record sections (Step 12 D)", async () => {
    mockFetch(SUMMARIES);
    renderWithProviders(<DashboardPage />, { user: { role: "admin" } });
    await screen.findByText("Total customers");
    expect(screen.queryByText(/start a new flow/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/open an existing record/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open contract/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create customer/i })).not.toBeInTheDocument();
  });

  it("hides the tabs for a non-privileged role", () => {
    mockFetch(SUMMARIES);
    renderWithProviders(<DashboardPage />, { user: { role: "sales_employee" } });
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("every tab panel is read-only — no mutating action buttons (Step 12 C)", async () => {
    mockFetch(SUMMARIES);
    renderWithProviders(<DashboardPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    await screen.findByText("Total customers");

    const MUTATING =
      /\b(create|new|add|approve|reject|cancel|settle|adjust|waive|delete|remove|submit|run|save|update|edit|request|record|post|match|resolve|confirm|generate)\b/i;

    for (const tabName of [
      "Executive",
      "Operations",
      "Portfolio",
      "Collections",
      "Credit & Risk",
    ]) {
      await user.click(screen.getByRole("tab", { name: tabName }));
      const panel = await screen.findByTestId(`tab-${tabName}`);
      const buttons = panel.querySelectorAll("button");
      for (const b of buttons) {
        expect(b.textContent ?? "").not.toMatch(MUTATING);
      }
      // links inside a panel only navigate to record detail pages
      const links = panel.querySelectorAll("a");
      for (const a of links) {
        expect(a.getAttribute("href") ?? "").toMatch(/^\/(customers|contracts|reports)\//);
      }
    }
  });
});
