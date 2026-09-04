import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { CustomerPage } from "../pages/CustomerPage";
import { ContractPage } from "../pages/ContractPage";
import { NewApplicationPage } from "../pages/NewApplicationPage";
import { AppearancePanel } from "../components/AppearancePanel";
import {
  AgingBarChart,
  RiskBandDonut,
  StatusDonut,
  agingToData,
  riskToData,
  statusToData,
} from "../components/charts";
import { formatReference, parseReference, coerceId } from "../lib/reference";
import { mockFetch, renderWithProviders } from "./helpers";

// --------------------------------------------------------------------------- //
// Part A — light sidebar (guard against the old navy fill returning)
// --------------------------------------------------------------------------- //
describe("Part A — light theme sidebar", () => {
  const shellCss = readFileSync(
    resolve(process.cwd(), "src/styles/shell.css"),
    "utf8",
  );

  it("the sidebar block no longer uses the navy fill", () => {
    const block = shellCss.match(/\.appshell__sidebar\s*\{[^}]*\}/)?.[0] ?? "";
    expect(block).not.toMatch(/--color-primary-dark|#0f2350/i);
    expect(block).toMatch(/--surface-1|--color-surface|#fff|white/i);
  });

  it("the active nav item is a light primary tint, not a solid fill", () => {
    const block = shellCss.match(/\.navlink\.active\s*\{[^}]*\}/)?.[0] ?? "";
    expect(block).toMatch(/color-mix.*--color-primary/i); // tinted background
    expect(block).toMatch(/color:\s*var\(--color-primary\)/); // primary text
  });
});

// --------------------------------------------------------------------------- //
// Part B — numeric legibility
// --------------------------------------------------------------------------- //
describe("Part B — tabular numerics", () => {
  const appCss = readFileSync(
    resolve(process.cwd(), "src/styles/app.css"),
    "utf8",
  );

  it("reference codes, table numeric columns and KPI values all use tabular-nums", () => {
    expect(appCss).toMatch(/\.ref-code\s*\{[^}]*font-variant-numeric:\s*tabular-nums/);
    expect(appCss).toMatch(/table\.data td\.num[^{]*\{[^}]*tabular-nums/);
    const numRule = appCss.match(
      /\.num,\s*\.ref-code,\s*\.metric-tile__value[^{]*\{[^}]*tabular-nums/,
    );
    expect(numRule).toBeTruthy();
  });
});

// --------------------------------------------------------------------------- //
// Part C — reference codes
// --------------------------------------------------------------------------- //
describe("Part C — reference codes", () => {
  it("formatReference / parseReference / coerceId round-trip", () => {
    expect(formatReference("InstallmentContract", 12)).toBe("CN-000012");
    expect(formatReference("Customer", "4")).toBe("CU-000004");
    expect(parseReference("cn-000012")).toEqual({
      entity: "InstallmentContract",
      id: 12,
    });
    expect(coerceId("CN-000012")).toBe("12");
    expect(coerceId("42")).toBe("42");
    expect(coerceId("garbage")).toBe("");
  });

  it("Customer screen shows the reference code, not #id", async () => {
    mockFetch([
      { method: "GET", url: /\/customers\/9\/exposure/, json: { customer_id: 9, aggregation_level: "company_wide", total_outstanding: 0, contracts: [] } },
      {
        method: "GET",
        url: /\/customers\/9$/,
        json: {
          id: 9,
          reference_code: "CU-000009",
          name: "Dana Q",
          national_id: "ID-9",
          phone: null,
          email: null,
          status: "active",
          risk_score: 700,
          created_at: "2026-01-01T00:00:00Z",
          profile: null,
        },
      },
    ]);
    renderWithProviders(<CustomerPage />, {
      user: { role: "credit_officer" },
      path: "/customers/9",
      routePath: "/customers/:customerId",
    });
    expect(await screen.findByText("CU-000009")).toBeInTheDocument();
    expect(screen.queryByText("#9")).not.toBeInTheDocument();
  });

  it("Contract screen shows CN-/SO-/AP- codes", async () => {
    mockFetch([
      { method: "GET", url: /\/contracts\/12\/receivable/, status: 403, json: { detail: "no" } },
      {
        method: "GET",
        url: /\/contracts\/12$/,
        json: {
          id: 12,
          reference_code: "CN-000012",
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
        },
      },
    ]);
    renderWithProviders(<ContractPage />, {
      user: { role: "finance_officer" },
      path: "/contracts/12",
      routePath: "/contracts/:contractId",
    });
    expect(await screen.findByText("CN-000012")).toBeInTheDocument();
    expect(screen.getByText("SO-000005")).toBeInTheDocument();
    expect(screen.getByText("AP-000007")).toBeInTheDocument();
    expect(screen.getByText("PR-000003")).toBeInTheDocument();
  });

  it("Application screen shows AP- code after submit", async () => {
    const app = {
      id: 7,
      reference_code: "AP-000007",
      customer_id: 1,
      product_id: 1,
      requested_amount: 1000,
      requested_tenor_months: 12,
      channel: "branch",
      created_at: "2026-01-01T00:00:00Z",
      created_by: "system",
      assessments: [],
    };
    mockFetch([
      { method: "POST", url: "/applications", json: { ...app, status: "draft", latest_assessment: null } },
      { method: "POST", url: "/applications/7/submit", json: { ...app, status: "approved", latest_assessment: null } },
    ]);
    renderWithProviders(<NewApplicationPage />, { withAuth: false });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/customer #/i), "1");
    await user.type(screen.getByLabelText(/product #/i), "1");
    await user.type(screen.getByLabelText(/requested amount/i), "1000");
    await user.click(screen.getByRole("button", { name: /create & submit/i }));
    expect(await screen.findByText("AP-000007")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Part D — charts from real summary data only
// --------------------------------------------------------------------------- //
describe("Part D — dashboard charts", () => {
  it("data mappers map the summary shapes, dropping zero slices", () => {
    expect(statusToData({ created: 1, active: 3, closed: 0 })).toEqual([
      { name: "created", value: 1 },
      { name: "active", value: 3 },
    ]);
    expect(riskToData({ high: 2, low: 5, medium: 0, unscored: 1 })).toEqual([
      { name: "low", value: 5 },
      { name: "high", value: 2 },
      { name: "unscored", value: 1 },
    ]);
    expect(
      agingToData({ current: 4, buckets: { "1-30": 1, "31-60": 0 } }),
    ).toEqual([
      { name: "current", value: 4 },
      { name: "1-30", value: 1 },
      { name: "31-60", value: 0 },
    ]);
  });

  it("StatusDonut renders a legend entry per non-zero status", () => {
    render(<StatusDonut byStatus={{ created: 1, active: 3, closed: 0 }} />);
    expect(screen.getByRole("img", { name: /contracts by status/i })).toBeInTheDocument();
    expect(screen.getByText("created")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.queryByText("closed")).not.toBeInTheDocument();
  });

  it("RiskBandDonut and AgingBarChart render from mocked summary data", () => {
    const { unmount } = render(
      <RiskBandDonut bands={{ low: 5, medium: 2, high: 0, unscored: 1 }} />,
    );
    expect(screen.getByText("low")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
    unmount();
    render(
      <AgingBarChart
        distribution={{ current: 3, buckets: { "1-30": 2, "31-60": 1 } }}
      />,
    );
    expect(
      screen.getByRole("img", { name: /dpd aging distribution/i }),
    ).toBeInTheDocument();
  });

  it("an all-zero breakdown shows an empty state, not a fabricated chart", () => {
    render(<StatusDonut byStatus={{ created: 0, active: 0, closed: 0 }} />);
    expect(screen.getByTestId("chart-empty")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Part E — Appearance (browser-local only)
// --------------------------------------------------------------------------- //
describe("Part E — appearance control", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("style");
  });

  it("changing a colour persists to localStorage and applies to :root", async () => {
    const { unmount } = render(<AppearancePanel />);
    const input = screen.getByTestId("appearance-input-primary") as HTMLInputElement;
    expect(input.value).toBe("#2c5fd6"); // default

    fireEvent.change(input, { target: { value: "#123456" } });

    const stored = JSON.parse(localStorage.getItem("rc.appearance")!);
    expect(stored.primary).toBe("#123456");
    expect(
      document.documentElement.style.getPropertyValue("--color-primary"),
    ).toBe("#123456");
    // preview reflects it (the live CSS var is set)
    expect(screen.getByTestId("appearance-preview")).toBeInTheDocument();

    // "reload": a fresh mount reads the stored value
    unmount();
    render(<AppearancePanel />);
    expect(
      (screen.getByTestId("appearance-input-primary") as HTMLInputElement).value,
    ).toBe("#123456");
  });

  it("Reset to defaults clears storage and restores the token", async () => {
    render(<AppearancePanel />);
    const input = screen.getByTestId("appearance-input-primary") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "#999999" } });
    expect(localStorage.getItem("rc.appearance")).toBeTruthy();

    await userEvent.setup().click(screen.getByTestId("appearance-reset"));

    expect(localStorage.getItem("rc.appearance")).toBeNull();
    expect(
      (screen.getByTestId("appearance-input-primary") as HTMLInputElement).value,
    ).toBe("#2c5fd6");
    expect(
      document.documentElement.style.getPropertyValue("--color-primary"),
    ).toBe("#2c5fd6");
  });

  it("states the browser-local limitation in the UI", () => {
    render(<AppearancePanel />);
    expect(screen.getByTestId("appearance-local-note")).toHaveTextContent(
      /on this browser only/i,
    );
    expect(screen.getByTestId("appearance-local-note")).toHaveTextContent(
      /not.*(synced|shared)/i,
    );
  });
});
