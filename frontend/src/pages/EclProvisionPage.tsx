import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  Gauge,
  Layers,
  Percent,
  PlayCircle,
  ShieldAlert,
  Wallet,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, downloadFile, errorMessage } from "../api/client";
import type {
  EclConfigResponse,
  EclDashboard,
  EclPortfolio,
  EclRunResult,
} from "../api/types";
import { MetricGrid, MetricTile } from "../components/MetricTile";
import { Card, EmptyState, ErrorNote, Field, money } from "../components/ui";
import { SkeletonTable, SkeletonTiles } from "../components/Skeleton";

const NA = "n/a";
const STAGE_COLOURS: Record<string, string> = {
  "1": "var(--color-secondary, #2e7d5b)",
  "2": "var(--color-warm, #b7791f)",
  "3": "var(--color-danger, #b3261e)",
};

function pct(v: number | null | undefined): string {
  return v == null ? NA : `${(v * 100).toFixed(2)}%`;
}
function num(v: number | null | undefined): string {
  return v == null ? NA : money(v);
}
function rate(v: number | null | undefined): string {
  return v == null ? NA : `${(v * 100).toFixed(2)}%`;
}
function qs(params: Record<string, string>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  return p.toString();
}

// --------------------------------------------------------------------------- //
// KPI + stage cards
// --------------------------------------------------------------------------- //
function Kpis({ d }: { d: EclDashboard }) {
  return (
    <MetricGrid>
      <MetricTile label="Total exposure (EAD)" value={money(d.total_exposure)} icon={Wallet} />
      <MetricTile
        label="Total ECL"
        value={money(d.total_ecl)}
        tone="warn"
        icon={ShieldAlert}
        subLabel={`config v${d.ecl_config_version} · ${d.active_methodology}`}
      />
      <MetricTile
        label="Total provision"
        value={money(d.total_provision)}
        icon={Layers}
        subLabel="carrying provision = ECL"
      />
      <MetricTile
        label="Provision movement"
        value={money(d.provision_movement)}
        tone={d.provision_movement > 0 ? "warn" : d.provision_movement < 0 ? "good" : "neutral"}
        icon={Activity}
        subLabel="latest run vs prior"
      />
      <MetricTile label="Coverage ratio" value={pct(d.coverage_ratio)} icon={Percent} />
    </MetricGrid>
  );
}

function StageCards({ d }: { d: EclDashboard }) {
  const stageEcl = d.stage_ecl ?? {};
  const stageExp = d.stage_exposure ?? {};
  const chart = ["1", "2", "3"].map((s) => ({
    stage: `Stage ${s}`,
    key: s,
    ecl: stageEcl[s] ?? 0,
  }));
  const staged = d.active_methodology === "three_stage";
  return (
    <Card title="IFRS 9 staging" soft>
      {!staged ? (
        <p className="muted" data-testid="ecl-stage-na">
          <strong>n/a</strong> — the active methodology (<code>{d.active_methodology}</code>) has
          no stage concept. A Stage 1 / 2 / 3 split applies only under <code>three_stage</code>.
        </p>
      ) : (
        <>
          <div className="metric-grid" style={{ marginBottom: "0.75rem" }}>
            {["1", "2", "3"].map((s) => (
              <div key={s} className="metric-tile" data-testid={`ecl-stage-card-${s}`}>
                <div className="metric-tile__head">
                  <span className="metric-tile__label">
                    Stage {s}
                    {s === "1" ? " — 12-month ECL" : " — lifetime ECL"}
                  </span>
                </div>
                <div className="metric-tile__value">{money(stageEcl[s] ?? 0)}</div>
                <div className="metric-tile__sub">
                  exposure {money(stageExp[s] ?? 0)}
                </div>
              </div>
            ))}
          </div>
          <div style={{ width: "100%", height: 200 }}>
            <ResponsiveContainer>
              <BarChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="stage" fontSize={12} />
                <YAxis fontSize={12} width={70} />
                <Tooltip formatter={(v) => money(Number(v))} />
                <Bar dataKey="ecl" radius={[4, 4, 0, 0]}>
                  {chart.map((c) => (
                    <Cell key={c.key} fill={STAGE_COLOURS[c.key]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Card>
  );
}

function IndicatorStrip({ d }: { d: EclDashboard }) {
  const m = d.stage_migration ?? {};
  return (
    <Card soft>
      <div className="kv" data-testid="ecl-indicators">
        <dt>Default (Stage 3) exposure</dt>
        <dd>{money(d.default_exposure)}</dd>
        <dt>Overrides — active</dt>
        <dd>{d.overrides_active}</dd>
        <dt>Overrides — pending approval</dt>
        <dd>
          {d.overrides_pending > 0 ? (
            <Link to="/approvals">{d.overrides_pending} pending</Link>
          ) : (
            "0"
          )}
        </dd>
        <dt>Stage migration (last run)</dt>
        <dd>
          ▲ {m.upgraded ?? 0} upgraded · ▼ {m.downgraded ?? 0} downgraded · {m.unchanged ?? 0} held
        </dd>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Run + post
// --------------------------------------------------------------------------- //
function RunPanel({
  d,
  onDone,
}: {
  d: EclDashboard;
  onDone: () => void;
}) {
  const [asOf, setAsOf] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = d.last_run;

  async function go(post: boolean) {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const res = await api<EclRunResult>("/ecl/run", {
        method: "POST",
        body: { post, ...(asOf ? { as_of: asOf } : {}) },
      });
      setMsg(
        `${res.run_ref} — ${res.status} · ${res.contracts_assessed} contract(s) · ` +
          `ECL ${money(res.total_ecl)} · movement ${num(res.total_provision_movement)}` +
          (res.posted ? ` · posted (event #${res.accounting_event_id})` : " · not posted"),
      );
      onDone();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Run ECL calculation">
      <p className="muted">
        On-demand recalculation (no scheduler). Re-assesses every active contract; a run is
        immutable. <strong>Post</strong> finalises it — one accounting event per contract
        provision movement plus a portfolio roll-up.
      </p>
      <dl className="kv">
        <dt>Active methodology</dt>
        <dd data-testid="ecl-active-methodology">
          <code>{d.active_methodology}</code> · config v{d.ecl_config_version} — set via{" "}
          <Link to="/ecl/config">ECL Configuration</Link>
        </dd>
        <dt>Last run</dt>
        <dd data-testid="ecl-last-run">
          {run
            ? `${run.run_ref} · ${run.status} · as of ${run.as_of_date}`
            : "— never run"}
        </dd>
        <dt>Total ECL (last run)</dt>
        <dd>{run ? num(run.total_ecl) : NA}</dd>
        <dt>Provision movement (last run)</dt>
        <dd>{run ? num(run.total_provision_movement) : NA}</dd>
      </dl>
      <form
        className="inline-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          void go(false);
        }}
      >
        <Field
          label="As of (optional — defaults to today)"
          type="date"
          value={asOf}
          onChange={(e) => setAsOf(e.target.value)}
        />
        <button className="btn-primary" type="submit" disabled={busy}>
          <PlayCircle size={15} aria-hidden /> {busy ? "Running…" : "Run (draft)"}
        </button>
        <button
          className="btn-secondary"
          type="button"
          disabled={busy}
          onClick={() => void go(true)}
        >
          Run &amp; post
        </button>
      </form>
      {msg && (
        <div className="alert alert--info" role="status" data-testid="ecl-run-result">
          {msg}
        </div>
      )}
      <ErrorNote message={err} />
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Portfolio table
// --------------------------------------------------------------------------- //
const PAGE = 25;

function PortfolioTable() {
  const [filters, setFilters] = useState({
    stage: "",
    dpd_band: "",
    risk_rating: "",
    override_status: "",
  });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<EclPortfolio | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useCallback(
    (extra: Record<string, string> = {}) =>
      qs({
        stage: filters.stage,
        dpd_band: filters.dpd_band,
        risk_rating: filters.risk_rating,
        override_status: filters.override_status,
        limit: String(PAGE),
        offset: String(offset),
        ...extra,
      }),
    [filters, offset],
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api<EclPortfolio>(`/ecl/assessments?${query()}`));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const set = (k: keyof typeof filters) => (e: { target: { value: string } }) => {
    setOffset(0);
    setFilters((f) => ({ ...f, [k]: e.target.value }));
  };

  const pageInfo = useMemo(() => {
    if (!data) return "";
    const from = data.total === 0 ? 0 : data.offset + 1;
    const to = Math.min(data.offset + data.limit, data.total);
    return `${from}–${to} of ${data.total}`;
  }, [data]);

  return (
    <div className="stack">
      <Card title="ECL portfolio">
        <form className="field-row" onSubmit={(e) => { e.preventDefault(); void load(); }}>
          <label className="field">
            <span>Final stage</span>
            <select value={filters.stage} onChange={set("stage")}>
              <option value="">Any</option>
              <option value="1">Stage 1</option>
              <option value="2">Stage 2</option>
              <option value="3">Stage 3</option>
            </select>
          </label>
          <label className="field">
            <span>DPD band</span>
            <select value={filters.dpd_band} onChange={set("dpd_band")}>
              <option value="">Any</option>
              {["current", "1-30", "31-60", "61-90", "91+"].map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </label>
          <Field
            label="Risk rating"
            value={filters.risk_rating}
            onChange={set("risk_rating")}
            placeholder="A / B / C…"
          />
          <label className="field">
            <span>Override</span>
            <select value={filters.override_status} onChange={set("override_status")}>
              <option value="">Any</option>
              <option value="active">With active override</option>
              <option value="none">No override</option>
            </select>
          </label>
        </form>
        <div className="inline-form" style={{ marginTop: "0.75rem" }}>
          <button className="btn-primary" onClick={() => void load()}>Apply</button>
          <span className="export-group">
            <span>Export</span>
            {(["csv", "xlsx", "pdf"] as const).map((fmt) => (
              <button
                key={fmt}
                type="button"
                className="btn-secondary"
                onClick={() =>
                  downloadFile(
                    `/ecl/assessments?${query({ format: fmt })}`,
                    `ecl-portfolio.${fmt}`,
                  ).catch((e) => setError(errorMessage(e)))
                }
              >
                {fmt.toUpperCase()}
              </button>
            ))}
          </span>
        </div>
      </Card>

      <ErrorNote message={error} />

      {!data && !error && (
        <Card><SkeletonTable rows={6} cols={9} /></Card>
      )}

      {data && data.rows.length === 0 && (
        <Card><EmptyState message="No ECL assessments match these filters." /></Card>
      )}

      {data && data.rows.length > 0 && (
        <Card>
          <p className="muted">
            {pageInfo} · EAD {money(data.total_ead)} · ECL {money(data.total_ecl)}
          </p>
          <div style={{ overflowX: "auto" }}>
            <table className="data" aria-label="ECL portfolio">
              <thead>
                <tr>
                  <th>Contract</th>
                  <th>Customer</th>
                  <th>Rating</th>
                  <th className="num">EAD</th>
                  <th className="num">DPD</th>
                  <th className="num">Auto stage</th>
                  <th className="num">Override</th>
                  <th className="num">Final stage</th>
                  <th className="num">Final ECL</th>
                  <th className="num">Movement</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.contract_id} data-testid={`ecl-row-${r.contract_id}`}>
                    <td>
                      <Link to={`/ecl/contracts/${r.contract_id}`}>#{r.contract_id}</Link>
                    </td>
                    <td>{r.customer_name ?? "—"}</td>
                    <td>{r.risk_rating ?? NA}</td>
                    <td className="num">{num(r.ead)}</td>
                    <td className="num">{r.dpd}</td>
                    <td className="num">{r.automated_stage ?? NA}</td>
                    <td className="num">
                      {r.override_stage != null ? (
                        <span className="badge badge--warn">→ {r.override_stage}</span>
                      ) : r.override_status === "active" ? (
                        <span className="badge badge--warn">param</span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="num"><strong>{r.final_stage ?? NA}</strong></td>
                    <td className="num">{num(r.final_ecl)}</td>
                    <td className="num">{num(r.provision_movement)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="inline-form" style={{ marginTop: "0.75rem" }}>
            <button
              className="btn-secondary"
              disabled={data.offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              ← Prev
            </button>
            <button
              className="btn-secondary"
              disabled={data.offset + data.limit >= data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next →
            </button>
          </div>
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
  const [cfg, setCfg] = useState<EclConfigResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setError(null);
    void api<EclDashboard>("/ecl/dashboard").then(setDash).catch((e) => setError(errorMessage(e)));
    void api<EclConfigResponse>("/ecl/config").then(setCfg).catch(() => undefined);
  }, [reloadKey]);

  void rate; // retained helper (used by detail page pattern)

  return (
    <div className="stack">
      <h1>ECL &amp; Provision</h1>
      <p className="muted">
        Expected Credit Loss is assessed for <strong>every</strong> active contract from
        activation onward. Stage is decided by a configurable engine (Stage-3 triggers first,
        then SICR) — DPD is one trigger among several. The automated result, any manual
        override, and the final approved result are stored separately. Every PD / LGD /
        threshold is a placeholder — <strong>BUSINESS / RISK MODEL DECISION REQUIRED</strong>.
      </p>

      <ErrorNote message={error} />
      {!dash && !error && <SkeletonTiles count={5} />}

      {dash && (
        <>
          <Kpis d={dash} />
          <StageCards d={dash} />
          <IndicatorStrip d={dash} />
          <RunPanel d={dash} onDone={() => setReloadKey((k) => k + 1)} />
        </>
      )}

      {cfg && (
        <p className="muted" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          <Gauge size={14} aria-hidden />
          Active configuration <strong>v{cfg.active.ecl_config_version}</strong>. Past runs are
          stamped with the version that produced them and stay reproducible.
        </p>
      )}

      <PortfolioTable />
    </div>
  );
}
