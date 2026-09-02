import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, downloadFile, errorMessage } from "../api/client";
import type {
  ContractReportPage,
  ProfitabilityReport,
} from "../api/types";
import { Card, ErrorNote, Field, money } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

const CATEGORIES = [
  "Contracts",
  "Profitability",
  "Customers",
  "Products",
  "Collections",
] as const;
type Category = (typeof CATEGORIES)[number];

function qs(params: Record<string, string>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  return p.toString();
}

function ContractsReport() {
  const [filters, setFilters] = useState({
    status: "",
    customer_id: "",
    product_id: "",
    date_from: "",
    date_to: "",
  });
  const [page, setPage] = useState<ContractReportPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const query = () =>
    qs({
      status: filters.status,
      customer_id: filters.customer_id,
      product_id: filters.product_id,
      date_from: filters.date_from,
      date_to: filters.date_to,
    });

  async function run(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      setPage(await api<ContractReportPage>(`/reports/contracts?${query()}`));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const set = (k: keyof typeof filters) => (e: { target: { value: string } }) =>
    setFilters((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="stack">
      <Card title="Contracts report">
        <form className="stack" onSubmit={run}>
          <div className="field-row">
            <label className="field">
              <span>Status</span>
              <select value={filters.status} onChange={set("status")}>
                <option value="">Any</option>
                <option value="created">created</option>
                <option value="active">active</option>
                <option value="closed">closed</option>
              </select>
            </label>
            <Field label="Customer #" inputMode="numeric" value={filters.customer_id} onChange={set("customer_id")} />
            <Field label="Product #" inputMode="numeric" value={filters.product_id} onChange={set("product_id")} />
          </div>
          <div className="field-row">
            <Field label="Created from" type="date" value={filters.date_from} onChange={set("date_from")} />
            <Field label="Created to" type="date" value={filters.date_to} onChange={set("date_to")} />
          </div>
          <div className="inline-form">
            <button className="btn-primary" type="submit" disabled={busy}>
              Run report
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                downloadFile(
                  `/reports/contracts?${qs({ ...filters, format: "csv" })}`,
                  "contracts.csv",
                ).catch((err) => setError(errorMessage(err)))
              }
            >
              Export CSV
            </button>
          </div>
        </form>
      </Card>

      <ErrorNote message={error} />

      {page && (
        <Card>
          <p className="muted">
            {page.total} contract(s) — showing {page.items.length}
          </p>
          <table className="data" aria-label="Contracts report results">
            <thead>
              <tr>
                <th>#</th>
                <th>Status</th>
                <th>Customer</th>
                <th>Product</th>
                <th>Category</th>
                <th className="num">Tenor</th>
                <th className="num">Sale price</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((r) => (
                <tr key={r.contract_id} data-testid={`contract-report-row-${r.contract_id}`}>
                  <td>
                    <Link to={`/contracts/${r.contract_id}`}>{r.contract_id}</Link>
                  </td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td>{r.customer_name}</td>
                  <td>{r.product_name}</td>
                  <td>{r.category}</td>
                  <td className="num">{r.tenor_months}</td>
                  <td className="num">{money(r.installment_sale_price)}</td>
                  <td>{new Date(r.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function ProfitabilityReportView() {
  const [filters, setFilters] = useState({ date_from: "", date_to: "", product_id: "" });
  const [report, setReport] = useState<ProfitabilityReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof filters) => (e: { target: { value: string } }) =>
    setFilters((f) => ({ ...f, [k]: e.target.value }));

  async function run(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      setReport(
        await api<ProfitabilityReport>(
          `/reports/profitability?${qs(filters)}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <Card title="Profitability report">
        <form className="stack" onSubmit={run}>
          <div className="field-row">
            <Field label="Created from" type="date" value={filters.date_from} onChange={set("date_from")} />
            <Field label="Created to" type="date" value={filters.date_to} onChange={set("date_to")} />
            <Field label="Product #" inputMode="numeric" value={filters.product_id} onChange={set("product_id")} />
          </div>
          <button className="btn-primary" type="submit" disabled={busy}>
            Run report
          </button>
        </form>
      </Card>

      <ErrorNote message={error} />

      {report && (
        <>
          <Card soft>
            <dl className="kv">
              <dt>Contracts counted</dt>
              <dd>{report.contracts_counted}</dd>
              <dt>Total contractual profit</dt>
              <dd data-testid="prof-contractual">{money(report.total_contractual_profit)}</dd>
              <dt>Recognised profit</dt>
              <dd data-testid="prof-recognized">{money(report.total_recognized_profit)}</dd>
              <dt>Unearned profit</dt>
              <dd data-testid="prof-unearned">{money(report.total_unearned_profit)}</dd>
            </dl>
          </Card>
          <Card title="By tenor">
            <ProfTable data={report.by_tenor} keyLabel="Tenor (months)" />
          </Card>
          <Card title="By product category">
            <ProfTable data={report.by_category} keyLabel="Category" />
          </Card>
        </>
      )}
    </div>
  );
}

function ProfTable({
  data,
  keyLabel,
}: {
  data: ProfitabilityReport["by_tenor"];
  keyLabel: string;
}) {
  const rows = Object.entries(data);
  if (rows.length === 0) return <p className="muted">No data.</p>;
  return (
    <table className="data">
      <thead>
        <tr>
          <th>{keyLabel}</th>
          <th className="num">Contracts</th>
          <th className="num">Contractual</th>
          <th className="num">Recognised</th>
          <th className="num">Unearned</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td>{k}</td>
            <td className="num">{v.contracts}</td>
            <td className="num">{money(v.contractual_profit)}</td>
            <td className="num">{money(v.recognized_profit)}</td>
            <td className="num">{money(v.unearned_profit)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ExistingScreenLink({
  category,
}: {
  category: "Customers" | "Products" | "Collections";
}) {
  const map = {
    Customers: { to: "/customers", label: "Customer Directory" },
    Products: { to: "/products", label: "Product Directory" },
    Collections: { to: "/collections", label: "Collections case list" },
  } as const;
  const { to, label } = map[category];
  return (
    <Card title={category}>
      <p className="muted">
        This data already has a screen — no duplicate is built here. The{" "}
        <strong>Export CSV</strong> button lives on that screen.
      </p>
      <Link className="btn-link" to={to} data-testid={`reports-link-${category}`}>
        Go to {label} →
      </Link>
    </Card>
  );
}

export function ReportsPage() {
  const [category, setCategory] = useState<Category>("Contracts");

  return (
    <div className="stack">
      <h1>Reports</h1>
      <p className="muted">
        Bounded reporting — every figure is a live query over existing tables.
        No charts, no scheduled/emailed reports, CSV export only.
      </p>
      <div className="split">
        <div className="split__nav" role="tablist" aria-label="Report categories">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              role="tab"
              aria-selected={category === c}
              className={category === c ? "active" : undefined}
              onClick={() => setCategory(c)}
            >
              {c}
            </button>
          ))}
        </div>
        <div className="split__body" data-testid={`report-${category}`}>
          {category === "Contracts" && <ContractsReport />}
          {category === "Profitability" && <ProfitabilityReportView />}
          {category === "Customers" && <ExistingScreenLink category="Customers" />}
          {category === "Products" && <ExistingScreenLink category="Products" />}
          {category === "Collections" && <ExistingScreenLink category="Collections" />}
        </div>
      </div>
    </div>
  );
}
