import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
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

const GENERIC = (columns: string[], rows: Record<string, unknown>[], extra = {}) => ({
  columns,
  rows,
  ...extra,
});

describe("Reports Center — six categories + sub-reports (Step 13)", () => {
  it("shows all six categories, each with its own sub-report list", async () => {
    mockFetch([{ method: "GET", url: /./, json: {} }]);
    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();

    for (const cat of [
      "Contracts",
      "Profitability",
      "Customers",
      "Products",
      "Collections",
      "Aging",
    ]) {
      expect(screen.getByRole("tab", { name: cat })).toBeInTheDocument();
    }

    // Customers category exposes three sub-reports as a visible sub-nav
    await user.click(screen.getByRole("tab", { name: "Customers" }));
    for (const sub of ["Full Directory", "By Risk Band", "By Exposure"]) {
      expect(screen.getByRole("tab", { name: sub })).toBeInTheDocument();
    }
  });

  it("selecting a by-X sub-report runs it and renders the grouped rows", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: /\/reports\/customers\/by-risk/,
        json: GENERIC(
          ["customer_id", "name", "national_id", "risk_score", "band"],
          [
            { customer_id: 1, name: "Ada", national_id: "N1", risk_score: 800, band: "low" },
            { customer_id: 2, name: "Ben", national_id: "N2", risk_score: 550, band: "high" },
          ],
          { counts: { low: 1, medium: 0, high: 1, unscored: 0 } },
        ),
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "credit_manager" } });
    const user = userEvent.setup();

    await user.click(screen.getByRole("tab", { name: "Customers" }));
    await user.click(screen.getByRole("tab", { name: "By Risk Band" }));

    expect(await screen.findByTestId("report-row-0")).toHaveTextContent("Ada");
    expect(screen.getByTestId("report-row-1")).toHaveTextContent("high");
    expect(
      fetchMock.mock.calls.map((c) => String(c[0])).some((u) =>
        u.includes("/reports/customers/by-risk"),
      ),
    ).toBe(true);
  });

  it("the export group offers CSV / Excel / PDF and each hits the endpoint", async () => {
    const fetchMock = mockFetch([
      { method: "GET", url: /\/reports\/contracts\/by-status\?.*format=/, json: undefined },
      {
        method: "GET",
        url: /\/reports\/contracts\/by-status/,
        json: GENERIC(["status", "contracts"], [{ status: "active", contracts: 3 }]),
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "admin" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Contracts" }));
    await user.click(screen.getByRole("tab", { name: "By Status" }));

    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export Excel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export PDF" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Export Excel" }));
    await user.click(screen.getByRole("button", { name: "Export PDF" }));
    await user.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("by-status?format=csv"))).toBe(true);
      expect(urls.some((u) => u.includes("by-status?format=xlsx"))).toBe(true);
      expect(urls.some((u) => u.includes("by-status?format=pdf"))).toBe(true);
    });
  });

  it("the Aging report shows buckets and drills into one", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/reports\/aging\?bucket=0/,
        json: {
          columns: ["contract_id", "customer_name", "dpd", "outstanding_amount"],
          rows: [
            { contract_id: 5, customer_id: 9, customer_name: "Overdue Guy", installment_id: 50, sequence_number: 1, due_date: "2026-08-01", dpd: 20, outstanding_amount: 100 },
          ],
          bucket: 0,
          label: "1-30",
        },
      },
      {
        method: "GET",
        url: /\/reports\/aging/,
        json: {
          columns: ["bucket", "label", "installment_count", "outstanding_amount"],
          rows: [
            { bucket: 0, label: "1-30", installment_count: 1, outstanding_amount: 100 },
            { bucket: 1, label: "31-60", installment_count: 0, outstanding_amount: 0 },
          ],
          as_of: "2026-09-02",
        },
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Aging" }));

    expect(await screen.findByTestId("aging-bucket-0")).toHaveTextContent("1-30");
    await user.click(
      screen.getByTestId("aging-bucket-0").querySelector("button")!,
    );
    expect(await screen.findByTestId("aging-detail-50")).toHaveTextContent("Overdue Guy");
  });

  it("keeps the three links-out for the literal full-list views", async () => {
    mockFetch([{ method: "GET", url: /./, json: {} }]);
    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();

    for (const [cat, href] of [
      ["Customers", "/customers"],
      ["Products", "/products"],
      ["Collections", "/collections"],
    ] as const) {
      await user.click(screen.getByRole("tab", { name: cat }));
      const link = await screen.findByTestId(`reports-link-${cat}`);
      expect(link).toHaveAttribute("href", href);
    }
    // no duplicate search box is rendered inside Reports Center
    expect(screen.queryByLabelText(/search by name/i)).not.toBeInTheDocument();
  });
});
