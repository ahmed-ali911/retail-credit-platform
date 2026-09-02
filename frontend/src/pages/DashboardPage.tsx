import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { MetricGrid, MetricTile } from "../components/MetricTile";
import { Card, ErrorNote, money } from "../components/ui";
import type {
  CollectionsSummary,
  CreditRiskSummary,
  ExecutiveSummary,
  OperationsSummary,
  PortfolioSummary,
} from "../api/types";

const DASHBOARD_ROLES = ["finance_officer", "credit_manager", "admin"];
const TABS = [
  "Executive",
  "Operations",
  "Portfolio",
  "Collections",
  "Credit & Risk",
] as const;
type Tab = (typeof TABS)[number];

function pct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function useSummary<T>(path: string, enabled: boolean) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    setData(null);
    setError(null);
    void api<T>(path)
      .then(setData)
      .catch((e) => setError(errorMessage(e)));
  }, [path, enabled]);
  return { data, error };
}

function ExecutiveTab() {
  const { data, error } = useSummary<ExecutiveSummary>(
    "/reports/summary/executive",
    true,
  );
  return (
    <TabBody error={error} data={data}>
      {(s) => (
        <MetricGrid>
          <MetricTile label="Total customers" value={s.total_customers} />
          <MetricTile
            label="Active contracts"
            value={s.active_contracts}
            tone="good"
          />
          <MetricTile
            label="Outstanding receivable"
            value={money(s.total_outstanding_receivable)}
            subLabel="principal + profit, active contracts"
          />
          <MetricTile
            label="Profit recognised to date"
            value={money(s.total_profit_recognized)}
            tone="good"
          />
          <MetricTile
            label="Approval rate"
            value={pct(s.approval_rate)}
            subLabel={`all-time · ${s.decisions_considered} decisions`}
          />
        </MetricGrid>
      )}
    </TabBody>
  );
}

function OperationsTab() {
  const { data, error } = useSummary<OperationsSummary>(
    "/reports/summary/operations",
    true,
  );
  return (
    <TabBody error={error} data={data}>
      {(s) => (
        <MetricGrid>
          <MetricTile
            label="Payments today"
            value={s.payments_today_count}
            subLabel={money(s.payments_today_amount)}
          />
          <MetricTile
            label="Applications submitted today"
            value={s.applications_submitted_today}
          />
          <MetricTile
            label="Overdue installments"
            value={s.overdue_installments}
            tone={s.overdue_installments > 0 ? "warn" : "good"}
          />
          <MetricTile
            label="Open reconciliation exceptions"
            value={s.open_reconciliation_exceptions}
            tone={s.open_reconciliation_exceptions > 0 ? "warn" : "good"}
          />
        </MetricGrid>
      )}
    </TabBody>
  );
}

function PortfolioTab() {
  const { data, error } = useSummary<PortfolioSummary>(
    "/reports/summary/portfolio",
    true,
  );
  return (
    <TabBody error={error} data={data}>
      {(s) => (
        <div className="stack">
          <MetricGrid>
            {Object.entries(s.contracts_by_status).map(([k, v]) => (
              <MetricTile key={k} label={`Contracts — ${k}`} value={v} />
            ))}
            <MetricTile
              label="Average contract size"
              value={money(s.average_contract_size)}
              subLabel="installment sale price"
            />
          </MetricGrid>
          <Card title="Aging distribution (DPD)" soft>
            <p className="muted">
              Display grouping only (config `dpd_report_buckets`) — not a
              collections-action policy. As of {s.dpd_distribution.as_of}.
            </p>
            <table className="data" aria-label="DPD distribution">
              <thead>
                <tr>
                  <th>Bucket</th>
                  <th className="num">Contracts</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Current</td>
                  <td className="num">{s.dpd_distribution.current}</td>
                </tr>
                {Object.entries(s.dpd_distribution.buckets).map(([k, v]) => (
                  <tr key={k} data-testid={`dpd-${k}`}>
                    <td>{k}</td>
                    <td className="num">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}
    </TabBody>
  );
}

function CollectionsTab() {
  const { data, error } = useSummary<CollectionsSummary>(
    "/reports/summary/collections",
    true,
  );
  return (
    <TabBody error={error} data={data}>
      {(s) => (
        <MetricGrid>
          <MetricTile
            label="Open collection cases"
            value={s.open_cases}
            tone={s.open_cases > 0 ? "warn" : "good"}
          />
          <MetricTile label="Promises kept" value={s.promise_to_pay_kept} tone="good" />
          <MetricTile
            label="Promises broken"
            value={s.promise_to_pay_broken}
            tone={s.promise_to_pay_broken > 0 ? "warn" : "neutral"}
          />
          <MetricTile
            label="Late fees charged"
            value={s.late_fees_charged_count}
            subLabel={money(s.late_fees_charged_amount)}
          />
          <MetricTile
            label="Late fees waived"
            value={s.late_fees_waived_count}
            subLabel={money(s.late_fees_waived_amount)}
          />
        </MetricGrid>
      )}
    </TabBody>
  );
}

function CreditRiskTab() {
  const { data, error } = useSummary<CreditRiskSummary>(
    "/reports/summary/credit-risk",
    true,
  );
  return (
    <TabBody error={error} data={data}>
      {(s) => (
        <div className="stack">
          <MetricGrid>
            <MetricTile
              label={`Low risk (≥ ${s.risk_band_thresholds.low_min})`}
              value={s.customers_by_risk_band.low}
              tone="good"
            />
            <MetricTile
              label={`Medium (${s.risk_band_thresholds.medium_min}–${s.risk_band_thresholds.low_min - 1})`}
              value={s.customers_by_risk_band.medium}
            />
            <MetricTile
              label={`High risk (< ${s.risk_band_thresholds.medium_min})`}
              value={s.customers_by_risk_band.high}
              tone={s.customers_by_risk_band.high > 0 ? "warn" : "neutral"}
            />
            <MetricTile label="Unscored" value={s.customers_by_risk_band.unscored} />
            <MetricTile label="Rejection rate" value={pct(s.rejection_rate)} />
            <MetricTile label="Referral rate" value={pct(s.referral_rate)} />
          </MetricGrid>
          <Card title="Top 10 customers by exposure" soft>
            {s.top_customers_by_exposure.length === 0 ? (
              <p className="muted">No customers with outstanding exposure.</p>
            ) : (
              <table className="data" aria-label="Top customers by exposure">
                <thead>
                  <tr>
                    <th>Customer</th>
                    <th className="num">Outstanding</th>
                  </tr>
                </thead>
                <tbody>
                  {s.top_customers_by_exposure.map((c) => (
                    <tr key={c.customer_id} data-testid={`top-exp-${c.customer_id}`}>
                      <td>
                        <Link to={`/customers/${c.customer_id}`}>{c.name}</Link>
                      </td>
                      <td className="num">{money(c.total_outstanding)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      )}
    </TabBody>
  );
}

function TabBody<T>({
  error,
  data,
  children,
}: {
  error: string | null;
  data: T | null;
  children: (d: T) => ReactNode;
}) {
  if (error) return <ErrorNote message={error} />;
  if (!data) return <p className="muted">Loading…</p>;
  return <>{children(data)}</>;
}

const TAB_COMPONENTS: Record<Tab, () => ReactNode> = {
  Executive: ExecutiveTab,
  Operations: OperationsTab,
  Portfolio: PortfolioTab,
  Collections: CollectionsTab,
  "Credit & Risk": CreditRiskTab,
};

export function DashboardPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const showTabs = user != null && DASHBOARD_ROLES.includes(user.role);
  const [tab, setTab] = useState<Tab>("Executive");
  const [customerId, setCustomerId] = useState("");
  const [appId, setAppId] = useState("");
  const [offerId, setOfferId] = useState("");
  const [contractId, setContractId] = useState("");

  const ActiveTab = TAB_COMPONENTS[tab];

  return (
    <div className="stack">
      <h1>Dashboard</h1>

      {showTabs && (
        <Card>
          <div className="tabs" role="tablist">
            {TABS.map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                className={tab === t ? "active" : undefined}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </div>
          <div style={{ marginTop: "1rem" }} data-testid={`tab-${tab}`}>
            <ActiveTab />
          </div>
          <p className="muted" style={{ marginTop: "1rem" }}>
            Snapshot figures — every number is a live query over existing data,
            not a forecast. For downloadable detail see{" "}
            <Link to="/reports">Reports</Link>.
          </p>
        </Card>
      )}

      <Card title="Start a new flow">
        <p className="muted">
          Create the records you need, then run an application through assessment,
          turn an approval into an offer, and accept it into a contract.
        </p>
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <button className="btn-primary" onClick={() => navigate("/customers/new")}>
            Create customer
          </button>{" "}
          <button className="btn-secondary" onClick={() => navigate("/products/new")}>
            Create product
          </button>{" "}
          <button className="btn-secondary" onClick={() => navigate("/applications/new")}>
            New application
          </button>
        </div>
      </Card>

      <Card title="Open an existing record" soft>
        <p className="muted">Open records by id (or use the directories in the nav).</p>
        <div className="inline-form">
          <label className="field">
            <span>Customer #</span>
            <input
              inputMode="numeric"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary"
            disabled={!customerId}
            onClick={() => navigate(`/customers/${customerId}`)}
          >
            Open customer
          </button>
        </div>
        <div className="inline-form" style={{ marginTop: "0.75rem" }}>
          <label className="field">
            <span>Application #</span>
            <input
              inputMode="numeric"
              value={appId}
              onChange={(e) => setAppId(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary"
            disabled={!appId}
            onClick={() => navigate(`/applications/${appId}/offer`)}
          >
            Open → offer
          </button>
        </div>
        <div className="inline-form" style={{ marginTop: "0.75rem" }}>
          <label className="field">
            <span>Offer #</span>
            <input
              inputMode="numeric"
              value={offerId}
              onChange={(e) => setOfferId(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary"
            disabled={!offerId}
            onClick={() => navigate(`/offers/${offerId}`)}
          >
            Open offer
          </button>
        </div>
        <div className="inline-form" style={{ marginTop: "0.75rem" }}>
          <label className="field">
            <span>Contract #</span>
            <input
              inputMode="numeric"
              value={contractId}
              onChange={(e) => setContractId(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary"
            disabled={!contractId}
            onClick={() => navigate(`/contracts/${contractId}`)}
          >
            Open contract
          </button>
        </div>
      </Card>
    </div>
  );
}
