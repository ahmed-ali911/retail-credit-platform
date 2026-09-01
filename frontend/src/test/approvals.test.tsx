import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApprovalsPage } from "../pages/ApprovalsPage";
import { mockFetch, renderWithProviders } from "./helpers";

const rows = [
  {
    id: 1,
    action_type: "config.update",
    entity_type: "config_parameter",
    entity_id: "late_fee_rate",
    requested_by: 42, // the current user
    requested_at: "2026-03-01T00:00:00Z",
    payload: { new_value: 0.03 },
    status: "pending",
    decided_by: null,
    decided_at: null,
    decision_notes: null,
  },
  {
    id: 2,
    action_type: "late_fee.waive",
    entity_type: "late_fee_charge",
    entity_id: "8",
    requested_by: 99, // someone else
    requested_at: "2026-03-02T00:00:00Z",
    payload: { reason: "goodwill" },
    status: "pending",
    decided_by: null,
    decided_at: null,
    decision_notes: null,
  },
];

describe("Approvals screen", () => {
  it("disables decide buttons on the current user's own request, enables others'", async () => {
    mockFetch([{ method: "GET", url: /\/approvals/, json: rows }]);

    renderWithProviders(<ApprovalsPage />, {
      user: { id: 42, role: "credit_manager" },
    });

    // own request (id 1) — no buttons, explanation shown
    expect(await screen.findByTestId("approval-blocked-1")).toHaveTextContent(
      /different approver is required/i,
    );
    expect(screen.queryByTestId("approve-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reject-1")).not.toBeInTheDocument();

    // someone else's request (id 2) — buttons present and enabled
    expect(screen.getByTestId("approve-2")).toBeEnabled();
    expect(screen.getByTestId("reject-2")).toBeEnabled();
  });
});
