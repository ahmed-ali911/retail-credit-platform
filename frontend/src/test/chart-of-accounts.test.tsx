import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ChartOfAccountsPage } from "../pages/ChartOfAccountsPage";
import { mockFetch, renderWithProviders } from "./helpers";

const ACCOUNTS = [
  { id: 1, account_code: "1000", account_name: "Bank / Cash", account_type: "ASSET", normal_balance: "DEBIT", is_active: true, is_demo: true, description: "DEMO", created_at: "2026-01-01T00:00:00Z", created_by: null, approved_at: "2026-01-01T00:00:00Z", approved_by: null },
  { id: 2, account_code: "4300", account_name: "Recovery Income", account_type: "INCOME", normal_balance: "CREDIT", is_active: true, is_demo: true, description: "DEMO", created_at: "2026-01-01T00:00:00Z", created_by: null, approved_at: "2026-01-01T00:00:00Z", approved_by: null },
];

const COA_REPORT = {
  columns: ["account_code", "account_name", "account_type", "normal_balance", "is_active", "is_demo", "usage_count", "created_at", "approved_at"],
  rows: ACCOUNTS.map((a) => ({
    account_code: a.account_code, account_name: a.account_name, account_type: a.account_type,
    normal_balance: a.normal_balance, is_active: a.is_active, is_demo: a.is_demo,
    usage_count: 3, created_at: a.created_at, approved_at: a.approved_at,
  })),
  totals: { row_count: 2 },
};

const MAPPING_REPORT = {
  columns: ["event_type", "version", "classification", "effective_from", "effective_to", "is_active", "is_demo", "line_sequence", "posting_side", "account_code", "amount_source", "change_reason"],
  rows: [
    { event_type: "payment_received", version: 1, classification: "POSTABLE", effective_from: "2026-01-01", effective_to: "", is_active: true, is_demo: true, line_sequence: 1, posting_side: "DEBIT", account_code: "1000", amount_source: "event_amount", change_reason: "" },
    { event_type: "ecl_provision_movement", version: 1, classification: "SUMMARY_ONLY", effective_from: "2026-01-01", effective_to: "", is_active: true, is_demo: true, line_sequence: "", posting_side: "", account_code: "", amount_source: "", change_reason: "" },
  ],
  totals: { row_count: 2 },
};

const TRIAL_BALANCE_REPORT = {
  columns: ["account_code", "account_name", "account_type", "opening_balance", "period_debits", "period_credits", "net_closing_balance"],
  rows: [
    { account_code: "1000", account_name: "Bank / Cash", account_type: "ASSET", opening_balance: 0, period_debits: 300, period_credits: 0, net_closing_balance: 300 },
  ],
  totals: { row_count: 1, period_debits: 300, period_credits: 300 },
  total_debits: 300,
  total_credits: 300,
  difference: 0,
  unmapped_event_count: 0,
  unmapped_event_total: 0,
  failed_journal_count: 0,
  summary_only_event_count: 1,
};

const UNMAPPED_REPORT = {
  columns: ["event_reference", "event_type", "event_date", "amount", "currency", "contract_id", "customer_id", "journal_status", "reason", "retry_eligible", "mapping_now_available"],
  rows: [
    { event_reference: "late-fee-waived-99", event_type: "late_fee_waived", event_date: "2000-01-01T00:00:00Z", amount: 5.0, currency: "KWD", contract_id: 7, customer_id: 3, journal_status: "UNMAPPED", reason: "No EventAccountMapping is effective", retry_eligible: false, mapping_now_available: true },
  ],
  totals: { row_count: 1 },
};

const LEDGER_REPORT = {
  columns: ["posting_date", "journal_reference", "event_reference", "event_type", "contract_id", "customer_id", "description", "debit", "credit", "running_balance", "journal_status", "mapping_version", "external_gl_reference"],
  rows: [
    { posting_date: "2026-01-01T00:00:00Z", journal_reference: "GLJ-1", event_reference: "down-payment-received-1", event_type: "down_payment_received", contract_id: 1, customer_id: 1, description: "", debit: 300, credit: 0, running_balance: 300, journal_status: "READY", mapping_version: 2, external_gl_reference: null },
  ],
  totals: { row_count: 1, debit: 300, credit: 0 },
  account_code: "1000",
  account_name: "Bank / Cash",
  normal_balance: "DEBIT",
  closing_balance: 300,
};

function baseHandlers() {
  return [
    { method: "GET", url: /\/gl\/accounts$/, json: ACCOUNTS },
    { method: "GET", url: /\/gl\/reports\/chart-of-accounts/, json: COA_REPORT },
    { method: "GET", url: /\/gl\/reports\/mappings/, json: MAPPING_REPORT },
    { method: "GET", url: /\/gl\/reports\/trial-balance/, json: TRIAL_BALANCE_REPORT },
    { method: "GET", url: /\/gl\/reports\/unmapped-failed/, json: UNMAPPED_REPORT },
    { method: "GET", url: /\/gl\/reports\/ledger/, json: LEDGER_REPORT },
  ];
}

describe("Chart of Accounts & GL — Accounts tab", () => {
  it("renders the seeded accounts by default", async () => {
    mockFetch(baseHandlers());
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    expect(await screen.findByText("1000")).toBeInTheDocument();
    expect(screen.getByText("4300")).toBeInTheDocument();
  });

  it("lets finance_officer propose a new account", async () => {
    const fetchMock = mockFetch([
      ...baseHandlers(),
      { method: "POST", url: /\/gl\/accounts\/propose-create/, json: { id: 55 } },
    ]);
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    const form = await screen.findByTestId("propose-account-form");
    await userEvent.type(within(form).getByLabelText(/Account code/), "9999");
    await userEvent.type(within(form).getByLabelText(/Account name/), "Test Account");
    await userEvent.click(within(form).getByRole("button", { name: /propose account/i }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/gl/accounts/propose-create"));
      expect(call).toBeTruthy();
    });
  });

  it("does not show the propose form for a role without propose access", async () => {
    mockFetch(baseHandlers());
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "collections_officer" } });

    await screen.findByTestId("coa-tab-accounts");
    expect(screen.queryByTestId("propose-account-form")).not.toBeInTheDocument();
  });
});

describe("Chart of Accounts & GL — Event Mappings tab", () => {
  it("switches to the mapping register and shows a SUMMARY_ONLY row", async () => {
    mockFetch(baseHandlers());
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    await screen.findByTestId("coa-tab-accounts");
    await userEvent.click(screen.getByRole("tab", { name: /event mappings/i }));

    expect(await screen.findByTestId("coa-tab-mappings")).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: /report results/i });
    expect(within(table).getByText("SUMMARY_ONLY")).toBeInTheDocument();
  });

  it("lets finance_officer propose a mapping change with debit/credit lines", async () => {
    const fetchMock = mockFetch([
      ...baseHandlers(),
      { method: "POST", url: /\/gl\/mappings\/propose-change/, json: { id: 77 } },
    ]);
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    await screen.findByTestId("coa-tab-accounts");
    await userEvent.click(screen.getByRole("tab", { name: /event mappings/i }));
    const form = await screen.findByTestId("propose-mapping-form");

    const lines = within(form).getAllByTestId(/mapping-line-/);
    expect(lines).toHaveLength(2);
    await userEvent.selectOptions(within(lines[0]).getByLabelText(/account/i), "1");
    await userEvent.selectOptions(within(lines[1]).getByLabelText(/account/i), "2");
    await userEvent.click(within(form).getByRole("button", { name: /propose mapping change/i }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/gl/mappings/propose-change"));
      expect(call).toBeTruthy();
    });
  });
});

describe("Chart of Accounts & GL — General Ledger tab", () => {
  it("requires an account to be selected before showing the ledger", async () => {
    mockFetch(baseHandlers());
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    await screen.findByTestId("coa-tab-accounts");
    await userEvent.click(screen.getByRole("tab", { name: /general ledger/i }));

    expect(await screen.findByTestId("coa-tab-ledger")).toBeInTheDocument();
    expect(screen.getByText(/choose an account/i)).toBeInTheDocument();
  });

  it("shows the ledger with a correct running balance once an account is picked", async () => {
    mockFetch(baseHandlers());
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    await screen.findByTestId("coa-tab-accounts");
    await userEvent.click(screen.getByRole("tab", { name: /general ledger/i }));
    await screen.findByTestId("coa-tab-ledger");

    await userEvent.selectOptions(screen.getByLabelText(/^account$/i), "1");

    expect(await screen.findByText("GLJ-1")).toBeInTheDocument();
    expect(screen.getAllByText("300").length).toBeGreaterThan(0);
  });
});

describe("Chart of Accounts & GL — Trial Balance tab", () => {
  it("shows the debit/credit summary line", async () => {
    mockFetch(baseHandlers());
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    await screen.findByTestId("coa-tab-accounts");
    await userEvent.click(screen.getByRole("tab", { name: /trial balance/i }));

    const summary = await screen.findByTestId("report-summary");
    expect(summary).toHaveTextContent("Total Debits: 300");
    expect(summary).toHaveTextContent("Total Credits: 300");
    expect(summary).toHaveTextContent("Difference: 0");
  });
});

describe("Chart of Accounts & GL — Unmapped/Failed tab", () => {
  it("lists an unmapped event with its reason", async () => {
    mockFetch(baseHandlers());
    renderWithProviders(<ChartOfAccountsPage />, { user: { role: "finance_officer" } });

    await screen.findByTestId("coa-tab-accounts");
    await userEvent.click(screen.getByRole("tab", { name: /unmapped/i }));

    expect(await screen.findByTestId("coa-tab-unmapped-failed")).toBeInTheDocument();
    expect(await screen.findByText("late-fee-waived-99")).toBeInTheDocument();
    expect(screen.getByText("UNMAPPED")).toBeInTheDocument();
  });
});
