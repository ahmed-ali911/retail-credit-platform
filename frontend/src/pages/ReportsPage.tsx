import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import { Link } from "react-router-dom";
import {
  ClipboardList,
  Clock,
  FileText,
  Package,
  TrendingUp,
  Users,
  type LucideIcon,
} from "lucide-react";
import { api, downloadFile, errorMessage } from "../api/client";
import type { ContractReportPage, ProfitabilityReport } from "../api/types";
import { Card, ErrorNote, Field, money } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

function qs(params: Record<string, string>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  return p.toString();
}

// --------------------------------------------------------------------------- //
// export button group (CSV / Excel / PDF) — one component, every report screen
// --------------------------------------------------------------------------- //
function ExportGroup({
  path,
  base,
  onError,
}: {
  path: string; // endpoint path incl. any query, WITHOUT the format param
  base: string; // download filename base
  onError: (m: string) => void;
}) {
  const go = (fmt: "csv" | "xlsx" | "pdf", ext: string) => {
    const sep = path.includes("?") ? "&" : "?";
    downloadFile(`${path}${sep}format=${fmt}`, `${base}.${ext}`).catch((err) =>
      onError(errorMessage(err)),
    );
  };
  return (
    <span className="export-group">
      <span>Export</span>
      <button type="button" className="btn-secondary" onClick={() => go("csv", "csv")}>
        Export CSV
      </button>
      <button type="button" className="btn-secondary" onClick={() => go("xlsx", "xlsx")}>
        Export Excel
      </button>
      <button type="button" className="btn-secondary" onClick={() => go("pdf", "pdf")}>
        Export PDF
      </button>
    </span>
  );
}

// --------------------------------------------------------------------------- //
// generic table report — drives every by-X / summary sub-report
// --------------------------------------------------------------------------- //
interface GenericReport {
  columns: string[];
  rows: Record<string, unknown>[];
  [k: string]: unknown;
}

function humanCol(c: string): string {
  return c.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

function GenericTableReport({
  endpoint,
  base,
  summaryKeys,
}: {
  endpoint: string;
  base: string;
  summaryKeys?: string[];
}) {
  const [data, setData] = useState<GenericReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      setData(await api<GenericReport>(endpoint));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [endpoint]);

  useEffect(() => {
    void run();
  }, [run]);

  return (
    <div className="stack">
      <div className="inline-form">
        <button className="btn-primary" onClick={() => void run()} disabled={busy}>
          Run report
        </button>
        <ExportGroup path={endpoint} base={base} onError={setError} />
      </div>
      <ErrorNote message={error} />
      {data && (
        <Card>
          {summaryKeys && summaryKeys.some((k) => k in data) && (
            <p className="muted" data-testid="report-summary">
              {summaryKeys
                .filter((k) => k in data)
                .map((k) => `${humanCol(k)}: ${String(data[k])}`)
                .join("  ·  ")}
            </p>
          )}
          <table className="data" aria-label="Report results">
            <thead>
              <tr>
                {data.columns.map((c) => (
                  <th key={c}>{humanCol(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.length === 0 ? (
                <tr>
                  <td colSpan={data.columns.length} className="muted">
                    No rows.
                  </td>
                </tr>
              ) : (
                data.rows.map((row, i) => (
                  <tr key={i} data-testid={`report-row-${i}`}>
                    {data.columns.map((c) => (
                      <td key={c}>{String(row[c] ?? "")}</td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Contracts — the Step 11 filterable list (kept)
// --------------------------------------------------------------------------- //
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
      <Card title="All contracts">
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
            <ExportGroup
              path={`/reports/contracts?${query()}`}
              base="contracts"
              onError={setError}
            />
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

// --------------------------------------------------------------------------- //
// Profitability — the Step 11 view (kept)
// --------------------------------------------------------------------------- //
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
      setReport(await api<ProfitabilityReport>(`/reports/profitability?${qs(filters)}`));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <Card title="Profitability — portfolio summary">
        <form className="stack" onSubmit={run}>
          <div className="field-row">
            <Field label="Created from" type="date" value={filters.date_from} onChange={set("date_from")} />
            <Field label="Created to" type="date" value={filters.date_to} onChange={set("date_to")} />
            <Field label="Product #" inputMode="numeric" value={filters.product_id} onChange={set("product_id")} />
          </div>
          <div className="inline-form">
            <button className="btn-primary" type="submit" disabled={busy}>
              Run report
            </button>
            <ExportGroup
              path={`/reports/profitability?${qs(filters)}`}
              base="profitability"
              onError={setError}
            />
          </div>
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
              <dt>Recognized profit</dt>
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
          <th className="num">Recognized</th>
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

// --------------------------------------------------------------------------- //
// Aging — bucket summary + drill-down (NEW, Step 13)
// --------------------------------------------------------------------------- //
interface AgingRow {
  bucket: number;
  label: string;
  installment_count: number;
  outstanding_amount: number;
}
interface AgingDetailRow {
  contract_id: number;
  customer_id: number;
  customer_name: string;
  installment_id: number;
  sequence_number: number;
  due_date: string;
  dpd: number;
  outstanding_amount: number;
}

function AgingReport() {
  const [rows, setRows] = useState<AgingRow[] | null>(null);
  const [asOf, setAsOf] = useState("");
  const [drill, setDrill] = useState<number | null>(null);
  const [detail, setDetail] = useState<AgingDetailRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const d = await api<{ rows: AgingRow[]; as_of: string }>("/reports/aging");
      setRows(d.rows);
      setAsOf(d.as_of);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function openBucket(i: number) {
    setDrill(i);
    setDetail(null);
    try {
      const d = await api<{ rows: AgingDetailRow[] }>(`/reports/aging?bucket=${i}`);
      setDetail(d.rows);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="stack">
      <Card title="Aging — overdue installments by DPD bucket">
        <p className="muted">
          Display grouping from the <code>dpd_report_buckets</code> config — not a
          collections-action policy. {asOf && `As of ${asOf}.`}
        </p>
        <div className="inline-form">
          <button className="btn-primary" onClick={() => void load()}>
            Run report
          </button>
          <ExportGroup path="/reports/aging" base="aging" onError={setError} />
        </div>
      </Card>

      <ErrorNote message={error} />

      {rows && (
        <Card>
          <table className="data" aria-label="Aging buckets">
            <thead>
              <tr>
                <th>DPD bucket</th>
                <th className="num">Installments</th>
                <th className="num">Outstanding</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.bucket} data-testid={`aging-bucket-${b.bucket}`}>
                  <td>{b.label}</td>
                  <td className="num">{b.installment_count}</td>
                  <td className="num">{money(b.outstanding_amount)}</td>
                  <td>
                    <button
                      className="btn-link"
                      disabled={b.installment_count === 0}
                      onClick={() => void openBucket(b.bucket)}
                    >
                      Drill in →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {drill != null && (
        <Card
          title={`Bucket ${rows?.[drill]?.label ?? drill} — installments`}
          soft
        >
          <div className="inline-form" style={{ marginBottom: "0.75rem" }}>
            <ExportGroup
              path={`/reports/aging?bucket=${drill}`}
              base={`aging-bucket-${drill}`}
              onError={setError}
            />
          </div>
          {!detail ? (
            <p className="muted">Loading…</p>
          ) : (
            <table className="data" aria-label="Aging bucket detail">
              <thead>
                <tr>
                  <th>Contract</th>
                  <th>Customer</th>
                  <th className="num">Seq</th>
                  <th>Due</th>
                  <th className="num">DPD</th>
                  <th className="num">Outstanding</th>
                </tr>
              </thead>
              <tbody>
                {detail.map((r) => (
                  <tr key={r.installment_id} data-testid={`aging-detail-${r.installment_id}`}>
                    <td>
                      <Link to={`/contracts/${r.contract_id}`}>{r.contract_id}</Link>
                    </td>
                    <td>
                      <Link to={`/customers/${r.customer_id}`}>{r.customer_name}</Link>
                    </td>
                    <td className="num">{r.sequence_number}</td>
                    <td>{r.due_date}</td>
                    <td className="num">{r.dpd}</td>
                    <td className="num">{money(r.outstanding_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// links-out (only for the literal full-list views)
// --------------------------------------------------------------------------- //
function ExistingScreenLink({
  category,
  to,
  label,
}: {
  category: string;
  to: string;
  label: string;
}) {
  return (
    <Card title={`${category} — full list`}>
      <p className="muted">
        This data already has a dedicated screen — no duplicate is built here.
        Its own export controls live there.
      </p>
      <Link className="btn-link" to={to} data-testid={`reports-link-${category}`}>
        Go to {label} →
      </Link>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// category / sub-report structure
// --------------------------------------------------------------------------- //
type Sub = {
  id: string;
  label: string;
  render: () => JSX.Element;
};

interface CategoryDef {
  id: string;
  label: string;
  icon: LucideIcon;
  desc: string;
  subs: Sub[];
}

function genericSub(
  id: string,
  label: string,
  endpoint: string,
  base: string,
  summaryKeys?: string[],
): Sub {
  return {
    id,
    label,
    render: () => (
      <GenericTableReport endpoint={endpoint} base={base} summaryKeys={summaryKeys} />
    ),
  };
}

const CATEGORIES: CategoryDef[] = [
  {
    id: "Contracts",
    label: "Contracts",
    icon: FileText,
    desc: "Every installment contract, filterable, plus status & channel breakdowns.",
    subs: [
      { id: "all", label: "All Contracts", render: () => <ContractsReport /> },
      genericSub("by-status", "By Status", "/reports/contracts/by-status", "contracts-by-status"),
      genericSub("by-channel", "By Channel", "/reports/contracts/by-channel", "contracts-by-channel"),
    ],
  },
  {
    id: "Profitability",
    label: "Profitability",
    icon: TrendingUp,
    desc: "Contractual vs recognized vs unearned profit, by tenor and category.",
    subs: [{ id: "summary", label: "Portfolio Summary", render: () => <ProfitabilityReportView /> }],
  },
  {
    id: "Customers",
    label: "Customers",
    icon: Users,
    desc: "The full book, grouped by risk band or by outstanding exposure.",
    subs: [
      {
        id: "full",
        label: "Full Directory",
        render: () => (
          <ExistingScreenLink category="Customers" to="/customers" label="Customer Directory" />
        ),
      },
      genericSub("by-risk", "By Risk Band", "/reports/customers/by-risk", "customers-by-risk", [
        "thresholds",
      ]),
      genericSub("by-exposure", "By Exposure", "/reports/customers/by-exposure", "customers-by-exposure", [
        "total",
      ]),
    ],
  },
  {
    id: "Products",
    label: "Products",
    icon: Package,
    desc: "Catalogue and stock position — availability and category rollups.",
    subs: [
      {
        id: "full",
        label: "Full Directory",
        render: () => (
          <ExistingScreenLink category="Products" to="/products" label="Product Directory" />
        ),
      },
      genericSub("by-availability", "By Availability", "/reports/products/by-availability", "products-by-availability", [
        "available",
        "sold_out",
      ]),
      genericSub("by-category", "By Category", "/reports/products/by-category", "products-by-category"),
    ],
  },
  {
    id: "Collections",
    label: "Collections",
    icon: ClipboardList,
    desc: "Cases, promise-to-pay performance and late-fee totals.",
    subs: [
      {
        id: "full",
        label: "Full Case List",
        render: () => (
          <ExistingScreenLink category="Collections" to="/collections" label="Collections case list" />
        ),
      },
      genericSub("status", "Status Summary", "/reports/collections/status-summary", "collections-status"),
      genericSub("promise", "Promise Performance", "/reports/collections/promise-performance", "collections-promise"),
      genericSub("late-fees", "Late Fees Summary", "/reports/collections/late-fees-summary", "collections-late-fees", [
        "charged_amount",
        "waived_amount",
      ]),
    ],
  },
  {
    id: "Aging",
    label: "Aging",
    icon: Clock,
    desc: "Overdue installments grouped into DPD buckets, with drill-down.",
    subs: [{ id: "buckets", label: "DPD Buckets", render: () => <AgingReport /> }],
  },
];

export function ReportsPage() {
  const [categoryId, setCategoryId] = useState(CATEGORIES[0].id);
  const [subId, setSubId] = useState(CATEGORIES[0].subs[0].id);

  const category = CATEGORIES.find((c) => c.id === categoryId)!;
  const sub = category.subs.find((s) => s.id === subId) ?? category.subs[0];

  function selectCategory(c: CategoryDef) {
    setCategoryId(c.id);
    setSubId(c.subs[0].id); // auto-select the first sub-report
  }

  return (
    <div className="stack">
      <h1>Reports</h1>
      <p className="muted">
        Bounded reporting — every figure is a live query over existing tables.
        No charts or scheduled reports; CSV / Excel / PDF export.
      </p>
      <div className="split">
        <div className="report-cats" role="tablist" aria-label="Report categories">
          {CATEGORIES.map((c) => {
            const Icon = c.icon;
            return (
              <button
                key={c.id}
                role="tab"
                aria-label={c.label}
                aria-selected={c.id === categoryId}
                className={c.id === categoryId ? "report-cat active" : "report-cat"}
                onClick={() => selectCategory(c)}
              >
                <Icon size={18} aria-hidden />
                <span>
                  <span className="report-cat__title">{c.label}</span>
                  <span className="report-cat__desc">{c.desc}</span>
                </span>
              </button>
            );
          })}
        </div>

        <div className="split__body" data-testid={`report-${categoryId}`}>
          {category.subs.length > 1 && (
            <div className="subreport-nav" role="tablist" aria-label="Sub-reports">
              {category.subs.map((s) => (
                <button
                  key={s.id}
                  role="tab"
                  aria-selected={s.id === sub.id}
                  className={s.id === sub.id ? "active" : undefined}
                  onClick={() => setSubId(s.id)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <div data-testid={`subreport-${sub.id}`}>{sub.render()}</div>
        </div>
      </div>
    </div>
  );
}
