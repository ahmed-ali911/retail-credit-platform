import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Banknote,
  CheckCircle2,
  ClipboardList,
  Clock,
  FileText,
  HandCoins,
  Layers,
  ShieldAlert,
  TrendingUp,
  Users,
  Wallet,
} from "lucide-react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { MetricGrid, MetricTile } from "../components/MetricTile";
import { AgingBarChart, RiskBandDonut, StatusDonut } from "../components/charts";
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
          <MetricTile label="Total customers" value={s.total_customers} icon={Users} />
          <MetricTile
            label="Active contracts"
            value={s.active_contracts}
            tone="good"
            icon={FileText}
          />
          <MetricTile
            label="Outstanding receivable"
            value={money(s.total_outstanding_receivable)}
            subLabel="principal + profit, active contracts"
            icon={Wallet}
          />
          <MetricTile
            label="Profit recognized to date"
            value={money(s.total_profit_recognized)}
            tone="good"
            icon={TrendingUp}
          />
          <MetricTile
            label="Approval rate"
            value={pct(s.approval_rate)}
            subLabel={`all-time · ${s.decisions_considered} decisions`}
            icon={CheckCircle2}
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
            icon={Banknote}
          />
          <MetricTile
            label="Applications submitted today"
            value={s.applications_submitted_today}
            icon={FileText}
          />
          <MetricTile
            label="Overdue installments"
            value={s.overdue_installments}
            tone={s.overdue_installments > 0 ? "warn" : "good"}
            icon={Clock}
          />
          <MetricTile
            label="Open reconciliation exceptions"
            value={s.open_reconciliation_exceptions}
            tone={s.open_reconciliation_exceptions > 0 ? "warn" : "good"}
            icon={AlertTriangle}
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
              icon={Layers}
            />
          </MetricGrid>

          <div className="chart-row">
            <Card title="Contracts by status">
              <StatusDonut byStatus={s.contracts_by_status} />
            </Card>
            <Card title="Aging distribution (DPD)">
              <p className="muted">
                Display grouping only (config <code>dpd_report_buckets</code>) —
                not a collections-action policy. As of {s.dpd_distribution.as_of}.
              </p>
              <AgingBarChart distribution={s.dpd_distribution} />
            </Card>
          </div>

          <Card title="Aging distribution — detail" soft>
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
            icon={ClipboardList}
          />
          <MetricTile
            label="Promises kept"
            value={s.promise_to_pay_kept}
            tone="good"
            icon={CheckCircle2}
          />
          <MetricTile
            label="Promises broken"
            value={s.promise_to_pay_broken}
            tone={s.promise_to_pay_broken > 0 ? "warn" : "neutral"}
            icon={AlertTriangle}
          />
          <MetricTile
            label="Late fees charged"
            value={s.late_fees_charged_count}
            subLabel={money(s.late_fees_charged_amount)}
            icon={HandCoins}
          />
          <MetricTile
            label="Late fees waived"
            value={s.late_fees_waived_count}
            subLabel={money(s.late_fees_waived_amount)}
            icon={HandCoins}
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
              icon={CheckCircle2}
            />
            <MetricTile
              label={`Medium (${s.risk_band_thresholds.medium_min}–${s.risk_band_thresholds.low_min - 1})`}
              value={s.customers_by_risk_band.medium}
              icon={Users}
            />
            <MetricTile
              label={`High risk (< ${s.risk_band_thresholds.medium_min})`}
              value={s.customers_by_risk_band.high}
              tone={s.customers_by_risk_band.high > 0 ? "warn" : "neutral"}
              icon={ShieldAlert}
            />
            <MetricTile label="Unscored" value={s.customers_by_risk_band.unscored} icon={Users} />
            <MetricTile label="Rejection rate" value={pct(s.rejection_rate)} icon={TrendingUp} />
            <MetricTile label="Referral rate" value={pct(s.referral_rate)} icon={TrendingUp} />
          </MetricGrid>
          <Card title="Customers by risk band">
            <RiskBandDonut bands={s.customers_by_risk_band} />
          </Card>
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
  const { user } = useAuth();
  const showTabs = user != null && DASHBOARD_ROLES.includes(user.role);
  const [tab, setTab] = useState<Tab>("Executive");

  const ActiveTab = TAB_COMPONENTS[tab];

  return (
    <div className="stack dashboard-page">
      <h1>Dashboard</h1>

      {showTabs ? (
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
            Read-only snapshot — every number is a live query over existing data,
            not a forecast, and nothing here changes anything. Use the nav to
            create records, or the directories and{" "}
            <Link to="/reports">Reports</Link> for detail.
          </p>
        </Card>
      ) : (
        <Card>
          <p className="muted">
            Use the navigation to create customers, products and applications, or
            open the directories to browse existing records.
          </p>
        </Card>
      )}
    </div>
  );
}
