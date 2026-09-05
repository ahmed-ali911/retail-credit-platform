import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EclProvisionPage } from "../pages/EclProvisionPage";
import { Shell } from "../components/Shell";
import { mockFetch, renderWithProviders } from "./helpers";

const DASHBOARD_DPD_BANDED = {
  active_methodology: "dpd_banded",
  methodology_note: "Path C — provision % per DPD band. Fully computed.",
  total_ead: 1962,
  ecl_balance: 9.81,
  provision_balance: 9.81,
  ecl_coverage_pct: 0.005,
  ecl_not_computable_count: 0,
  pd_lgd_note: null,
  contracts_assessed: 2,
  stage_exposure: "n/a",
  last_run: {
    run_id: 3,
    as_of_date: "2026-09-06",
    methodology: "dpd_banded",
    contracts_assessed: 2,
    total_ead: 1962,
    total_ecl: 9.81,
    total_provision_movement: 9.81,
    accounting_event_id: 12,
    created_at: "2026-09-06T00:00:00Z",
  },
};

const PORTFOLIO_DPD_BANDED = {
  columns: ["contract_id", "customer_name", "ead", "dpd", "ecl_amount"],
  rows: [
    {
      contract_id: 7,
      customer_id: 1,
      customer_name: "Ada L",
      product_id: 2,
      contract_status: "active",
      risk_band: "low",
      assessment_date: "2026-09-06",
      methodology: "dpd_banded",
      ead: 981,
      dpd: 0,
      dpd_bucket: "current",
      stage: null,
      stage_reason: null,
      pd: null,
      lgd: null,
      pd_lgd_note: null,
      loss_rate: 0.005,
      ecl_amount: 4.91,
      ecl_note: null,
      provision_before: 0,
      provision_movement: 4.91,
    },
  ],
  totals: { row_count: 1, ead: 981, ecl_amount: 4.91, ecl_not_computable_count: 0 },
  total_ead: 981,
  total_ecl: 4.91,
  ecl_not_computable_count: 0,
};

describe("ECL & Provision screen", () => {
  it("renders day-one tiles, the run panel and the portfolio table", async () => {
    mockFetch([
      { method: "GET", url: /\/ecl\/dashboard/, json: DASHBOARD_DPD_BANDED },
      { method: "GET", url: /\/ecl\/assessments/, json: PORTFOLIO_DPD_BANDED },
    ]);

    renderWithProviders(<EclProvisionPage />, { user: { role: "finance_officer" } });

    // tiles
    expect(await screen.findByText("Total EAD")).toBeInTheDocument();
    expect(screen.getByText("ECL coverage %")).toBeInTheDocument();
    expect(screen.getByTestId("ecl-active-methodology")).toHaveTextContent("dpd_banded");
    expect(screen.getByTestId("ecl-last-run")).toHaveTextContent("2026-09-06");

    // stage split is n/a because the active path isn't three_stage
    expect(screen.getByTestId("ecl-stage-na")).toBeInTheDocument();

    // portfolio row with a real, non-zero ECL for a current (0 DPD) contract
    const row = await screen.findByTestId("ecl-row-7");
    expect(within(row).getByText("4.91")).toBeInTheDocument();
    expect(within(row).getByText("Ada L")).toBeInTheDocument();
  });

  it("shows PD / LGD / ECL as 'n/a' — never 0 — under the three_stage path", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/ecl\/dashboard/,
        json: {
          ...DASHBOARD_DPD_BANDED,
          active_methodology: "three_stage",
          ecl_balance: 0,
          provision_balance: 0,
          ecl_coverage_pct: null,
          ecl_not_computable_count: 1,
          pd_lgd_note: "n/a — no PD/LGD source configured",
          stage_exposure: { "1": 981, "2": 0, "3": 0, unstaged: 0 },
        },
      },
      {
        method: "GET",
        url: /\/ecl\/assessments/,
        json: {
          ...PORTFOLIO_DPD_BANDED,
          rows: [
            {
              ...PORTFOLIO_DPD_BANDED.rows[0],
              methodology: "three_stage",
              dpd_bucket: null,
              stage: 1,
              loss_rate: null,
              pd: null,
              lgd: null,
              pd_lgd_note: "n/a — no PD/LGD source configured",
              ecl_amount: null,
              ecl_note: "n/a — no PD/LGD source configured",
            },
          ],
          total_ecl: null,
          ecl_not_computable_count: 1,
        },
      },
    ]);

    renderWithProviders(<EclProvisionPage />, { user: { role: "credit_manager" } });

    const row = await screen.findByTestId("ecl-row-7");
    // the ECL cell shows the note, not "0" / "0.00"
    expect(within(row).getAllByText(/no PD\/LGD source configured/).length).toBeGreaterThan(0);
    expect(within(row).queryByText("0.00")).not.toBeInTheDocument();
    expect(within(row).getByText("Stage 1")).toBeInTheDocument();

    // stage exposure table IS shown (staging is live under Path B)
    expect(screen.getByTestId("ecl-stage-1")).toHaveTextContent("981");
  });
});

describe("ECL nav visibility", () => {
  function navFor(role: string) {
    renderWithProviders(<Shell />, { user: { role } });
    return screen.getByRole("navigation");
  }

  it("a finance_officer sees ECL & Provision", () => {
    expect(navFor("finance_officer")).toHaveTextContent("ECL & Provision");
  });

  it("a credit_manager sees ECL & Provision", () => {
    expect(navFor("credit_manager")).toHaveTextContent("ECL & Provision");
  });

  it("a sales_employee does not see ECL & Provision", () => {
    expect(navFor("sales_employee")).not.toHaveTextContent("ECL & Provision");
  });

  it("a collections_officer does not see ECL & Provision", () => {
    expect(navFor("collections_officer")).not.toHaveTextContent("ECL & Provision");
  });
});
