import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EclProvisionPage } from "../pages/EclProvisionPage";
import { EclAssessmentDetailPage } from "../pages/EclAssessmentDetailPage";
import { EclConfigPage } from "../pages/EclConfigPage";
import { Shell } from "../components/Shell";
import { mockFetch, renderWithProviders } from "./helpers";

const DASHBOARD = {
  active_methodology: "three_stage",
  ecl_config_version: 1,
  total_exposure: 1962,
  total_ecl: 47.1,
  total_provision: 47.1,
  provision_movement: 12.3,
  coverage_ratio: 0.024,
  contracts_assessed: 2,
  stage_exposure: { "1": 981, "2": 981, "3": 0, unstaged: 0 },
  stage_ecl: { "1": 7.85, "2": 39.25, "3": 0, unstaged: 0 },
  default_exposure: 0,
  overrides_pending: 1,
  overrides_active: 0,
  stage_migration: { upgraded: 1, downgraded: 0, unchanged: 1, new: 0 },
  last_run: {
    run_id: 3,
    run_ref: "ECL-RUN-2026-09-003",
    status: "COMPLETED",
    as_of_date: "2026-09-10",
    methodology: "three_stage",
    ecl_config_version: 1,
    contracts_assessed: 2,
    contracts_by_stage: { "1": 1, "2": 1, "3": 0, none: 0 },
    total_ead: 1962,
    total_ecl: 47.1,
    total_ecl_stage_1: 7.85,
    total_ecl_stage_2: 39.25,
    total_ecl_stage_3: 0,
    total_provision_movement: 12.3,
    accounting_event_id: null,
    posted_at: null,
    created_at: "2026-09-10T00:00:00Z",
  },
};

const CONFIG = {
  active: { ecl_config_version: 1, methodology: "three_stage", lifetime_loss_rate: 0.1 },
  versions: [
    { version: 1, is_active: true, methodology: "three_stage", notes: "seed", created_by: null, activated_at: "2026-09-01" },
  ],
};

const PORTFOLIO = {
  columns: [],
  rows: [
    {
      assessment_id: 10,
      run_id: 3,
      contract_id: 7,
      customer_id: 1,
      customer_name: "Ada L",
      product_id: 2,
      contract_status: "active",
      assessment_date: "2026-09-10",
      methodology: "three_stage",
      ecl_config_version: 1,
      ead: 981,
      dpd: 40,
      dpd_bucket: null,
      risk_rating: "B",
      risk_segment: "retail_prime",
      origination_rating: "B",
      origination_pd_12m: 0.02,
      automated_stage: 2,
      automated_pd_12m: 0.02,
      automated_pd_lifetime: 0.12,
      automated_lgd: 0.4,
      automated_ecl: 39.25,
      override_stage: null,
      override_pd_12m: null,
      override_pd_lifetime: null,
      override_lgd: null,
      override_status: null,
      final_stage: 2,
      final_pd_12m: 0.02,
      final_pd_lifetime: 0.12,
      final_lgd: 0.4,
      final_ead: 981,
      final_ecl: 39.25,
      previous_ecl: 7.85,
      opening_provision: 7.85,
      calculated_ecl: 39.25,
      override_adjustment: 0,
      closing_provision: 39.25,
      provision_movement: 31.4,
      movement_type: "increased",
      stage_reason: "SICR criteria satisfied: DPD 40 ≥ 30",
      cure_note: null,
    },
  ],
  total: 1,
  limit: 25,
  offset: 0,
  totals: { row_count: 1, final_ead: 981, final_ecl: 39.25 },
  total_ead: 981,
  total_ecl: 39.25,
};

const DETAIL = {
  ...PORTFOLIO.rows[0],
  stage_triggers: [
    { id: "dpd_sicr", name: "DPD ≥ SICR threshold", category: "dpd", triggered: true, detail: "DPD 40 ≥ threshold 30" },
    { id: "collections_case", name: "Open collections case", category: "qualitative", triggered: false, detail: "not present" },
  ],
  config_snapshot: { methodology: "three_stage", pd_term_structure: {} },
  versions: { ecl_config_version: 1, stage_rule_version: "1.0", pd_model_version: "1.0", lgd_model_version: "1.0", calculation_version: "1.0" },
  history: [
    { assessment_date: "2026-09-10", run_id: 3, methodology: "three_stage", ead: 981, dpd: 40, automated_stage: 2, final_stage: 2, automated_ecl: 39.25, final_ecl: 39.25, opening_provision: 7.85, closing_provision: 39.25, provision_movement: 31.4, movement_type: "increased", override_id: null },
  ],
  stage_migration_history: [],
  overrides: [],
  accounting_events: [],
  override_rules: { require_evidence: true },
};

describe("ECL & Provision workstation", () => {
  it("renders KPI tiles, stage cards and the portfolio table", async () => {
    mockFetch([
      { method: "GET", url: /\/ecl\/dashboard/, json: DASHBOARD },
      { method: "GET", url: /\/ecl\/config/, json: CONFIG },
      { method: "GET", url: /\/ecl\/assessments/, json: PORTFOLIO },
    ]);
    renderWithProviders(<EclProvisionPage />, { user: { role: "finance_officer" } });

    expect(await screen.findByText("Total exposure (EAD)")).toBeInTheDocument();
    expect(screen.getByText("Coverage ratio")).toBeInTheDocument();
    expect(screen.getByTestId("ecl-active-methodology")).toHaveTextContent("three_stage");
    expect(screen.getByTestId("ecl-last-run")).toHaveTextContent("ECL-RUN-2026-09-003");
    expect(screen.getByTestId("ecl-stage-card-2")).toHaveTextContent("39.25");

    const row = await screen.findByTestId("ecl-row-7");
    expect(within(row).getByText("Ada L")).toBeInTheDocument();
    expect(within(row).getByText("39.25")).toBeInTheDocument();
  });

  it("shows the n/a staging note when the active path is not three_stage", async () => {
    mockFetch([
      { method: "GET", url: /\/ecl\/dashboard/, json: { ...DASHBOARD, active_methodology: "simplified_lifetime" } },
      { method: "GET", url: /\/ecl\/config/, json: CONFIG },
      { method: "GET", url: /\/ecl\/assessments/, json: PORTFOLIO },
    ]);
    renderWithProviders(<EclProvisionPage />, { user: { role: "credit_manager" } });
    expect(await screen.findByTestId("ecl-stage-na")).toBeInTheDocument();
  });
});

describe("ECL assessment detail — automated / override / final + explainability", () => {
  it("shows the triggered-rules checklist, the 'why' line and the result matrix", async () => {
    mockFetch([{ method: "GET", url: /\/ecl\/contracts\/7/, json: DETAIL }]);
    renderWithProviders(<EclAssessmentDetailPage />, {
      user: { role: "finance_officer" },
      path: "/ecl/contracts/7",
      routePath: "/ecl/contracts/:contractId",
    });

    expect(await screen.findByTestId("ecl-why")).toHaveTextContent("Automated Stage 2");
    expect(screen.getByTestId("trigger-dpd_sicr")).toHaveTextContent("yes");
    expect(screen.getByTestId("trigger-collections_case")).toHaveTextContent("no");

    const matrix = screen.getByTestId("ecl-result-matrix");
    expect(within(matrix).getByText("Automated")).toBeInTheDocument();
    expect(within(matrix).getByText("Final")).toBeInTheDocument();
  });

  it("lets an authorised user request a stage override (maker-checker)", async () => {
    const fetchMock = mockFetch([
      { method: "GET", url: /\/ecl\/contracts\/7/, json: DETAIL },
      { method: "POST", url: /request-stage-override/, json: { id: 55 } },
    ]);
    renderWithProviders(<EclAssessmentDetailPage />, {
      user: { role: "credit_manager" },
      path: "/ecl/contracts/7",
      routePath: "/ecl/contracts/:contractId",
    });

    const form = await screen.findByTestId("stage-override-form");
    await userEvent.type(within(form).getByLabelText(/Justification/), "Borrower disclosed job loss");
    await userEvent.type(within(form).getByLabelText(/Evidence/), "DOC-9");
    await userEvent.click(within(form).getByRole("button", { name: /Request stage override/ }));

    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("request-stage-override"));
    expect(call).toBeTruthy();
    expect(JSON.parse((call![1] as RequestInit).body as string)).toMatchObject({
      stage: 2,
      reason_code: "MODEL_LIMITATION",
      evidence_ref: "DOC-9",
    });
  });

  it("hides the override forms from a read-only role", async () => {
    mockFetch([{ method: "GET", url: /\/ecl\/contracts\/7/, json: DETAIL }]);
    renderWithProviders(<EclAssessmentDetailPage />, {
      user: { role: "credit_officer" },
      path: "/ecl/contracts/7",
      routePath: "/ecl/contracts/:contractId",
    });
    await screen.findByTestId("ecl-why");
    expect(screen.queryByTestId("stage-override-form")).not.toBeInTheDocument();
  });
});

describe("ECL configuration — versioned, maker-checker", () => {
  it("lists versions and proposes a change", async () => {
    const fetchMock = mockFetch([
      { method: "GET", url: /\/ecl\/config/, json: CONFIG },
      { method: "PUT", url: /\/ecl\/config/, json: { id: 88 } },
    ]);
    renderWithProviders(<EclConfigPage />, { user: { role: "finance_officer" } });

    expect(await screen.findByTestId("ecl-config-version-1")).toHaveTextContent("active");
    await userEvent.type(screen.getByTestId("ecl-config-changes"), '{{"lifetime_loss_rate": 0.15}');
    await userEvent.click(screen.getByRole("button", { name: /Propose change/ }));

    const call = fetchMock.mock.calls.find(
      (c) => String(c[0]).includes("/ecl/config") && (c[1] as RequestInit)?.method === "PUT",
    );
    expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
      changes: { lifetime_loss_rate: 0.15 },
    });
  });
});

describe("ECL nav visibility", () => {
  function navFor(role: string) {
    renderWithProviders(<Shell />, { user: { role } });
    return screen.getByRole("navigation");
  }
  it("a finance_officer sees ECL & Provision and ECL Model & Rules", () => {
    const nav = navFor("finance_officer");
    expect(nav).toHaveTextContent("ECL & Provision");
    expect(nav).toHaveTextContent("ECL Model & Rules");
  });
  it("a sales_employee sees neither", () => {
    const nav = navFor("sales_employee");
    expect(nav).not.toHaveTextContent("ECL & Provision");
    expect(nav).not.toHaveTextContent("ECL Model & Rules");
  });
  it("a collections_officer sees neither", () => {
    expect(navFor("collections_officer")).not.toHaveTextContent("ECL & Provision");
  });
});
