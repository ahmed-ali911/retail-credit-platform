import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Card } from "../components/ui";
import { MetricTile } from "../components/MetricTile";
import { ToastProvider, useToast } from "../components/Toast";
import { SkeletonTable } from "../components/Skeleton";
import { ContractPage } from "../pages/ContractPage";
import { CustomerPage } from "../pages/CustomerPage";
import { ConfigPage } from "../pages/ConfigPage";
import { CustomerDirectoryPage } from "../pages/CustomerDirectoryPage";
import { mockFetch, renderWithProviders } from "./helpers";

const appCss = readFileSync(resolve(process.cwd(), "src/styles/app.css"), "utf8");
const printCss = readFileSync(resolve(process.cwd(), "src/styles/print.css"), "utf8");

// --------------------------------------------------------------------------- //
// Part A — card / KPI tile hover micro-interaction
// --------------------------------------------------------------------------- //
describe("Part A — card & tile hover", () => {
  it("one shared `.hover-raise` utility deepens the shadow and (for cards) shifts the border on hover", () => {
    const hoverBlock = appCss.match(/\.hover-raise:hover\s*\{[^}]*\}/)?.[0] ?? "";
    expect(hoverBlock).toMatch(/box-shadow:\s*var\(--shadow-md\)/);
    // border shift is scoped to cards / report cards
    expect(appCss).toMatch(/\.card\.hover-raise:hover[\s\S]*?border-color:/);
    // fast + no movement (no transform / scale)
    const trans = appCss.match(/\.hover-raise\s*\{[^}]*\}/)?.[0] ?? "";
    expect(trans).toMatch(/transition:[\s\S]*?(150|160|180|200)ms/);
    expect(hoverBlock).not.toMatch(/transform|scale/);
  });

  it("Card and MetricTile both apply the shared hover class", () => {
    const { container: c1 } = render(<Card>body</Card>);
    expect(c1.querySelector("section.card")).toHaveClass("hover-raise");

    const { container: c2 } = render(<MetricTile label="X" value="1" />);
    expect(c2.querySelector(".metric-tile")).toHaveClass("hover-raise");
  });
});

// --------------------------------------------------------------------------- //
// Part B — toast notifications
// --------------------------------------------------------------------------- //
describe("Part B — toast notifications", () => {
  function Harness() {
    const toast = useToast();
    return (
      <button type="button" onClick={() => toast.success("Payment recorded")}>
        fire
      </button>
    );
  }

  it("auto-dismisses after ~4s and offers a manual close", () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <Harness />
        </ToastProvider>,
      );
      act(() => {
        screen.getByRole("button", { name: "fire" }).click();
      });
      const toast = screen.getByTestId("toast");
      expect(toast).toHaveTextContent("Payment recorded");
      expect(within(toast).getByRole("button", { name: /dismiss/i })).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(4100);
      });
      expect(screen.queryByTestId("toast")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("the manual close button removes the toast immediately", () => {
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    act(() => {
      screen.getByRole("button", { name: "fire" }).click();
    });
    const toast = screen.getByTestId("toast");
    act(() => {
      within(toast).getByRole("button", { name: /dismiss/i }).click();
    });
    expect(screen.queryByTestId("toast")).not.toBeInTheDocument();
  });

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

  it("real action #1 — a recorded payment fires a confirmation toast", async () => {
    mockFetch([
      { method: "GET", url: /\/contracts\/5\/receivable/, status: 403, json: { detail: "no" } },
      { method: "GET", url: /\/applications\/7/, json: { id: 7, channel: "branch", created_by: "system", created_at: "2026-01-01T00:00:00Z" } },
      {
        method: "POST",
        url: /\/contracts\/5\/payments/,
        json: {
          replayed: false,
          payment: {
            id: 1, amount: 100, external_reference: "PMT-1", status: "applied",
            allocated_amount: 100, unallocated_amount: 0,
            reference_code: "PY-000001", contract_reference: "CN-000005",
          },
        },
      },
      { method: "GET", url: /\/contracts\/5$/, json: CONTRACT },
    ]);

    const user = userEvent.setup();
    renderWithProviders(
      <ToastProvider>
        <ContractPage />
      </ToastProvider>,
      { user: { role: "finance_officer" }, path: "/contracts/5", routePath: "/contracts/:contractId" },
    );

    await user.type(await screen.findByLabelText(/amount/i), "100");
    await user.type(screen.getByLabelText(/external reference/i), "PMT-1");
    await user.click(screen.getByRole("button", { name: /record payment/i }));

    expect(await screen.findByTestId("toast")).toHaveTextContent(/payment PY-000001 recorded/i);
  });

  it("real action #2 — a requested config change fires a toast and keeps the inline pending banner", async () => {
    mockFetch([
      {
        method: "GET",
        url: "/config/parameters",
        json: [{ key: "late_fee_rate", value: "0.02", value_type: "float", description: "Late fee rate" }],
      },
      {
        method: "PUT",
        url: "/config/parameters/late_fee_rate",
        status: 202,
        json: { id: 7, action_type: "config.update", status: "pending" },
      },
    ]);

    const user = userEvent.setup();
    renderWithProviders(
      <ToastProvider>
        <ConfigPage />
      </ToastProvider>,
      { user: { role: "admin" } },
    );

    await user.click(await screen.findByTestId("config-edit-late_fee_rate"));
    await user.clear(screen.getByLabelText(/new value for late_fee_rate/i));
    await user.type(screen.getByLabelText(/new value for late_fee_rate/i), "0.03");
    await user.click(screen.getByRole("button", { name: /request change/i }));

    expect(await screen.findByTestId("toast")).toHaveTextContent(/configuration change requested/i);
    // the actionable inline banner (with the Approvals link) stays as an inline message
    expect(screen.getByTestId("config-pending")).toHaveTextContent(/awaiting a different approver/i);
  });
});

// --------------------------------------------------------------------------- //
// Part C — loading skeletons
// --------------------------------------------------------------------------- //
describe("Part C — loading skeletons", () => {
  it("a directory shows a skeleton placeholder before its rows arrive", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/customers/,
        json: [
          { id: 1, reference_code: "CU-000001", name: "Ada", national_id: "N1", status: "active", risk_score: 700 },
        ],
      },
    ]);

    renderWithProviders(<CustomerDirectoryPage />, { user: { role: "sales_employee" } });

    // skeleton is on screen immediately, before the fetch resolves
    expect(screen.getByTestId("skeleton")).toBeInTheDocument();

    // …then it is replaced by the real rows
    expect(await screen.findByTestId("customer-row-1")).toBeInTheDocument();
    expect(screen.queryByTestId("skeleton")).not.toBeInTheDocument();
  });

  it("the SkeletonTable primitive renders the requested shape", () => {
    const { container } = render(<SkeletonTable rows={3} cols={4} />);
    expect(container.querySelectorAll(".skeleton-table__row")).toHaveLength(3);
    expect(container.querySelectorAll(".skeleton-table__cell")).toHaveLength(12);
  });
});

// --------------------------------------------------------------------------- //
// Part D — table polish
// --------------------------------------------------------------------------- //
describe("Part D — table polish", () => {
  it("data-table rows get a hover tint", () => {
    expect(appCss).toMatch(/table\.data tbody tr:hover\s*\{[^}]*background:/);
  });

  it("a directory with zero results shows the standardized empty state (icon + message), not a bare table", async () => {
    mockFetch([{ method: "GET", url: /\/customers/, json: [] }]);
    renderWithProviders(<CustomerDirectoryPage />, { user: { role: "sales_employee" } });

    const empty = await screen.findByTestId("customers-empty");
    expect(empty).toHaveClass("empty-state");
    expect(empty.querySelector("svg")).toBeTruthy(); // the icon
    expect(within(empty).getByText("No customers match.")).toBeInTheDocument();
    // no results table shell rendered
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Part F — printable Customer & Contract report layout
// --------------------------------------------------------------------------- //
describe("Part F — print views", () => {
  it("the print stylesheet hides the sidebar / top bar and reveals the report blocks", () => {
    const printBlock = printCss.match(/@media print\s*\{[\s\S]*\}/)?.[0] ?? "";
    expect(printBlock).toMatch(/\.appshell__sidebar[\s\S]*display:\s*none/);
    expect(printBlock).toMatch(/\.appshell__topbar/);
    expect(printBlock).toMatch(/\.print-only[\s\S]*display:\s*block/);
    expect(printBlock).toMatch(/\.no-print[\s\S]*display:\s*none/);
  });

  it("the Contract screen renders a Print button and a populated print header", async () => {
    mockFetch([
      { method: "GET", url: /\/contracts\/5\/receivable/, json: {
        outstanding_principal: 600, outstanding_profit: 40, outstanding_late_fees: 0,
        total_installments_paid: 2, total_installments_remaining: 10,
      } },
      { method: "GET", url: /\/applications\/7/, json: { id: 7, channel: "branch", created_by: "system", created_at: "2026-01-01T00:00:00Z" } },
      {
        method: "GET",
        url: /\/contracts\/5$/,
        json: {
          id: 5, reference_code: "CN-000005", sales_order_id: 5, tenor_months: 12,
          total_profit: 81, unearned_profit_balance: 81, status: "active",
          created_at: "2026-01-01T00:00:00Z", activated_at: "2026-02-01T00:00:00Z",
          sales_order: {
            id: 5, reference_code: "SO-000005", application_id: 7, application_reference: "AP-000007",
            product_id: 3, product_reference: "PR-000003", offer_id: 5,
            sale_price: 1281, down_payment_amount: 300, created_at: "2026-01-01T00:00:00Z",
          },
          installments: [], late_fee_charges: [], closure: null,
        },
      },
    ]);

    renderWithProviders(<ContractPage />, {
      user: { role: "finance_officer" }, path: "/contracts/5", routePath: "/contracts/:contractId",
    });

    expect(await screen.findByTestId("print-view-button")).toBeInTheDocument();
    const header = screen.getByTestId("contract-print-header");
    expect(header).toHaveClass("print-only");
    expect(header).toHaveTextContent("Contract CN-000005");
    expect(header).toHaveTextContent(/Reference CN-000005/);
    expect(header).toHaveTextContent("1,281.00"); // sale-price KPI
  });

  it("the Customer screen renders a Print button and a populated print header", async () => {
    mockFetch([
      { method: "GET", url: /\/customers\/9\/exposure/, json: { customer_id: 9, aggregation_level: "company_wide", total_outstanding: 1500, contracts: [] } },
      { method: "GET", url: /\/reports\/contracts/, json: { items: [], total: 0, limit: 200, offset: 0, totals: { row_count: 0 } } },
      {
        method: "GET",
        url: /\/customers\/9$/,
        json: {
          id: 9, name: "Dana Q", national_id: "ID-9", reference_code: "CU-000009",
          phone: null, email: null, status: "active", risk_score: 700,
          created_at: "2026-01-01T00:00:00Z",
          profile: { id: 1, customer_id: 9, monthly_income: 5000, existing_monthly_obligations: 200 },
        },
      },
    ]);

    renderWithProviders(<CustomerPage />, {
      user: { role: "credit_officer" }, path: "/customers/9", routePath: "/customers/:customerId",
    });

    expect(await screen.findByTestId("print-view-button")).toBeInTheDocument();
    const header = screen.getByTestId("customer-print-header");
    expect(header).toHaveClass("print-only");
    expect(header).toHaveTextContent("Customer Dana Q");
    expect(header).toHaveTextContent(/Reference CU-000009/);
  });
});

// --------------------------------------------------------------------------- //
// Part G — responsive grid audit
// --------------------------------------------------------------------------- //
describe("Part G — responsive", () => {
  it("KPI tile grid caps at 4 columns on desktop-wide and wide content scrolls inside its card", () => {
    expect(appCss).toMatch(
      /@media \(min-width: 1000px\)\s*\{\s*\.metric-grid\s*\{\s*grid-template-columns:\s*repeat\(4/,
    );
    expect(appCss).toMatch(/\.card:has\(table\.data\)\s*\{\s*overflow-x:\s*auto/);
    // Reports Center two-pane collapses on narrow
    expect(appCss).toMatch(/@media \(max-width: 760px\)[\s\S]*\.split\s*\{\s*flex-direction:\s*column/);
  });

  it("charts stay within their container (fluid grid + max-width)", () => {
    expect(appCss).toMatch(/\.chart\s*\{\s*max-width:\s*100%/);
    expect(appCss).toMatch(/\.chart-row\s*\{\s*grid-template-columns:\s*repeat\(auto-fit/);
  });
});
