import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { WriteOffCard } from "../components/WriteOffCard";
import { WriteOffRecoveryListPage } from "../pages/WriteOffRecoveryListPage";
import { mockFetch, renderWithProviders } from "./helpers";

const ELIGIBLE = {
  contract_id: 5,
  status: "ELIGIBLE",
  indicators: [
    { id: "contract_active", name: "Contract is active", category: "structural", result: "satisfied", detail: "Contract status: active" },
    { id: "dpd_threshold", name: "DPD >= 180", category: "delinquency", result: "satisfied", detail: "DPD 200 vs threshold 180" },
    { id: "ecl_stage", name: "ECL stage >= 3", category: "credit_risk", result: "satisfied", detail: "Stage 3 vs minimum 3" },
  ],
  dpd: 200,
  ecl_stage: 3,
  ecl_amount: 150.0,
  provision_amount: 150.0,
  collections_case_id: 9,
  collections_case_status: "open",
};

const NOT_ELIGIBLE = { ...ELIGIBLE, status: "NOT_ELIGIBLE", indicators: [
  { ...ELIGIBLE.indicators[1], result: "not_satisfied", detail: "DPD 90 vs threshold 180" },
] };

describe("WriteOffCard — request", () => {
  it("shows eligibility and submits a normal request when ELIGIBLE", async () => {
    const fetchMock = mockFetch([
      { method: "GET", url: /\/write-offs\/eligibility\/5/, json: ELIGIBLE },
      { method: "GET", url: /\/write-offs\/requests\?contract_id=5/, json: [] },
      { method: "GET", url: /\/write-offs\/executions\?contract_id=5/, json: [] },
      { method: "POST", url: /\/write-offs\/contracts\/5\/requests/, json: { id: 42 } },
    ]);

    renderWithProviders(
      <WriteOffCard contractId={5} contractStatus="active" onChanged={() => {}} />,
      { user: { role: "finance_officer" } },
    );

    expect(await screen.findByTestId("writeoff-eligibility")).toHaveTextContent("ELIGIBLE");
    expect(screen.queryByTestId("writeoff-exception-note")).not.toBeInTheDocument();

    const form = screen.getByTestId("writeoff-request-form");
    await userEvent.type(
      within(form).getByLabelText(/Justification/),
      "Exhausted all reasonable collection efforts over 200 days.",
    );
    await userEvent.click(within(form).getByRole("button", { name: /request write-off/i }));

    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/write-offs/contracts/5/requests"));
    expect(call).toBeTruthy();
    const body = JSON.parse((call![1] as RequestInit).body as string);
    expect(body.write_off_type).toBe("FULL");
    expect(body.exception_justification).toBeUndefined();
  });

  it("requires exception_justification when NOT_ELIGIBLE", async () => {
    mockFetch([
      { method: "GET", url: /\/write-offs\/eligibility\/5/, json: NOT_ELIGIBLE },
      { method: "GET", url: /\/write-offs\/requests\?contract_id=5/, json: [] },
      { method: "GET", url: /\/write-offs\/executions\?contract_id=5/, json: [] },
    ]);

    renderWithProviders(
      <WriteOffCard contractId={5} contractStatus="active" onChanged={() => {}} />,
      { user: { role: "finance_officer" } },
    );

    expect(await screen.findByTestId("writeoff-exception-note")).toHaveTextContent("NOT_ELIGIBLE");
    expect(screen.getByTestId("exception-justification-field")).toBeInTheDocument();
  });

  it("renders nothing for a non-active contract with no write-off history", async () => {
    mockFetch([
      { method: "GET", url: /\/write-offs\/requests\?contract_id=5/, json: [] },
      { method: "GET", url: /\/write-offs\/executions\?contract_id=5/, json: [] },
    ]);
    renderWithProviders(
      <WriteOffCard contractId={5} contractStatus="closed" onChanged={() => {}} />,
      { user: { role: "finance_officer" } },
    );
    await waitFor(() =>
      expect(screen.queryByTestId("writeoff-request-form")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/Write-off/)).not.toBeInTheDocument();
  });
});

describe("WriteOffCard — execution & recovery", () => {
  const EXECUTION_DETAIL = {
    id: 7,
    write_off_request_id: 42,
    contract_id: 5,
    customer_id: 1,
    write_off_type: "FULL",
    executed_principal: 600.0,
    executed_profit: 50.0,
    executed_late_fee: 0.0,
    executed_other_charges: 0.0,
    remaining_principal: 0,
    remaining_profit: 0,
    remaining_late_fee: 0,
    ecl_stage_snapshot: 3,
    ecl_amount_snapshot: 150.0,
    provision_amount_snapshot: 150.0,
    contract_closure_id: 3,
    collection_case_id: 9,
    accounting_event_id: 11,
    executed_by: 1,
    executed_at: "2026-09-18T00:00:00Z",
    recoveries: [],
    total_recovered: 0,
    remaining_recoverable: 650.0,
  };

  it("shows the executed write-off and records a recovery", async () => {
    const fetchMock = mockFetch([
      { method: "GET", url: /\/write-offs\/requests\?contract_id=5/, json: [] },
      {
        method: "GET",
        url: /\/write-offs\/executions\?contract_id=5/,
        json: [{ id: 7 }],
      },
      { method: "GET", url: /\/write-offs\/executions\/7$/, json: EXECUTION_DETAIL },
      {
        method: "POST",
        url: /\/write-offs\/executions\/7\/recoveries/,
        json: { id: 1, amount: 100 },
      },
    ]);

    renderWithProviders(
      <WriteOffCard contractId={5} contractStatus="closed" onChanged={() => {}} />,
      { user: { role: "finance_officer" } },
    );

    expect(await screen.findByTestId("writeoff-execution-summary")).toHaveTextContent("FULL");
    expect(screen.getByTestId("remaining-recoverable")).toHaveTextContent("650.00");

    const form = screen.getByTestId("record-recovery-form");
    await userEvent.type(within(form).getByLabelText(/Amount/), "100");
    await userEvent.type(within(form).getByLabelText(/External reference/), "BANK-REF-1");
    await userEvent.click(within(form).getByRole("button", { name: /record recovery/i }));

    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/recoveries"));
    expect(call).toBeTruthy();
    const body = JSON.parse((call![1] as RequestInit).body as string);
    expect(body.amount).toBe(100);
    expect(body.external_reference).toBe("BANK-REF-1");
  });

  it("hides the recovery form from a role without finance/admin access", async () => {
    mockFetch([
      { method: "GET", url: /\/write-offs\/requests\?contract_id=5/, json: [] },
      { method: "GET", url: /\/write-offs\/executions\?contract_id=5/, json: [{ id: 7 }] },
      { method: "GET", url: /\/write-offs\/executions\/7$/, json: EXECUTION_DETAIL },
    ]);
    renderWithProviders(
      <WriteOffCard contractId={5} contractStatus="closed" onChanged={() => {}} />,
      { user: { role: "collections_officer" } },
    );
    await screen.findByTestId("writeoff-execution-summary");
    expect(screen.queryByTestId("record-recovery-form")).not.toBeInTheDocument();
  });
});

describe("Write-offs & Recoveries list", () => {
  it("lists requests and filters by status", async () => {
    const rows = [
      {
        id: 1, contract_id: 5, customer_id: 1, write_off_type: "FULL", status: "EXECUTED",
        reason_code: "COLLECTIONS_EXHAUSTED", justification: "x", evidence_ref: null, comments: null,
        eligibility_status: "ELIGIBLE", eligibility_snapshot: [], is_exception: false, exception_justification: null,
        snapshot_principal_outstanding: 600, snapshot_profit_outstanding: 50, snapshot_late_fee_outstanding: 0,
        snapshot_other_charges_outstanding: 0, snapshot_total_outstanding: 650, snapshot_dpd: 200,
        snapshot_ecl_stage: 3, snapshot_ecl_amount: 150, snapshot_provision_amount: 150,
        snapshot_collections_case_status: "open", snapshot_collections_case_id: 9,
        requested_principal: 600, requested_profit: 50, requested_late_fee: 0, requested_other_charges: 0,
        approval_request_id: 10, requested_by: 2, approved_by: 3, created_at: "2026-09-18T00:00:00Z", decided_at: null,
      },
      {
        id: 2, contract_id: 6, customer_id: 2, write_off_type: "PARTIAL", status: "PENDING",
        reason_code: "MANAGEMENT_DECISION", justification: "y", evidence_ref: null, comments: null,
        eligibility_status: "NOT_ELIGIBLE", eligibility_snapshot: [], is_exception: true, exception_justification: "board approved",
        snapshot_principal_outstanding: 300, snapshot_profit_outstanding: 20, snapshot_late_fee_outstanding: 0,
        snapshot_other_charges_outstanding: 0, snapshot_total_outstanding: 320, snapshot_dpd: 90,
        snapshot_ecl_stage: 3, snapshot_ecl_amount: 80, snapshot_provision_amount: 80,
        snapshot_collections_case_status: "open", snapshot_collections_case_id: 11,
        requested_principal: 100, requested_profit: 0, requested_late_fee: 0, requested_other_charges: 0,
        approval_request_id: 11, requested_by: 2, approved_by: null, created_at: "2026-09-18T00:00:00Z", decided_at: null,
      },
    ];

    mockFetch([
      { method: "GET", url: /\/write-offs\/requests\?status=EXECUTED/, json: rows.filter((r) => r.status === "EXECUTED") },
      { method: "GET", url: /\/write-offs\/requests$/, json: rows },
    ]);

    renderWithProviders(<WriteOffRecoveryListPage />, { user: { role: "admin" } });

    expect(await screen.findByTestId("writeoff-list-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("writeoff-list-row-2")).toHaveTextContent("exception");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText(/status/i), "EXECUTED");

    await waitFor(() => expect(screen.queryByTestId("writeoff-list-row-2")).not.toBeInTheDocument());
    expect(screen.getByTestId("writeoff-list-row-1")).toBeInTheDocument();
  });
});
