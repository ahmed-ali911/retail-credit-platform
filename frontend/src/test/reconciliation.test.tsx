import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ReconciliationPage } from "../pages/ReconciliationPage";
import { mockFetch, renderWithProviders } from "./helpers";

describe("Reconciliation screen", () => {
  it("adds a bank line and runs matching, updating the displayed counts", async () => {
    const state = {
      unreconciled_payments: 2,
      reconciled_payments: 0,
      exception_payments: 0,
      open_exceptions: 0,
      resolved_exceptions: 0,
      unmatched_bank_lines: 0,
    };

    mockFetch([
      { method: "GET", url: "/reconciliation/status", json: () => ({ ...state }) },
      { method: "GET", url: /\/reconciliation\/exceptions/, json: () => [] },
      {
        method: "POST",
        url: "/reconciliation/bank-lines",
        json: () => {
          state.unmatched_bank_lines += 1;
          return { id: 1 };
        },
      },
      {
        method: "POST",
        url: "/reconciliation/run",
        json: () => {
          state.unmatched_bank_lines = 0;
          state.reconciled_payments = 1;
          state.unreconciled_payments = 1;
          return { lines_processed: 1, matched: 1, exceptions_created: 0 };
        },
      },
    ]);

    renderWithProviders(<ReconciliationPage />, { user: { role: "finance_officer" } });

    expect(await screen.findByTestId("st-reconciled")).toHaveTextContent("0");
    expect(screen.getByTestId("st-unmatched-lines")).toHaveTextContent("0");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/bank reference/i), "BREF-1");
    await user.type(screen.getByLabelText(/amount/i), "100");
    await user.type(screen.getByLabelText(/value date/i), "2026-03-01");
    await user.click(screen.getByRole("button", { name: /add bank line/i }));

    await waitFor(() =>
      expect(screen.getByTestId("st-unmatched-lines")).toHaveTextContent("1"),
    );

    await user.click(screen.getByRole("button", { name: /run matching/i }));

    await waitFor(() => {
      expect(screen.getByTestId("run-result")).toHaveTextContent("matched 1");
      expect(screen.getByTestId("st-reconciled")).toHaveTextContent("1");
      expect(screen.getByTestId("st-unmatched-lines")).toHaveTextContent("0");
    });
  });
});
