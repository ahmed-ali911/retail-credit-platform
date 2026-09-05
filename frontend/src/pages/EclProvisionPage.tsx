import {
  Fragment,
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import {
  Activity,
  Gauge,
  Layers,
  Percent,
  PlayCircle,
  ShieldAlert,
  Wallet,
} from "lucide-react";
import { api, downloadFile, errorMessage } from "../api/client";
import type {
  EclContractDetail,
  EclDashboard,
  EclPortfolio,
  EclRunResult,
} from "../api/types";
import { MetricGrid, MetricTile } from "../components/MetricTile";
import { Card, ErrorNote, Field, RefCode, money } from "../components/ui";

const NA = "n/a";

function pct(v: number | null | undefined): string {
  return v == null ? NA : `${(v * 100).toFixed(2)}%`;
}

function num(v: number | null | undefined): string {
  return v == null ? NA : money(v);
}

function qs(params: Record<string, string>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  return p.toString();
}

// --------------------------------------------------------------------------- //
// Dashboard tiles
// --------------------------------------------------------------------------- //
function DashboardTiles({ data }: { data: EclDashboard }) {
  const stage = data.stage_exposure;
  return (
    <div className="stack">
      <MetricGrid>
        <MetricTile label="Total EAD" value={money(data.total_ead)} icon={Wallet} subLabel="exposure at default" />
        <MetricTile
          label="ECL balance"
          value={money(data.ecl_balance)}
          tone="warn"
          icon={ShieldAlert}
          subLabel={
            data.ecl_not_computable_count > 0
              ? `${data.ecl_not_computable_count} contract(s) n/a — ${data.pd_lgd_note ?? ""}`
              : undefined
          }
        />
        <MetricTile label="Provision balance" value={money(data.provision_balance)} icon={Layers} subLabel="provision = ECL in this slice" />
        <MetricTile label="ECL coverage %" value={pct(data.ecl_coverage_pct)} icon={Percent} />
        <MetricTile label="Contracts assessed" value={data.contracts_assessed} icon={Activity} />
      </MetricGrid>

      <Card title="Stage exposure (IFRS 9 3-stage — Path B only)" soft>
        {stage === "n/a" ? (
          <p className="muted" data-testid="ecl-stage-na">
            <strong>n/a</strong> — the active methodology (<code>{data.active_methodology}</code>)
            has no stage concept. A 1/2/3 split only applies under{" "}
            <code>three_stage</code>.
          </p>
        ) : (
          <table className="data" aria-label="Stage exposure">
            <thead>
              <tr>
                <th>Stage</th>
                <th className="num">EAD exposure</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stage).map(([k, v]) => (
                <tr key={k} data-testid={`ecl-stage-${k}`}>
                  <td>{k === "unstaged" ? "unstaged" : `Stage ${k}`}</td>
                  <td className="num">{money(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Run ECL Calculation panel
// --------------------------------------------------------------------------- //
function RunPanel({
  data,
  onRun,
  busy,
}: {
  data: EclDashboard;
  onRun: (asOf: string) => void;
  busy: boolean;
}) {
  const [asOf, setAsOf] = useState("");
  const run = data.last_run;
  return (
    <Card title="Run ECL calculation">
      <p className="muted">
        On-demand recalculation (no scheduler yet). Re-assesses every active
        contract and emits one <code>ecl_provision_movement</code> accounting
        event for the portfolio's provision movement.
      </p>
      <dl className="kv">
        <dt>Active methodology</dt>
        <dd data-testid="ecl-active-methodology">
          <code>{data.active_methodology}</code> — read-only, set via Configuration
        </dd>
        <dd style={{ gridColumn: "1 / -1" }} className="muted">
          {data.methodology_note}
        </dd>
        <dt>Last successful run</dt>
        <dd data-testid="ecl-last-run">
          {run ? `${run.as_of_date} (run #${run.run_id})` : "— never run"}
        </dd>
        <dt>Contracts processed</dt>
        <dd>{run ? run.contracts_assessed : NA}</dd>
        <dt>Total ECL generated</dt>
        <dd>{run ? num(run.total_ecl) : NA}</dd>
        <dt>Provision movement (last run)</dt>
        <dd>{run ? num(run.total_provision_movement) : NA}</dd>
      </dl>
      <form
        className="inline-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          onRun(asOf);
        }}
      >
        <Field
          label="As of (optional — defaults to today)"
          type="date"
          value={asOf}
          onChange={(e) => setAsOf(e.target.value)}
        />
        <button className="btn-primary" type="submit" disabled={busy}>
          <PlayCircle size={15} aria-hidden /> {busy ? "Running…" : "Run ECL calculation"}
        </button>
      </form>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Drill-down — the exact inputs of a contract's last calculation
// --------------------------------------------------------------------------- //
function ContractDrilldown({ contractId }: { contractId: number }) {
  const [detail, setDetail] = useState<EclContractDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    void api<EclContractDetail>(`/ecl/contracts/${contractId}`)
      .then(setDetail)
      .catch((e) => setError(errorMessage(e)));
  }, [contractId]);

  if (error) return <ErrorNote message={error} />;
  if (!detail) return <p className="muted">Loading…</p>;

  return (
    <div className="stack" data-testid={`ecl-drilldown-${contractId}`}>
      <dl className="kv">
        <dt>Methodology</dt>
        <dd>{detail.methodology}</dd>
        <dt>Assessment date</dt>
        <dd>{detail.assessment_date}</dd>
        <dt>EAD</dt>
        <dd>{num(detail.ead)}</dd>
        <dt>DPD</dt>
        <dd>{detail.dpd}</dd>
        <dt>DPD bucket</dt>
        <dd>{detail.dpd_bucket ?? NA}</dd>
        <dt>Stage</dt>
        <dd>{detail.stage ?? NA}</dd>
        {detail.stage_reason && (
          <>
            <dt>Stage reasoning</dt>
            <dd>{detail.stage_reason}</dd>
          </>
        )}
        <dt>PD</dt>
        <dd>{detail.pd ?? detail.pd_lgd_note ?? NA}</dd>
        <dt>LGD</dt>
        <dd>{detail.lgd ?? detail.pd_lgd_note ?? NA}</dd>
        <dt>Loss rate</dt>
        <dd>{detail.loss_rate == null ? NA : pct(detail.loss_rate)}</dd>
        <dt>Calculated ECL</dt>
        <dd>{detail.ecl_amount ?? detail.ecl_note ?? NA}</dd>
        <dt>Provision movement</dt>
        <dd>{num(detail.provision_movement)}</dd>
      </dl>

      <Card title="Config snapshot at calculation time" soft>
        <pre
          data-testid="ecl-config-snapshot"
          style={{
            margin: 0,
            padding: "0.75rem",
            background: "var(--color-bg)",
            borderRadius: "var(--radius-sm, 6px)",
            fontSize: "0.78rem",
            overflowX: "auto",
          }}
        >
          {JSON.stringify(detail.config_snapshot, null, 2)}
        </pre>
      </Card>

      {detail.history.length > 1 && (
        <Card title="Assessment history" soft>
          <table className="data">
            <thead>
              <tr>
                <th>Date</th>
                <th>Method</th>
                <th className="num">EAD</th>
                <th className="num">DPD</th>
                <th className="num">Stage</th>
                <th className="num">ECL</th>
                <th className="num">Movement</th>
              </tr>
            </thead>
            <tbody>
              {detail.history.map((h, i) => (
                <tr key={i}>
                  <td>{h.assessment_date}</td>
                  <td>{h.methodology}</td>
                  <td className="num">{num(h.ead)}</td>
                  <td className="num">{h.dpd}</td>
                  <td className="num">{h.stage ?? NA}</td>
                  <td className="num">{h.ecl_amount ?? NA}</td>
                  <td className="num">{num(h.provision_movement)}</td>
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
// Portfolio table
// --------------------------------------------------------------------------- //
const RISK_BANDS = ["low", "medium", "high", "unscored"];
const CONTRACT_STATUSES = ["created", "active", "closed"];

function PortfolioTable() {
  const [filters, setFilters] = useState({
    assessment_date: "",
    product_id: "",
    risk_band: "",
    dpd_bucket: "",
    contract_status: "",
  });
  const [data, setData] = useState<EclPortfolio | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const query = useCallback(
    () =>
      qs({
        assessment_date: filters.assessment_date,
        product_id: filters.product_id,
        risk_band: filters.risk_band,
        dpd_bucket: filters.dpd_bucket,
        contract_status: filters.contract_status,
      }),
    [filters],
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const q = query();
      setData(await api<EclPortfolio>(`/ecl/assessments${q ? `?${q}` : ""}`));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const set = (k: keyof typeof filters) => (e: { target: { value: string } }) =>
    setFilters((f) => ({ ...f, [k]: e.target.value }));

  const buckets = Array.from(
    new Set((data?.rows ?? []).map((r) => r.dpd_bucket).filter(Boolean) as string[]),
  );

  return (
    <div className="stack">
      <Card title="ECL portfolio">
        <form
          className="field-row"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void load();
          }}
        >
          <Field label="Assessment date" type="date" value={filters.assessment_date} onChange={set("assessment_date")} />
          <Field label="Product #" value={filters.product_id} onChange={set("product_id")} inputMode="numeric" />
          <label className="field">
            <span>Risk band</span>
            <select value={filters.risk_band} onChange={set("risk_band")}>
              <option value="">Any</option>
              {RISK_BANDS.map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>DPD bucket</span>
            <select value={filters.dpd_bucket} onChange={set("dpd_bucket")}>
              <option value="">Any</option>
              {["current", ...buckets.filter((b) => b !== "current")].map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Contract status</span>
            <select value={filters.contract_status} onChange={set("contract_status")}>
              <option value="">Any</option>
              {CONTRACT_STATUSES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
        </form>
        <div className="inline-form" style={{ marginTop: "0.75rem" }}>
          <button className="btn-primary" onClick={() => void load()}>
            Apply filters
          </button>
          <span className="export-group">
            <span>Export</span>
            {(["csv", "xlsx", "pdf"] as const).map((fmt) => (
              <button
                key={fmt}
                type="button"
                className="btn-secondary"
                onClick={() => {
                  const q = query();
                  downloadFile(
                    `/ecl/assessments?${q ? `${q}&` : ""}format=${fmt}`,
                    `ecl-portfolio.${fmt}`,
                  ).catch((e) => setError(errorMessage(e)));
                }}
              >
                {fmt.toUpperCase()}
              </button>
            ))}
          </span>
        </div>
      </Card>

      <ErrorNote message={error} />

      {data && (
        <Card>
          <p className="muted">
            {data.rows.length} contract(s) · Total EAD {money(data.total_ead)} ·
            ECL {data.total_ecl == null ? NA : money(data.total_ecl)}
            {data.ecl_not_computable_count > 0 &&
              ` · ${data.ecl_not_computable_count} not computable (no PD/LGD source)`}
          </p>
          <table className="data" aria-label="ECL portfolio">
            <thead>
              <tr>
                <th>Contract</th>
                <th>Customer</th>
                <th className="num">EAD</th>
                <th className="num">DPD</th>
                <th>Stage</th>
                <th>PD</th>
                <th>LGD</th>
                <th className="num">Calculated ECL</th>
                <th>Assessment date</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="muted">No assessments match.</td>
                </tr>
              ) : (
                data.rows.map((r) => (
                  <Fragment key={r.contract_id}>
                    <tr data-testid={`ecl-row-${r.contract_id}`}>
                      <td>
                        <button
                          className="btn-link"
                          onClick={() =>
                            setOpen((o) => (o === r.contract_id ? null : r.contract_id))
                          }
                        >
                          <RefCode entity="InstallmentContract" id={r.contract_id} />
                        </button>
                      </td>
                      <td>{r.customer_name ?? "—"}</td>
                      <td className="num">{num(r.ead)}</td>
                      <td className="num">{r.dpd}</td>
                      <td>
                        {r.stage == null ? (
                          <span className="muted">n/a</span>
                        ) : (
                          `Stage ${r.stage}`
                        )}
                      </td>
                      <td>{r.pd ?? <span className="muted">{r.pd_lgd_note ?? "n/a"}</span>}</td>
                      <td>{r.lgd ?? <span className="muted">{r.pd_lgd_note ?? "n/a"}</span>}</td>
                      <td className="num">
                        {r.ecl_amount == null ? (
                          <span className="muted">{r.ecl_note ?? "n/a"}</span>
                        ) : (
                          money(r.ecl_amount)
                        )}
                      </td>
                      <td>{r.assessment_date}</td>
                    </tr>
                    {open === r.contract_id && (
                      <tr>
                        <td colSpan={9}>
                          <ContractDrilldown contractId={r.contract_id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
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
// Page
// --------------------------------------------------------------------------- //
export function EclProvisionPage() {
  const [dash, setDash] = useState<EclDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setError(null);
    void api<EclDashboard>("/ecl/dashboard")
      .then(setDash)
      .catch((e) => setError(errorMessage(e)));
  }, [reloadKey]);

  async function run(asOf: string) {
    setBusy(true);
    setRunMsg(null);
    try {
      const res = await api<EclRunResult>("/ecl/run", {
        method: "POST",
        body: asOf ? { as_of: asOf } : {},
      });
      setRunMsg(
        `Run #${res.run_id} — ${res.contracts_assessed} contract(s), ` +
          `ECL ${res.total_ecl == null ? "n/a (no PD/LGD source)" : money(res.total_ecl)}, ` +
          `provision movement ${
            res.total_provision_movement == null
              ? "n/a"
              : money(res.total_provision_movement)
          }` +
          (res.accounting_event_id
            ? ` · accounting event #${res.accounting_event_id}`
            : " · no accounting event (nothing computable to post)"),
      );
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>ECL &amp; Provision</h1>
      <p className="muted">
        Expected Credit Loss is assessed for <strong>every</strong> active
        contract from activation onward — not only delinquent ones. The
        calculation path is set by the <code>ecl_methodology</code> config
        switch. Paths A (simplified lifetime) and B (IFRS 9 3-stage) are
        structurally present; Path B's PD/LGD/ECL show <strong>n/a</strong>{" "}
        pending a real data source and Finance/Risk sign-off.
      </p>

      <ErrorNote message={error} />
      {runMsg && (
        <div className="alert alert--info" role="status" data-testid="ecl-run-result">
          {runMsg}
        </div>
      )}

      {dash && (
        <>
          <DashboardTiles data={dash} />
          <RunPanel data={dash} onRun={run} busy={busy} />
        </>
      )}

      <PortfolioTable />

      <p className="muted" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
        <Gauge size={14} aria-hidden />
        Every figure is a live read of persisted assessments; the methodology and
        config values in force are snapshotted per assessment (see a contract's
        drill-down).
      </p>
    </div>
  );
}
