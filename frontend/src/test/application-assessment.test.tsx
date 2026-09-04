import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { NewApplicationPage } from "../pages/NewApplicationPage";
import { mockFetch, renderWithProviders } from "./helpers";

const baseApp = {
  id: 7,
  customer_id: 1,
  product_id: 1,
  requested_amount: 1000,
  requested_tenor_months: 12,
  channel: "branch" as const,
  created_at: "2026-01-01T00:00:00Z",
  created_by: "system",
  reference_code: "AP-000007",
  assessments: [],
};

describe("New application -> assessment screen", () => {
  it("shows the decision, DBR and every triggered-rule reason", async () => {
    mockFetch([
      {
        method: "POST",
        url: "/applications",
        json: { ...baseApp, status: "draft", latest_assessment: null },
      },
      {
        method: "POST",
        url: "/applications/7/submit",
        json: {
          ...baseApp,
          status: "referred",
          latest_assessment: {
            id: 1,
            decision: "referred",
            estimated_installment: 83.33,
            debt_burden_ratio: 0.45,
            config_snapshot: {},
            created_at: "2026-01-01T00:00:00Z",
            triggered_rules: [
              {
                rule: "debt_burden_ratio",
                outcome: "referred",
                reason: "DBR 0.4500 exceeds maximum 0.4000",
              },
              {
                rule: "risk_band",
                outcome: "referred",
                reason: "risk_score 620 in referral band [600, 650)",
              },
            ],
          },
        },
      },
    ]);

    renderWithProviders(<NewApplicationPage />, { withAuth: false });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/customer #/i), "1");
    await user.type(screen.getByLabelText(/product #/i), "1");
    await user.type(screen.getByLabelText(/requested amount/i), "1000");
    await user.click(screen.getByRole("button", { name: /create & submit/i }));

    const panel = await screen.findByTestId("assessment-panel");
    expect(panel).toBeInTheDocument();

    // decision
    expect(screen.getAllByText("referred").length).toBeGreaterThan(0);
    // DBR shown to 4dp
    expect(screen.getByTestId("dbr")).toHaveTextContent("0.4500");
    // both rules + their reasons
    expect(screen.getByTestId("rule-debt_burden_ratio")).toBeInTheDocument();
    expect(screen.getByTestId("rule-risk_band")).toBeInTheDocument();
    expect(
      screen.getByText("DBR 0.4500 exceeds maximum 0.4000"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("risk_score 620 in referral band [600, 650)"),
    ).toBeInTheDocument();
  });

  it("shows a clean pass with no triggered rules when approved", async () => {
    mockFetch([
      {
        method: "POST",
        url: "/applications",
        json: { ...baseApp, status: "draft", latest_assessment: null },
      },
      {
        method: "POST",
        url: "/applications/7/submit",
        json: {
          ...baseApp,
          status: "approved",
          latest_assessment: {
            id: 2,
            decision: "approved",
            estimated_installment: 83.33,
            debt_burden_ratio: 0.05,
            config_snapshot: {},
            created_at: "2026-01-01T00:00:00Z",
            triggered_rules: [],
          },
        },
      },
    ]);

    renderWithProviders(<NewApplicationPage />, { withAuth: false });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/customer #/i), "1");
    await user.type(screen.getByLabelText(/product #/i), "1");
    await user.type(screen.getByLabelText(/requested amount/i), "1000");
    await user.click(screen.getByRole("button", { name: /create & submit/i }));

    expect(await screen.findByTestId("no-triggered-rules")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /generate an offer/i })).toBeInTheDocument();
  });
});
