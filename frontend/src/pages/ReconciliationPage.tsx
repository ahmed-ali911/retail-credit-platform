import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage, uploadFile } from "../api/client";
import type {
  BankLineUploadResult,
  MatchRunResult,
  ReconciliationException,
  ReconciliationStatus,
} from "../api/types";
import { Card, ErrorNote, Field } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

export function ReconciliationPage() {
  const [status, setStatus] = useState<ReconciliationStatus | null>(null);
  const [exceptions, setExceptions] = useState<ReconciliationException[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | "open" | "resolved">("");
  const [line, setLine] = useState({ bank_reference: "", amount: "", value_date: "" });
  const [runResult, setRunResult] = useState<MatchRunResult | null>(null);
  const [uploadResult, setUploadResult] = useState<BankLineUploadResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [matchFor, setMatchFor] = useState<number | null>(null);
  const [matchForm, setMatchForm] = useState({ payment_id: "", reason: "" });

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api<ReconciliationStatus>("/reconciliation/status"));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  const loadExceptions = useCallback(async () => {
    try {
      const q = statusFilter ? `?status=${statusFilter}` : "";
      setExceptions(
        await api<ReconciliationException[]>(`/reconciliation/exceptions${q}`),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [statusFilter]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);
  useEffect(() => {
    void loadExceptions();
  }, [loadExceptions]);

  async function addLine(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await api("/reconciliation/bank-lines", {
        method: "POST",
        body: {
          bank_reference: line.bank_reference,
          amount: Number(line.amount),
          value_date: line.value_date,
        },
      });
      setNotice(`Bank line "${line.bank_reference}" recorded.`);
      setLine({ bank_reference: "", amount: "", value_date: "" });
      await loadStatus();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function uploadStatement(e: FormEvent) {
    e.preventDefault();
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    setError(null);
    setNotice(null);
    setUploadResult(null);
    setUploading(true);
    try {
      const res = await uploadFile<BankLineUploadResult>(
        "/reconciliation/bank-lines/upload",
        file,
      );
      setUploadResult(res);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadStatus();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  async function runMatching() {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const res = await api<MatchRunResult>("/reconciliation/run", {
        method: "POST",
      });
      setRunResult(res);
      await Promise.all([loadStatus(), loadExceptions()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function requestMatch(e: FormEvent, exceptionId: number) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await api(`/reconciliation/exceptions/${exceptionId}/request-match`, {
        method: "POST",
        body: {
          payment_id: Number(matchForm.payment_id),
          reason: matchForm.reason,
        },
      });
      setNotice(
        "Match requested — a different approver must approve it in Approvals.",
      );
      setMatchFor(null);
      setMatchForm({ payment_id: "", reason: "" });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Bank Reconciliation</h1>
      <ErrorNote message={error} />
      {notice && <div className="alert alert--info">{notice}</div>}

      <Card title="Status" soft>
        {!status ? (
          <p className="muted">Loading…</p>
        ) : (
          <dl className="kv">
            <dt>Unreconciled payments</dt>
            <dd data-testid="st-unreconciled">{status.unreconciled_payments}</dd>
            <dt>Reconciled payments</dt>
            <dd data-testid="st-reconciled">{status.reconciled_payments}</dd>
            <dt>Payments in exception</dt>
            <dd data-testid="st-exception">{status.exception_payments}</dd>
            <dt>Open exceptions</dt>
            <dd data-testid="st-open-exceptions">{status.open_exceptions}</dd>
            <dt>Unmatched bank lines</dt>
            <dd data-testid="st-unmatched-lines">{status.unmatched_bank_lines}</dd>
          </dl>
        )}
      </Card>

      <Card title="Add bank line">
        <form className="inline-form" onSubmit={addLine}>
          <Field
            label="Bank reference"
            value={line.bank_reference}
            onChange={(e) => setLine((l) => ({ ...l, bank_reference: e.target.value }))}
            required
          />
          <Field
            label="Amount"
            inputMode="decimal"
            value={line.amount}
            onChange={(e) => setLine((l) => ({ ...l, amount: e.target.value }))}
            required
          />
          <Field
            label="Value date"
            type="date"
            value={line.value_date}
            onChange={(e) => setLine((l) => ({ ...l, value_date: e.target.value }))}
            required
          />
          <button className="btn-primary" type="submit" disabled={busy}>
            Add bank line
          </button>
        </form>
      </Card>

      <Card title="Upload bank statement (.xlsx)" soft>
        <p className="muted">
          Bulk alternative to the single-line form above — doesn't replace it.
          Expected columns: <code>bank_reference</code>, <code>amount</code>,{" "}
          <code>value_date</code> (any order, case-insensitive header, extra
          columns ignored — see the README for the exact layout).
        </p>
        <form className="inline-form" onSubmit={uploadStatement}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx"
            data-testid="statement-upload-input"
          />
          <button className="btn-primary" type="submit" disabled={uploading}>
            {uploading ? "Uploading…" : "Upload"}
          </button>
        </form>
        {uploadResult && (
          <div
            className="alert alert--info"
            style={{ marginTop: "0.75rem" }}
            data-testid="upload-result"
          >
            <p>
              Processed {uploadResult.rows_processed} · ingested{" "}
              {uploadResult.rows_ingested} · matched {uploadResult.matched} ·
              exceptions created {uploadResult.exceptions_created} · rejected{" "}
              {uploadResult.rows_rejected}
            </p>
            {uploadResult.rejected.length > 0 && (
              <ul>
                {uploadResult.rejected.map((r) => (
                  <li key={r.row} data-testid={`upload-rejected-${r.row}`}>
                    Row {r.row}: {r.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      <Card title="Run matching">
        <button className="btn-primary" onClick={runMatching} disabled={busy}>
          Run matching
        </button>
        {runResult && (
          <p className="alert alert--info" style={{ marginTop: "0.75rem" }} data-testid="run-result">
            Processed {runResult.lines_processed} · matched {runResult.matched} ·
            exceptions created {runResult.exceptions_created}
          </p>
        )}
      </Card>

      <Card title="Exceptions">
        <label className="field" style={{ maxWidth: 220 }}>
          <span>Filter by status</span>
          <select
            value={statusFilter}
            onChange={(e) =>
              setStatusFilter(e.target.value as "" | "open" | "resolved")
            }
          >
            <option value="">All</option>
            <option value="open">Open</option>
            <option value="resolved">Resolved</option>
          </select>
        </label>

        {exceptions.length === 0 ? (
          <p className="muted">No exceptions.</p>
        ) : (
          <table className="data" aria-label="Reconciliation exceptions">
            <thead>
              <tr>
                <th>#</th>
                <th>Bank line #</th>
                <th>Reason</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {exceptions.map((ex) => (
                <tr key={ex.id} data-testid={`exc-row-${ex.id}`}>
                  <td>{ex.id}</td>
                  <td>{ex.bank_line_id}</td>
                  <td>{ex.reason.replace(/_/g, " ")}</td>
                  <td>
                    <StatusBadge status={ex.status} />
                  </td>
                  <td>
                    {ex.status === "open" && (
                      <button
                        className="btn-link"
                        onClick={() =>
                          setMatchFor(matchFor === ex.id ? null : ex.id)
                        }
                      >
                        Request match
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {matchFor != null && (
          <form
            className="inline-form"
            style={{ marginTop: "1rem" }}
            onSubmit={(e) => requestMatch(e, matchFor)}
            aria-label={`Request match for exception ${matchFor}`}
          >
            <Field
              label="Target payment #"
              inputMode="numeric"
              value={matchForm.payment_id}
              onChange={(e) =>
                setMatchForm((f) => ({ ...f, payment_id: e.target.value }))
              }
              required
            />
            <Field
              label="Reason"
              value={matchForm.reason}
              onChange={(e) =>
                setMatchForm((f) => ({ ...f, reason: e.target.value }))
              }
              required
            />
            <button className="btn-primary" type="submit" disabled={busy}>
              Submit request
            </button>
          </form>
        )}
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          A requested match becomes a pending item in{" "}
          <Link to="/approvals">Approvals</Link> — a different approver resolves
          it there.
        </p>
      </Card>
    </div>
  );
}
