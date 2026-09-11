import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { Check, X } from "lucide-react";
import { api, errorMessage } from "../api/client";
import type { EclContractDetail } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Card, EmptyState, ErrorNote, Field, SelectField, money } from "../components/ui";
import { SkeletonText } from "../components/Skeleton";
import { useToast } from "../components/Toast";

const NA = "n/a";
const OVERRIDE_ROLES = ["finance_officer", "credit_manager", "admin"];
const REASON_CODES = [
  "CUSTOMER_FINANCIAL_DIFFICULTY",
  "UNLIKELY_TO_PAY",
  "RESTRUCTURING_FORBEARANCE",
  "DATA_QUALITY_ISSUE",
  "MODEL_LIMITATION",
  "SPECIFIC_CREDIT_EVENT",
  "OTHER",
];

function rate(v: number | null | undefined): string {
  return v == null ? NA : `${(v * 100).toFixed(2)}%`;
}
function num(v: number | null | undefined): string {
  return v == null ? NA : money(v);
}

// --------------------------------------------------------------------------- //
// Triggered-rules checklist + the "why" line
// --------------------------------------------------------------------------- //
function StageExplainer({ d }: { d: EclContractDetail }) {
  const staged = d.automated_stage != null;
  return (
    <Card title="Why this stage?">
     <div data-testid="ecl-why">
      {!staged ? (
        <p className="muted">
          The active methodology (<code>{d.methodology}</code>) has no staging.
        </p>
      ) : (
        <>
          <p>
            <strong>Automated Stage {d.automated_stage}.</strong> {d.stage_reason}
          </p>
          {d.cure_note && (
            <p className="muted" data-testid="ecl-cure-note">
              Curing: {d.cure_note}
            </p>
          )}
          <table className="data" aria-label="Stage triggers">
            <thead>
              <tr>
                <th>Trigger</th>
                <th>Category</th>
                <th>Fired?</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {d.stage_triggers.map((t) => (
                <tr key={t.id} data-testid={`trigger-${t.id}`}>
                  <td>{t.name}</td>
                  <td>{t.category}</td>
                  <td>
                    {t.triggered ? (
                      <span className="badge badge--warn">
                        <Check size={12} aria-hidden /> yes
                      </span>
                    ) : (
                      <span className="muted">
                        <X size={12} aria-hidden /> no
                      </span>
                    )}
                  </td>
                  <td>{t.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
     </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Automated / Override / Final
// --------------------------------------------------------------------------- //
function ResultMatrix({ d }: { d: EclContractDetail }) {
  const rows: Array<[string, string, string, string]> = [
    [
      "Stage",
      String(d.automated_stage ?? NA),
      d.override_stage != null ? String(d.override_stage) : "—",
      String(d.final_stage ?? NA),
    ],
    [
      "12-month PD",
      rate(d.automated_pd_12m),
      d.override_pd_12m != null ? rate(d.override_pd_12m) : "—",
      rate(d.final_pd_12m),
    ],
    [
      "Lifetime PD",
      rate(d.automated_pd_lifetime),
      d.override_pd_lifetime != null ? rate(d.override_pd_lifetime) : "—",
      rate(d.final_pd_lifetime),
    ],
    [
      "LGD",
      rate(d.automated_lgd),
      d.override_lgd != null ? rate(d.override_lgd) : "—",
      rate(d.final_lgd),
    ],
    ["EAD", num(d.ead), d.final_ead !== d.ead ? num(d.final_ead) : "—", num(d.final_ead)],
    ["ECL", num(d.automated_ecl), "—", num(d.final_ecl)],
  ];
  return (
    <Card title="Automated · Override · Final">
     <div data-testid="ecl-result-matrix">
      <p className="muted">
        The automated result is never mutated. An active override layers on top; “Final” is
        what is provisioned.
      </p>
      <table className="data">
        <thead>
          <tr>
            <th>Measure</th>
            <th className="num">Automated</th>
            <th className="num">Override</th>
            <th className="num">Final</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r[0]}>
              <td>{r[0]}</td>
              <td className="num">{r[1]}</td>
              <td className="num">{r[2]}</td>
              <td className="num"><strong>{r[3]}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
     </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Override request forms
// --------------------------------------------------------------------------- //
function OverrideForms({ contractId, onDone }: { contractId: number; onDone: () => void }) {
  const toast = useToast();
  const [err, setErr] = useState<string | null>(null);
  const [stage, setStage] = useState({ stage: "2", reason_code: "MODEL_LIMITATION", justification: "", evidence_ref: "", effective_to: "" });
  const [param, setParam] = useState({ pd_12m: "", pd_lifetime: "", lgd: "", ead: "", reason_code: "MODEL_LIMITATION", justification: "", evidence_ref: "", effective_to: "" });
  const [busy, setBusy] = useState(false);

  async function submit(path: string, body: Record<string, unknown>) {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ id: number }>(path, { method: "POST", body });
      toast.success(`Override requested — approval #${r.id} pending a second approver.`);
      onDone();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Request a manual override (maker-checker)">
      <p className="muted">
        Two separate actions. Both require a different user to approve. Evidence is mandatory.
      </p>
      <ErrorNote message={err} />
      <div className="field-row" style={{ alignItems: "flex-start" }}>
        <form
          className="stack"
          data-testid="stage-override-form"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void submit(`/ecl/assessments/${contractId}/request-stage-override`, {
              stage: Number(stage.stage),
              reason_code: stage.reason_code,
              justification: stage.justification,
              evidence_ref: stage.evidence_ref || undefined,
              effective_to: stage.effective_to || undefined,
            });
          }}
        >
          <h3>Stage override</h3>
          <SelectField label="Force stage" value={stage.stage} onChange={(e) => setStage({ ...stage, stage: e.target.value })}>
            <option value="1">Stage 1</option>
            <option value="2">Stage 2</option>
            <option value="3">Stage 3</option>
          </SelectField>
          <SelectField label="Reason code" value={stage.reason_code} onChange={(e) => setStage({ ...stage, reason_code: e.target.value })}>
            {REASON_CODES.map((c) => <option key={c} value={c}>{c}</option>)}
          </SelectField>
          <Field label="Justification" value={stage.justification} onChange={(e) => setStage({ ...stage, justification: e.target.value })} required minLength={10} />
          <Field label="Evidence reference" value={stage.evidence_ref} onChange={(e) => setStage({ ...stage, evidence_ref: e.target.value })} />
          <Field label="Effective to (optional)" type="date" value={stage.effective_to} onChange={(e) => setStage({ ...stage, effective_to: e.target.value })} />
          <button className="btn-primary" type="submit" disabled={busy}>Request stage override</button>
        </form>

        <form
          className="stack"
          data-testid="param-override-form"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            const body: Record<string, unknown> = {
              reason_code: param.reason_code,
              justification: param.justification,
              evidence_ref: param.evidence_ref || undefined,
              effective_to: param.effective_to || undefined,
            };
            for (const k of ["pd_12m", "pd_lifetime", "lgd", "ead"] as const) {
              if (param[k] !== "") body[k] = Number(param[k]);
            }
            void submit(`/ecl/assessments/${contractId}/request-parameter-override`, body);
          }}
        >
          <h3>Parameter override</h3>
          <Field label="12-month PD (0–1)" value={param.pd_12m} onChange={(e) => setParam({ ...param, pd_12m: e.target.value })} inputMode="decimal" />
          <Field label="Lifetime PD (0–1)" value={param.pd_lifetime} onChange={(e) => setParam({ ...param, pd_lifetime: e.target.value })} inputMode="decimal" />
          <Field label="LGD (0–1)" value={param.lgd} onChange={(e) => setParam({ ...param, lgd: e.target.value })} inputMode="decimal" />
          <Field label="EAD" value={param.ead} onChange={(e) => setParam({ ...param, ead: e.target.value })} inputMode="decimal" />
          <SelectField label="Reason code" value={param.reason_code} onChange={(e) => setParam({ ...param, reason_code: e.target.value })}>
            {REASON_CODES.map((c) => <option key={c} value={c}>{c}</option>)}
          </SelectField>
          <Field label="Justification" value={param.justification} onChange={(e) => setParam({ ...param, justification: e.target.value })} required minLength={10} />
          <Field label="Evidence reference" value={param.evidence_ref} onChange={(e) => setParam({ ...param, evidence_ref: e.target.value })} />
          <Field label="Effective to (optional)" type="date" value={param.effective_to} onChange={(e) => setParam({ ...param, effective_to: e.target.value })} />
          <button className="btn-primary" type="submit" disabled={busy}>Request parameter override</button>
        </form>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //
export function EclAssessmentDetailPage() {
  const { contractId } = useParams<{ contractId: string }>();
  const cid = Number(contractId);
  const { user } = useAuth();
  const toast = useToast();
  const [d, setD] = useState<EclContractDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState(0);

  const load = useCallback(() => {
    setError(null);
    void api<EclContractDetail>(`/ecl/contracts/${cid}`)
      .then(setD)
      .catch((e) => setError(errorMessage(e)));
  }, [cid]);

  useEffect(load, [load, key]);

  async function cancelOverride(id: number) {
    try {
      await api(`/ecl/overrides/${id}/cancel`, { method: "POST", body: { reason: "cancelled from detail view" } });
      toast.success(`Override #${id} cancelled.`);
      setKey((k) => k + 1);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (error) return <div className="stack"><h1>ECL assessment</h1><ErrorNote message={error} /></div>;
  if (!d) return <div className="stack"><h1>ECL assessment</h1><SkeletonText lines={6} /></div>;

  const canOverride = user != null && OVERRIDE_ROLES.includes(user.role);

  return (
    <div className="stack">
      <p className="muted">
        <Link to="/ecl">← ECL &amp; Provision</Link>
      </p>
      <h1>
        ECL assessment — contract #{d.contract_id}
        {d.customer_name ? ` · ${d.customer_name}` : ""}
      </h1>

      <Card title="Summary" soft>
        <div className="kv" data-testid="ecl-summary">
          <dt>Methodology</dt><dd><code>{d.methodology}</code> · config v{d.ecl_config_version}</dd>
          <dt>Assessment date</dt><dd>{d.assessment_date}{d.run_id ? ` · run #${d.run_id}` : " · origination"}</dd>
          <dt>Contract status</dt><dd>{d.contract_status ?? NA}</dd>
          <dt>Risk rating → segment</dt>
          <dd>{d.risk_rating ?? NA} → {d.risk_segment ?? NA} (origination {d.origination_rating ?? NA})</dd>
          <dt>EAD</dt><dd>{num(d.ead)}</dd>
          <dt>DPD</dt><dd>{d.dpd}{d.dpd_bucket ? ` (${d.dpd_bucket})` : ""}</dd>
          <dt>Final stage / ECL</dt>
          <dd><strong>{d.final_stage ?? NA}</strong> · {num(d.final_ecl)}</dd>
        </div>
      </Card>

      <StageExplainer d={d} />
      <ResultMatrix d={d} />

      <Card title="Provision movement" soft>
        <div className="kv" data-testid="ecl-movement">
          <dt>Opening provision</dt><dd>{num(d.opening_provision)}</dd>
          <dt>Calculated ECL</dt><dd>{num(d.calculated_ecl)}</dd>
          <dt>Override adjustment</dt><dd>{num(d.override_adjustment)}</dd>
          <dt>Closing provision</dt><dd><strong>{num(d.closing_provision)}</strong></dd>
          <dt>Movement</dt><dd>{num(d.provision_movement)} ({d.movement_type ?? NA})</dd>
        </div>
      </Card>

      {d.overrides.length > 0 && (
        <Card title="Overrides">
          <table className="data" aria-label="Overrides">
            <thead>
              <tr>
                <th>#</th><th>Type</th><th>Status</th><th>Reason</th>
                <th className="num">Impact</th><th>Effective</th><th />
              </tr>
            </thead>
            <tbody>
              {d.overrides.map((o) => (
                <tr key={o.id} data-testid={`override-row-${o.id}`}>
                  <td>{o.id}</td>
                  <td>{o.override_type}</td>
                  <td><span className="badge">{o.status}</span></td>
                  <td>{o.reason_code}</td>
                  <td className="num">{num(o.financial_impact)}</td>
                  <td>{o.effective_from} → {o.effective_to ?? "—"}</td>
                  <td>
                    {canOverride && ["PENDING", "APPROVED", "ACTIVE"].includes(o.status) && (
                      <button className="btn-link" onClick={() => void cancelOverride(o.id)}>
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {canOverride && <OverrideForms contractId={cid} onDone={() => setKey((k) => k + 1)} />}

      <Card title="Model versions" soft>
        <div className="kv">
          {Object.entries(d.versions).map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k}</dt><dd>{String(v ?? NA)}</dd>
            </div>
          ))}
        </div>
      </Card>

      {d.history.length > 0 && (
        <Card title="Assessment history" soft>
          <table className="data" aria-label="Assessment history">
            <thead>
              <tr>
                <th>Date</th><th>Run</th><th className="num">DPD</th>
                <th className="num">Auto</th><th className="num">Final</th>
                <th className="num">Auto ECL</th><th className="num">Final ECL</th>
                <th className="num">Movement</th>
              </tr>
            </thead>
            <tbody>
              {d.history.map((h, i) => (
                <tr key={i}>
                  <td>{h.assessment_date}</td>
                  <td>{h.run_id ?? "orig"}</td>
                  <td className="num">{h.dpd}</td>
                  <td className="num">{h.automated_stage ?? NA}</td>
                  <td className="num">{h.final_stage ?? NA}</td>
                  <td className="num">{num(h.automated_ecl)}</td>
                  <td className="num">{num(h.final_ecl)}</td>
                  <td className="num">{num(h.provision_movement)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="Accounting events" soft>
        {d.accounting_events.length === 0 ? (
          <EmptyState message="No ECL accounting events for this contract yet (emitted on run post)." />
        ) : (
          <table className="data" aria-label="Accounting events">
            <thead>
              <tr><th>#</th><th>Type</th><th className="num">Amount</th><th>Status</th><th>Date</th></tr>
            </thead>
            <tbody>
              {d.accounting_events.map((e) => (
                <tr key={e.id}>
                  <td>{e.id}</td>
                  <td>{e.event_type}</td>
                  <td className="num">{num(e.amount)}</td>
                  <td>{e.status}</td>
                  <td>{e.event_date?.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Configuration snapshot at calculation time" soft>
        <pre
          data-testid="ecl-config-snapshot"
          style={{ margin: 0, padding: "0.75rem", background: "var(--color-bg)", borderRadius: 6, fontSize: "0.78rem", overflowX: "auto" }}
        >
          {JSON.stringify(d.config_snapshot, null, 2)}
        </pre>
      </Card>
    </div>
  );
}
