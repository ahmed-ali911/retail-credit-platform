import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { EclConfigResponse } from "../api/types";
import { Card, EmptyState, ErrorNote } from "../components/ui";
import { SkeletonText } from "../components/Skeleton";
import { useToast } from "../components/Toast";

const MUTABLE_FIELDS = [
  "methodology",
  "rating_mapping",
  "risk_segments",
  "pd_term_structure",
  "lgd_model",
  "stage3_rules",
  "sicr_rules",
  "cure_rules",
  "override_rules",
  "accounting_mapping",
  "dpd_provision_pct",
  "lifetime_loss_rate",
];

export function EclConfigPage() {
  const toast = useToast();
  const [cfg, setCfg] = useState<EclConfigResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    void api<EclConfigResponse>("/ecl/config").then(setCfg).catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(load, [load]);

  async function propose(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    let changes: unknown;
    try {
      changes = JSON.parse(draft);
    } catch {
      setError("Changes must be valid JSON, e.g. {\"lifetime_loss_rate\": 0.12}");
      setBusy(false);
      return;
    }
    try {
      const r = await api<{ id: number }>("/ecl/config", {
        method: "PUT",
        body: { changes, notes: notes || undefined },
      });
      toast.success(`Configuration change proposed — approval #${r.id} pending a second approver.`);
      setDraft("");
      setNotes("");
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <p className="muted"><Link to="/ecl">← ECL &amp; Provision</Link></p>
      <h1>ECL Configuration</h1>
      <p className="muted">
        The ECL model calibration is an immutable, <strong>versioned</strong> record. A change
        activates a new version (maker-checker — a different user must approve). Past runs keep
        the version that produced them, so they stay reproducible. Every value is a placeholder —
        <strong> BUSINESS / RISK MODEL DECISION REQUIRED</strong>.
      </p>

      <ErrorNote message={error} />
      {!cfg && !error && <SkeletonText lines={8} />}

      {cfg && (
        <>
          <Card title={`Active configuration — v${cfg.active.ecl_config_version}`}>
            <div className="kv" data-testid="ecl-config-active">
              <dt>Methodology</dt><dd><code>{cfg.active.methodology}</code></dd>
              <dt>Lifetime loss rate (simplified path)</dt>
              <dd>{String(cfg.active.lifetime_loss_rate ?? "n/a")}</dd>
            </div>
            <pre
              style={{ margin: "0.75rem 0 0", padding: "0.75rem", background: "var(--color-bg)", borderRadius: 6, fontSize: "0.78rem", overflowX: "auto" }}
            >
              {JSON.stringify(cfg.active, null, 2)}
            </pre>
          </Card>

          <Card title="Version history">
            {cfg.versions.length === 0 ? (
              <EmptyState message="No configuration versions yet." />
            ) : (
              <table className="data" aria-label="Config versions">
                <thead>
                  <tr><th>Version</th><th>Active</th><th>Methodology</th><th>Notes</th><th>Activated</th></tr>
                </thead>
                <tbody>
                  {cfg.versions.map((v) => (
                    <tr key={v.version} data-testid={`ecl-config-version-${v.version}`}>
                      <td>v{v.version}</td>
                      <td>{v.is_active ? <span className="badge badge--warn">active</span> : "—"}</td>
                      <td><code>{v.methodology}</code></td>
                      <td>{v.notes ?? "—"}</td>
                      <td>{v.activated_at?.slice(0, 10) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Propose a new version (maker-checker)">
            <p className="muted">
              JSON object of changed fields only. Allowed: <code>{MUTABLE_FIELDS.join(", ")}</code>.
            </p>
            <form className="stack" onSubmit={propose}>
              <label className="field">
                <span>Changes (JSON)</span>
                <textarea
                  data-testid="ecl-config-changes"
                  rows={6}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder='{"lifetime_loss_rate": 0.12}'
                  style={{ fontFamily: "monospace", fontSize: "0.82rem" }}
                />
              </label>
              <label className="field">
                <span>Notes (optional)</span>
                <input value={notes} onChange={(e) => setNotes(e.target.value)} />
              </label>
              <button className="btn-primary" type="submit" disabled={busy || !draft.trim()}>
                {busy ? "Submitting…" : "Propose change"}
              </button>
            </form>
          </Card>
        </>
      )}
    </div>
  );
}
