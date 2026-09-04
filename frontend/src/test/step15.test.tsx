import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ReportsPage } from "../pages/ReportsPage";
import { CustomerDirectoryPage } from "../pages/CustomerDirectoryPage";
import { CustomerPage } from "../pages/CustomerPage";
import { ContractDirectoryPage } from "../pages/ContractDirectoryPage";
import { NewApplicationPage } from "../pages/NewApplicationPage";
import { ReconciliationPage } from "../pages/ReconciliationPage";
import { AppearancePanel } from "../components/AppearancePanel";
import { DEFAULT_APPEARANCE } from "../lib/appearance";
import { mockFetch, renderWithProviders } from "./helpers";

// --------------------------------------------------------------------------- //
// Part A — totals / counts
// --------------------------------------------------------------------------- //
describe("Part A — totals and counts", () => {
  it("Contracts report totals row matches a manually-summed scenario", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/reports\/contracts/,
        json: {
          items: [
            {
              contract_id: 7, status: "active", customer_id: 1, customer_name: "Ada L",
              product_id: 2, product_name: "Fridge", category: "appliances",
              tenor_months: 12, installment_sale_price: 1281, created_at: "2026-05-01T00:00:00Z",
              outstanding_total: 900, next_due_date: "2026-06-01",
            },
            {
              contract_id: 8, status: "active", customer_id: 2, customer_name: "Bo K",
              product_id: 3, product_name: "TV", category: "electronics",
              tenor_months: 6, installment_sale_price: 519, created_at: "2026-05-02T00:00:00Z",
              outstanding_total: 300, next_due_date: "2026-06-02",
            },
          ],
          total: 2,
          limit: 50,
          offset: 0,
          totals: { row_count: 2, installment_sale_price: 1800 }, // 1281 + 519, manually summed
        },
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /run report/i }));

    const totals = await screen.findByTestId("totals-row");
    expect(totals).toHaveTextContent("TOTAL (2 rows)");
    expect(totals).toHaveTextContent("1,800.00");
  });

  it("a generic sub-report (Products by availability) shows a totals row matching the mocked sums", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/reports\/products\/by-availability/,
        json: {
          columns: ["product_id", "name", "category", "stock_quantity", "reserved_quantity", "available_quantity", "state"],
          rows: [
            { product_id: 1, name: "A", category: "appliances", stock_quantity: 10, reserved_quantity: 2, available_quantity: 8, state: "available" },
            { product_id: 2, name: "B", category: "electronics", stock_quantity: 3, reserved_quantity: 0, available_quantity: 3, state: "available" },
          ],
          totals: { row_count: 2, stock_quantity: 13, reserved_quantity: 2, available_quantity: 11 },
          available: 2,
          sold_out: 0,
        },
      },
    ]);

    renderWithProviders(<ReportsPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Products" }));
    await user.click(screen.getByRole("tab", { name: "By Availability" }));

    const totals = await screen.findByTestId("totals-row");
    expect(totals).toHaveTextContent("TOTAL (2 rows)");
    expect(totals).toHaveTextContent("13"); // stock_quantity sum, matches 10 + 3
  });

  it("Customer Directory shows a plain row count, not a monetary total", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/customers/,
        json: [
          { id: 1, name: "Anna", national_id: "N1", status: "active", risk_score: 700, reference_code: "CU-000001" },
          { id: 2, name: "Bora", national_id: "N2", status: "active", risk_score: 650, reference_code: "CU-000002" },
        ],
      },
    ]);
    renderWithProviders(<CustomerDirectoryPage />, { user: { role: "sales_employee" } });
    expect(await screen.findByTestId("customers-count")).toHaveTextContent("2 customers");
    // no monetary total row is forced onto a non-amount-heavy directory
    expect(screen.queryByTestId("totals-row")).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Part B — richer Customer detail
// --------------------------------------------------------------------------- //
describe("Part B — richer Customer detail", () => {
  it("renders previously-hidden profile fields and shows closed contracts in history", async () => {
    mockFetch([
      {
        method: "GET",
        url: "/customers/9/exposure",
        json: { customer_id: 9, aggregation_level: "company_wide", total_outstanding: 0, contracts: [] },
      },
      {
        method: "GET",
        url: /\/reports\/contracts/,
        json: {
          items: [
            {
              contract_id: 40, status: "closed", customer_id: 9, customer_name: "Dana Q",
              product_id: 1, product_name: "Fridge", category: "appliances", tenor_months: 12,
              installment_sale_price: 1281, created_at: "2026-01-01T00:00:00Z",
              outstanding_total: 0, next_due_date: null,
            },
          ],
          total: 1, limit: 200, offset: 0, totals: { row_count: 1 },
        },
      },
      {
        method: "GET",
        url: "/customers/9",
        json: {
          id: 9, name: "Dana Q", national_id: "ID-9", reference_code: "CU-000009",
          phone: "+96500000000", email: "dana@example.com", status: "active", risk_score: 700,
          created_at: "2026-01-01T00:00:00Z",
          profile: {
            id: 1, customer_id: 9, monthly_income: 5000, existing_monthly_obligations: 200,
            employer_name: "ACME Co", employment_type: "full_time",
            address_line: "1 Main St", city: "Kuwait City", contact_phone: "+96511111111",
          },
        },
      },
    ]);

    renderWithProviders(<CustomerPage />, {
      user: { role: "credit_officer" },
      path: "/customers/9",
      routePath: "/customers/:customerId",
    });

    // previously-hidden profile fields now render
    expect(await screen.findByText("ACME Co")).toBeInTheDocument();
    expect(screen.getByText("full time")).toBeInTheDocument();
    expect(screen.getByText("1 Main St")).toBeInTheDocument();
    expect(screen.getByText("Kuwait City")).toBeInTheDocument();
    expect(screen.getByText("+96511111111")).toBeInTheDocument();

    // full history shows the closed contract, not "no open contracts" only
    expect(await screen.findByTestId("history-row-40")).toBeInTheDocument();
    expect(screen.getByText(/see full history below/i)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Part C — Contracts Directory
// --------------------------------------------------------------------------- //
describe("Part C — Contracts Directory", () => {
  it("searching by reference code resolves to contract_id and links the row to the contract page", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: /contract_id=12/,
        json: {
          items: [{
            contract_id: 12, status: "active", customer_id: 1, customer_name: "Ada L",
            product_id: 1, product_name: "Fridge", category: "appliances", tenor_months: 12,
            installment_sale_price: 1281, created_at: "2026-01-01T00:00:00Z",
            outstanding_total: 900, next_due_date: "2026-06-01",
          }],
          total: 1, limit: 50, offset: 0, totals: { row_count: 1 },
        },
      },
      { method: "GET", url: /\/reports\/contracts/, json: { items: [], total: 0, limit: 50, offset: 0, totals: { row_count: 0 } } },
    ]);

    renderWithProviders(<ContractDirectoryPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("contract-directory-ref-input"), "CN-000012");
    await user.click(screen.getByRole("button", { name: /search/i }));

    const row = await screen.findByTestId("contract-directory-row-12");
    expect(row).toHaveTextContent("Ada L");
    expect(screen.getByRole("link", { name: "CN-000012" })).toHaveAttribute(
      "href", "/contracts/12",
    );
    const url = fetchMock.mock.calls.map((c) => String(c[0])).find((u) => u.includes("contract_id=12"));
    expect(url).toBeTruthy();
  });

  it("searching by customer resolves the SearchSelect pick to customer_id", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/customers\?search=/,
        json: [{ id: 1, name: "Ada L", reference_code: "CU-000001" }],
      },
      {
        method: "GET",
        url: /customer_id=1/,
        json: {
          items: [{
            contract_id: 12, status: "active", customer_id: 1, customer_name: "Ada L",
            product_id: 1, product_name: "Fridge", category: "appliances", tenor_months: 12,
            installment_sale_price: 1281, created_at: "2026-01-01T00:00:00Z",
            outstanding_total: 900, next_due_date: "2026-06-01",
          }],
          total: 1, limit: 50, offset: 0, totals: { row_count: 1 },
        },
      },
      { method: "GET", url: /\/reports\/contracts/, json: { items: [], total: 0, limit: 50, offset: 0, totals: { row_count: 0 } } },
    ]);

    renderWithProviders(<ContractDirectoryPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("search-select-customer-input"), "Ada");
    const option = await screen.findByText("Ada L");
    await user.click(option);
    await user.click(screen.getByRole("button", { name: /search/i }));

    expect(await screen.findByTestId("contract-directory-row-12")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Part D — search-as-you-type on New Application
// --------------------------------------------------------------------------- //
describe("Part D — search-as-you-type dropdowns", () => {
  it("accepts a typed search selection", async () => {
    mockFetch([
      { method: "GET", url: /\/customers\?search=/, json: [{ id: 1, name: "Ada L", reference_code: "CU-000001" }] },
      { method: "GET", url: /\/products\?search=/, json: [] },
    ]);
    renderWithProviders(<NewApplicationPage />, { withAuth: false });
    const user = userEvent.setup();

    await user.type(screen.getByTestId("search-select-customer-input"), "Ada");
    const option = await screen.findByText("Ada L");
    await user.click(option);

    expect(screen.getByTestId("search-select-customer-input")).toHaveValue("CU-000001");
  });

  it("still accepts a pasted raw id / reference code directly, no search needed", async () => {
    mockFetch([]);
    renderWithProviders(<NewApplicationPage />, { withAuth: false });
    const input = screen.getByTestId("search-select-product-input");
    fireEvent.change(input, { target: { value: "PR-000004" } });
    expect(input).toHaveValue("PR-000004");
  });
});

// --------------------------------------------------------------------------- //
// Part E — Excel bank-statement upload
// --------------------------------------------------------------------------- //
describe("Part E — Excel bank-statement upload", () => {
  it("shows the upload summary after a well-formed file is processed", async () => {
    mockFetch([
      { method: "GET", url: "/reconciliation/status", json: {
        unreconciled_payments: 0, reconciled_payments: 1, exception_payments: 0,
        open_exceptions: 0, resolved_exceptions: 0, unmatched_bank_lines: 0,
      } },
      { method: "GET", url: /\/reconciliation\/exceptions/, json: [] },
      {
        method: "POST",
        url: "/reconciliation/bank-lines/upload",
        json: { rows_processed: 3, rows_ingested: 2, rows_rejected: 1, rejected: [{ row: 4, reason: "amount is empty" }], matched: 2, exceptions_created: 0 },
      },
    ]);

    renderWithProviders(<ReconciliationPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    const file = new File(["dummy"], "statement.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await user.upload(screen.getByTestId("statement-upload-input"), file);
    await user.click(screen.getByRole("button", { name: /^upload$/i }));

    const result = await screen.findByTestId("upload-result");
    expect(result).toHaveTextContent("ingested 2");
    expect(result).toHaveTextContent("matched 2");
    expect(result).toHaveTextContent("rejected 1");
    expect(screen.getByTestId("upload-rejected-4")).toHaveTextContent("amount is empty");
  });

  it("a missing-column rejection surfaces the backend's error, not a silent partial success", async () => {
    mockFetch([
      { method: "GET", url: "/reconciliation/status", json: {
        unreconciled_payments: 0, reconciled_payments: 0, exception_payments: 0,
        open_exceptions: 0, resolved_exceptions: 0, unmatched_bank_lines: 0,
      } },
      { method: "GET", url: /\/reconciliation\/exceptions/, json: [] },
      {
        method: "POST",
        url: "/reconciliation/bank-lines/upload",
        status: 422,
        json: { detail: "Missing required column(s): value_date." },
      },
    ]);

    renderWithProviders(<ReconciliationPage />, { user: { role: "finance_officer" } });
    const user = userEvent.setup();
    const file = new File(["dummy"], "statement.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await user.upload(screen.getByTestId("statement-upload-input"), file);
    await user.click(screen.getByRole("button", { name: /^upload$/i }));

    expect(await screen.findByText(/missing required column/i)).toBeInTheDocument();
    expect(screen.queryByTestId("upload-result")).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Part F — green default + Appearance reset
// --------------------------------------------------------------------------- //
describe("Part F — --color-secondary default is green", () => {
  const tokensCss = readFileSync(resolve(process.cwd(), "src/styles/tokens.css"), "utf8");

  it("the shipped default token value is green, not the old teal", () => {
    const match = tokensCss.match(/--color-secondary:\s*(#[0-9a-fA-F]{6})/);
    expect(match?.[1].toLowerCase()).toBe("#219653");
    expect(match?.[1].toLowerCase()).not.toBe("#2fb8c6");
  });

  it("DEFAULT_APPEARANCE.secondary matches the tokens.css default exactly", () => {
    expect(DEFAULT_APPEARANCE.secondary).toBe("#219653");
  });

  it("badge--good and the DPD-current chart slice are driven by --color-secondary (so they follow the new green)", () => {
    const appCss = readFileSync(resolve(process.cwd(), "src/styles/app.css"), "utf8");
    expect(appCss).toMatch(/\.badge--good\s*\{[^}]*var\(--color-secondary\)/);
  });

  it("Appearance's Reset restores the new green, not the old teal", async () => {
    localStorage.clear();
    document.documentElement.removeAttribute("style");
    render(<AppearancePanel />);
    const input = screen.getByTestId("appearance-input-secondary") as HTMLInputElement;
    expect(input.value).toBe("#219653"); // default on first mount

    fireEvent.change(input, { target: { value: "#000000" } });
    expect(input.value).toBe("#000000");

    const user = userEvent.setup();
    await user.click(screen.getByTestId("appearance-reset"));

    await waitFor(() => {
      expect(
        (screen.getByTestId("appearance-input-secondary") as HTMLInputElement).value,
      ).toBe("#219653");
    });
    expect(
      document.documentElement.style.getPropertyValue("--color-secondary"),
    ).toBe("#219653");
    expect(localStorage.getItem("rc.appearance")).toBeNull();
  });
});
