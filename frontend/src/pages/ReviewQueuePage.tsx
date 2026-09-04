import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ApplicationListItem, ApplicationOut } from "../api/types";
import { AssessmentPanel } from "../components/AssessmentPanel";
import { StatusBadge } from "../components/StatusBadge";
import { Card, ErrorNote, RefCode, money } from "../components/ui";

export function ReviewQueuePage() {
  const [rows, setRows] = useState<ApplicationListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    void (async () => {
      try {
        setRows(
          await api<ApplicationListItem[]>("/applications?status=referred"),
        );
      } catch (err) {
        setError(errorMessage(err));
      }
    })();
  }, []);

  return (
    <div className="stack">
      <h1>Review Queue</h1>
      <p className="muted">
        Applications the automated engine sent to <strong>referred</strong> —
        awaiting a manual credit decision.
      </p>
      <ErrorNote message={error} />

      <Card>
        {rows == null ? (
          <p className="muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="muted" data-testid="review-empty">
            Nothing in the queue — no referred applications.
          </p>
        ) : (
          <table className="data" aria-label="Referred applications">
            <thead>
              <tr>
                <th>Application</th>
                <th>Customer</th>
                <th>Product</th>
                <th className="num">Requested</th>
                <th>Submitted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} data-testid={`review-row-${r.id}`}>
                  <td>
                    <RefCode code={r.reference_code} to={`/review/${r.id}`} />
                  </td>
                  <td>
                    <RefCode
                      entity="Customer"
                      id={r.customer_id}
                      to={`/customers/${r.customer_id}`}
                    />
                  </td>
                  <td><RefCode entity="Product" id={r.product_id} /></td>
                  <td className="num">{money(r.requested_amount)}</td>
                  <td>{new Date(r.submitted_at).toLocaleDateString()}</td>
                  <td>
                    <button
                      className="btn-link"
                      onClick={() => navigate(`/review/${r.id}`)}
                    >
                      Review →
                    </button>
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

const DECISIONS = [
  { value: "approved", label: "Approve" },
  { value: "rejected", label: "Reject" },
  { value: "return_for_info", label: "Return for Info" },
] as const;

export function ReviewApplicationPage() {
  const { applicationId } = useParams();
  const [app, setApp] = useState<ApplicationOut | null>(null);
  const [decision, setDecision] =
    useState<(typeof DECISIONS)[number]["value"]>("approved");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ApplicationOut | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setApp(await api<ApplicationOut>(`/applications/${applicationId}`));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api<ApplicationOut>(
        `/applications/${applicationId}/review`,
        { method: "POST", body: { decision, reason } },
      );
      setOutcome(res);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!app) {
    return (
      <div className="stack">
        <h1>Review application</h1>
        <ErrorNote message={error} />
        {!error && <p className="muted">Loading…</p>}
      </div>
    );
  }

  const automated =
    app.assessments.find((a) => a.source === "automated") ??
    app.latest_assessment;
  const done = outcome != null;

  return (
    <div className="stack">
      <h1>
        Review application <RefCode code={app.reference_code} />{" "}
        <StatusBadge status={done ? outcome!.status : app.status} />
      </h1>
      <p>
        <Link className="btn-link" to="/review">
          ← Back to queue
        </Link>
      </p>
      <ErrorNote message={error} />

      <Card title="Application">
        <dl className="kv">
          <dt>Customer</dt>
          <dd>
            <RefCode
              entity="Customer"
              id={app.customer_id}
              to={`/customers/${app.customer_id}`}
            />
          </dd>
          <dt>Product</dt>
          <dd><RefCode entity="Product" id={app.product_id} /></dd>
          <dt>Requested amount</dt>
          <dd>{money(app.requested_amount)}</dd>
          <dt>Requested tenor</dt>
          <dd>{app.requested_tenor_months} months</dd>
          <dt>Channel</dt>
          <dd>{app.channel}</dd>
        </dl>
      </Card>

      <Card title="Automated assessment">
        {automated ? (
          <AssessmentPanel assessment={automated} />
        ) : (
          <p className="muted">No automated assessment on record.</p>
        )}
      </Card>

      {done ? (
        <Card soft title="Decision recorded">
          <p>
            Application is now <StatusBadge status={outcome!.status} />.
          </p>
          {outcome!.status === "approved" && (
            <p style={{ marginTop: "0.75rem" }}>
              <Link
                className="btn-link"
                to={`/applications/${outcome!.id}/offer`}
              >
                Generate an offer →
              </Link>
            </p>
          )}
          {outcome!.status === "draft" && (
            <p className="muted">
              Returned for info — the customer/branch resubmits through the
              normal application flow.
            </p>
          )}
        </Card>
      ) : app.status !== "referred" ? (
        <Card soft>
          <p className="muted">
            This application is no longer awaiting review (status:{" "}
            {app.status}).
          </p>
        </Card>
      ) : (
        <Card title="Manual decision">
          <form className="stack" onSubmit={submit}>
            <label className="field">
              <span>Decision</span>
              <select
                value={decision}
                onChange={(e) =>
                  setDecision(
                    e.target.value as (typeof DECISIONS)[number]["value"],
                  )
                }
              >
                {DECISIONS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Reason</span>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                rows={3}
              />
            </label>
            <button className="btn-primary" type="submit" disabled={busy}>
              {busy ? "Submitting…" : "Submit decision"}
            </button>
          </form>
        </Card>
      )}
    </div>
  );
}
