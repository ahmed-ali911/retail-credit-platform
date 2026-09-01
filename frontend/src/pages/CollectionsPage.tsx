import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type {
  CollectionCaseDetailOut,
  CollectionCaseOut,
} from "../api/types";
import { Card, ErrorNote, Field, money } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

export function CollectionsPage() {
  const { user } = useAuth();
  const [rows, setRows] = useState<CollectionCaseOut[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | "open" | "closed">("");
  const [contractFilter, setContractFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    const qs = new URLSearchParams();
    if (statusFilter) qs.set("status", statusFilter);
    if (contractFilter) qs.set("contract_id", contractFilter);
    try {
      setRows(
        await api<CollectionCaseOut[]>(
          `/collections/cases${qs.toString() ? `?${qs}` : ""}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [statusFilter, contractFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runOverdue() {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const res = await api<{
        installments_marked_overdue: number;
        collection_cases_opened: number;
      }>("/jobs/assess-overdue", { method: "POST", body: {} });
      setNotice(
        `Overdue run complete — ${res.installments_marked_overdue} installment(s) marked overdue, ${res.collection_cases_opened} case(s) opened.`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Collections</h1>
      <ErrorNote message={error} />
      {notice && <div className="alert alert--info">{notice}</div>}

      {user?.role === "admin" && (
        <Card title="Overdue assessment" soft>
          <p className="muted">
            Manual utility — marks overdue installments, assesses late fees and
            opens cases. (No scheduler yet.)
          </p>
          <button className="btn-secondary" onClick={runOverdue} disabled={busy}>
            Run overdue assessment
          </button>
        </Card>
      )}

      <Card title="Cases">
        <div className="inline-form">
          <label className="field" style={{ maxWidth: 180 }}>
            <span>Status</span>
            <select
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(e.target.value as "" | "open" | "closed")
              }
            >
              <option value="">All</option>
              <option value="open">Open</option>
              <option value="closed">Closed</option>
            </select>
          </label>
          <Field
            label="Contract #"
            inputMode="numeric"
            value={contractFilter}
            onChange={(e) => setContractFilter(e.target.value)}
          />
        </div>

        {rows == null ? (
          <p className="muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="muted" data-testid="cases-empty">
            No collection cases.
          </p>
        ) : (
          <table className="data" aria-label="Collection cases">
            <thead>
              <tr>
                <th>#</th>
                <th>Contract #</th>
                <th>Status</th>
                <th>Opened</th>
                <th>Reason</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} data-testid={`case-row-${c.id}`}>
                  <td>{c.id}</td>
                  <td>{c.contract_id}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>{new Date(c.opened_at).toLocaleDateString()}</td>
                  <td>{c.opened_reason}</td>
                  <td>
                    <Link className="btn-link" to={`/collections/${c.id}`}>
                      Open →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

const ACTIVITY_TYPES = ["call", "sms", "email", "visit", "promise_to_pay", "other"] as const;

export function CollectionCasePage() {
  const { caseId } = useParams();
  const [detail, setDetail] = useState<CollectionCaseDetailOut | null>(null);
  const [form, setForm] = useState({
    activity_type: "call" as (typeof ACTIVITY_TYPES)[number],
    notes: "",
    promised_amount: "",
    promised_date: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDetail(
        await api<CollectionCaseDetailOut>(`/collections/cases/${caseId}`),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function logActivity(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const isPromise = form.activity_type === "promise_to_pay";
    try {
      await api(`/collections/cases/${caseId}/activities`, {
        method: "POST",
        body: {
          activity_type: form.activity_type,
          notes: form.notes || null,
          promised_amount: isPromise ? Number(form.promised_amount) : null,
          promised_date: isPromise ? form.promised_date : null,
        },
      });
      setForm({
        activity_type: "call",
        notes: "",
        promised_amount: "",
        promised_date: "",
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!detail) {
    return (
      <div className="stack">
        <h1>Collection case</h1>
        <ErrorNote message={error} />
        {!error && <p className="muted">Loading…</p>}
      </div>
    );
  }

  const isPromise = form.activity_type === "promise_to_pay";

  return (
    <div className="stack">
      <h1>
        Case #{detail.id} <StatusBadge status={detail.status} />
      </h1>
      <p>
        <Link className="btn-link" to="/collections">
          ← Back to cases
        </Link>
      </p>
      <ErrorNote message={error} />

      <Card title="Case">
        <dl className="kv">
          <dt>Contract #</dt>
          <dd>{detail.contract_id}</dd>
          <dt>Opened</dt>
          <dd>{new Date(detail.opened_at).toLocaleString()}</dd>
          <dt>Reason</dt>
          <dd>{detail.opened_reason}</dd>
          <dt>Closed</dt>
          <dd>
            {detail.closed_at
              ? new Date(detail.closed_at).toLocaleString()
              : "—"}
          </dd>
        </dl>
      </Card>

      <Card title="Log activity">
        <form className="stack" onSubmit={logActivity}>
          <label className="field">
            <span>Activity type</span>
            <select
              value={form.activity_type}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  activity_type: e.target
                    .value as (typeof ACTIVITY_TYPES)[number],
                }))
              }
            >
              {ACTIVITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Notes</span>
            <textarea
              rows={2}
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            />
          </label>
          {isPromise && (
            <div className="field-row" data-testid="promise-fields">
              <Field
                label="Promised amount"
                inputMode="decimal"
                value={form.promised_amount}
                onChange={(e) =>
                  setForm((f) => ({ ...f, promised_amount: e.target.value }))
                }
                required
              />
              <Field
                label="Promised date"
                type="date"
                value={form.promised_date}
                onChange={(e) =>
                  setForm((f) => ({ ...f, promised_date: e.target.value }))
                }
                required
              />
            </div>
          )}
          <button className="btn-primary" type="submit" disabled={busy}>
            Log activity
          </button>
        </form>
      </Card>

      <Card title="Activity history">
        {detail.activities.length === 0 ? (
          <p className="muted">No activity yet.</p>
        ) : (
          <table className="data" aria-label="Activity history">
            <thead>
              <tr>
                <th>When</th>
                <th>Type</th>
                <th>Notes</th>
                <th>Promise</th>
              </tr>
            </thead>
            <tbody>
              {detail.activities.map((a) => (
                <tr key={a.id} data-testid={`activity-row-${a.id}`}>
                  <td>{new Date(a.created_at).toLocaleString()}</td>
                  <td>{a.activity_type.replace(/_/g, " ")}</td>
                  <td>{a.notes ?? "—"}</td>
                  <td>
                    {a.promised_amount != null
                      ? `${money(a.promised_amount)} by ${a.promised_date} (${a.promise_status})`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
