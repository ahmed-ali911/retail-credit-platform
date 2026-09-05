import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { ApprovalRequestOut } from "../api/types";
import { Card, ErrorNote } from "../components/ui";
import { formatReference } from "../lib/reference";

function payloadSummary(req: ApprovalRequestOut): string {
  const p = req.payload ?? {};
  switch (req.action_type) {
    case "config.update":
      return `set ${req.entity_id} → ${JSON.stringify(p.new_value)}`;
    case "late_fee.waive":
      return `waive late fee #${req.entity_id}${p.reason ? ` — ${p.reason}` : ""}`;
    case "reconciliation.manual_match":
      return `match bank exception #${req.entity_id} → payment ${formatReference(
        "Payment",
        p.payment_id as number,
      )}${p.reason ? ` — ${p.reason}` : ""}`;
    case "contract.settlement_rebate": {
      const pct = p.requested_rebate_pct as number | null;
      const amt = p.requested_rebate_amount as number | null;
      const grant =
        pct != null ? `${(pct * 100).toFixed(0)}%` : amt != null ? String(amt) : "?";
      return `early-settle ${formatReference(
        "InstallmentContract",
        Number(req.entity_id),
      )} with a ${grant} profit rebate`;
    }
    default: {
      const parts = Object.entries(p).map(([k, v]) => `${k}=${JSON.stringify(v)}`);
      return parts.length ? parts.join(", ") : `${req.entity_type} #${req.entity_id}`;
    }
  }
}

export function ApprovalsPage() {
  const { user } = useAuth();
  const [rows, setRows] = useState<ApprovalRequestOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRows(
        await api<ApprovalRequestOut[]>("/approvals?status=pending"),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(id: number, action: "approve" | "reject") {
    setError(null);
    setNotice(null);
    setBusyId(id);
    try {
      await api(`/approvals/${id}/${action}`, { method: "POST" });
      setNotice(`Request #${id} ${action === "approve" ? "approved" : "rejected"}.`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stack">
      <h1>Approvals</h1>
      <p className="muted">
        Pending maker-checker requests. You cannot decide a request you made
        yourself — a different approver is required.
      </p>
      <ErrorNote message={error} />
      {notice && <div className="alert alert--info">{notice}</div>}

      <Card>
        {rows == null ? (
          <p className="muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="muted" data-testid="approvals-empty">
            No pending requests.
          </p>
        ) : (
          <table className="data" aria-label="Pending approvals">
            <thead>
              <tr>
                <th>#</th>
                <th>Action</th>
                <th>Summary</th>
                <th>Requested by</th>
                <th>Requested at</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((req) => {
                const mine = user?.id === req.requested_by;
                return (
                  <tr key={req.id} data-testid={`approval-row-${req.id}`}>
                    <td>{req.id}</td>
                    <td>{req.action_type}</td>
                    <td>{payloadSummary(req)}</td>
                    <td>user #{req.requested_by}</td>
                    <td>{new Date(req.requested_at).toLocaleString()}</td>
                    <td>
                      {mine ? (
                        <span
                          className="muted"
                          data-testid={`approval-blocked-${req.id}`}
                        >
                          you requested this — a different approver is required
                        </span>
                      ) : (
                        <span style={{ display: "inline-flex", gap: "0.4rem" }}>
                          <button
                            className="btn-primary"
                            data-testid={`approve-${req.id}`}
                            disabled={busyId === req.id}
                            onClick={() => decide(req.id, "approve")}
                          >
                            Approve
                          </button>
                          <button
                            className="btn-secondary"
                            data-testid={`reject-${req.id}`}
                            disabled={busyId === req.id}
                            onClick={() => decide(req.id, "reject")}
                          >
                            Reject
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
