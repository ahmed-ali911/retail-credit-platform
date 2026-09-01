import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ReviewQueuePage, ReviewApplicationPage } from "../pages/ReviewQueuePage";
import { mockFetch, renderWithProviders } from "./helpers";

describe("Review Queue", () => {
  it("lists referred applications from the status-filtered endpoint", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: "/applications?status=referred",
        json: [
          {
            id: 11,
            customer_id: 3,
            product_id: 2,
            requested_amount: 1200,
            status: "referred",
            submitted_at: "2026-02-01T00:00:00Z",
          },
          {
            id: 12,
            customer_id: 4,
            product_id: 2,
            requested_amount: 800,
            status: "referred",
            submitted_at: "2026-02-02T00:00:00Z",
          },
        ],
      },
    ]);

    renderWithProviders(<ReviewQueuePage />, { user: { role: "credit_officer" } });

    expect(await screen.findByTestId("review-row-11")).toBeInTheDocument();
    expect(screen.getByTestId("review-row-12")).toBeInTheDocument();
    const call = fetchMock.mock.calls[0][0] as string;
    expect(call).toContain("/applications?status=referred");
  });

  it("submits the review decision with the right payload and links to the offer", async () => {
    const fetchMock = mockFetch([
      {
        method: "GET",
        url: "/applications/7",
        json: {
          id: 7,
          customer_id: 1,
          product_id: 1,
          requested_amount: 1000,
          requested_tenor_months: 12,
          channel: "branch",
          status: "referred",
          created_at: "2026-01-01T00:00:00Z",
          created_by: "system",
          latest_assessment: null,
          assessments: [
            {
              id: 1,
              decision: "referred",
              source: "automated",
              estimated_installment: 90,
              debt_burden_ratio: 0.42,
              triggered_rules: [
                { rule: "debt_burden_ratio", outcome: "referred", reason: "DBR high" },
              ],
              config_snapshot: {},
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        },
      },
      {
        method: "POST",
        url: "/applications/7/review",
        json: {
          id: 7,
          customer_id: 1,
          product_id: 1,
          requested_amount: 1000,
          requested_tenor_months: 12,
          channel: "branch",
          status: "approved",
          created_at: "2026-01-01T00:00:00Z",
          created_by: "system",
          latest_assessment: null,
          assessments: [],
        },
      },
    ]);

    renderWithProviders(<ReviewApplicationPage />, {
      user: { role: "credit_officer" },
      path: "/review/7",
      routePath: "/review/:applicationId",
    });

    // the automated assessment is shown
    expect(await screen.findByTestId("rule-debt_burden_ratio")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText(/decision/i), "approved");
    await user.type(screen.getByLabelText(/reason/i), "Verified income by payslip");
    await user.click(screen.getByRole("button", { name: /submit decision/i }));

    await waitFor(() => {
      expect(screen.getByRole("link", { name: /generate an offer/i })).toBeInTheDocument();
    });

    const reviewCall = fetchMock.mock.calls.find(
      (c) => String(c[0]).includes("/applications/7/review"),
    );
    expect(reviewCall).toBeTruthy();
    expect(JSON.parse((reviewCall![1] as RequestInit).body as string)).toEqual({
      decision: "approved",
      reason: "Verified income by payslip",
    });
  });
});
