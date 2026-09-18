import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { ApprovalRequestOut } from "../api/types";
import { Card, EmptyState, ErrorNote } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { Tabs } from "../components/Tabs";
import { ConfirmationDialog } from "../components/ConfirmationDialog";
import { SkeletonTable } from "../components/Skeleton";
import { useToast } from "../components/Toast";
import { formatReference } from "../lib/reference";

const STATUS_TABS = [
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
] as const;
type StatusTab = (typeof STATUS_TABS)[number]["value"];

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
    case "ecl.stage_override":
      return `ECL: force ${formatReference(
        "InstallmentContract",
        Number(p.contract_id ?? req.entity_id),
      )} to Stage ${p.requested_stage} (auto Stage ${p.automated_stage}) — ${
        p.reason_code
      }; ECL ${p.ecl_before} → ${p.ecl_after}`;
    case "ecl.parameter_override":
      return `ECL: pin ${Object.entries(p.approved_value ?? {})
        .map(([k, v]) => `${k}=${v}`)
        .join(", ")} on ${formatReference(
        "InstallmentContract",
        Number(p.contract_id ?? req.entity_id),
      )} — ${p.reason_code}; ECL ${p.ecl_before} → ${p.ecl_after}`;
    case "ecl.config_update":
      return `ECL config: activate a new version from v${p.from_version} — ${JSON.stringify(
        p.changes,
      )}`;
    case "writeoff.request": {
      const total = p.requested_total as number | undefined;
      const exception = p.is_exception ? ` — EXCEPTION (${p.eligibility_status})` : "";
      return `${p.write_off_type} write-off on ${formatReference(
        "InstallmentContract",
        Number(p.contract_id ?? req.entity_id),
      )}: ${total != null ? total : "?"} — ${p.reason_code}${exception}`;
    }
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
  const toast = useToast();
  const [tab, setTab] = useState<StatusTab>("pending");
  const [rows, setRows] = useState<ApprovalRequestOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmAction, setConfirmAction] =
    useState<{ id: number; action: "approve" | "reject" } | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setRows(null);
    try {
      setRows(await api<ApprovalRequestOut[]>(`/approvals?status=${tab}`));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [tab]);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(id: number, action: "approve" | "reject") {
    setError(null);
    setBusyId(id);
    try {
      await api(`/approvals/${id}/${action}`, { method: "POST" });
      toast.success(
        `Request #${id} ${action === "approve" ? "approved" : "rejected"}.`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stack page-wide">
      <PageHeader
        title="Approvals"
        description="Maker-checker requests. You cannot decide a request you made yourself — a different approver is required."
      />
      <ErrorNote message={error} />

      <Card>
        <Tabs ariaLabel="Approval status" items={[...STATUS_TABS]} value={tab} onChange={(v) => setTab(v as StatusTab)} />
        <div style={{ marginTop: "1rem" }}>
        {rows == null ? (
          <SkeletonTable rows={4} cols={6} />
        ) : rows.length === 0 ? (
          <EmptyState
            testId="approvals-empty"
            message={`No ${tab} requests.`}
          />
        ) : (
          <table className="data" aria-label={`${tab} approvals`}>
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Request type</th>
                <th>Summary</th>
                <th>Requested by</th>
                <th>Requested at</th>
                {tab !== "pending" && <th>Decided by / at</th>}
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((req) => {
                const mine = user?.id === req.requested_by;
                return (
                  <tr key={req.id} data-testid={`approval-row-${req.id}`}>
                    <td className="num">{req.id}</td>
                    <td>{req.action_type}</td>
                    <td>{payloadSummary(req)}</td>
                    <td>user #{req.requested_by}</td>
                    <td>{new Date(req.requested_at).toLocaleString()}</td>
                    {tab !== "pending" && (
                      <td>
                        {req.decided_by != null
                          ? `user #${req.decided_by} · ${new Date(req.decided_at!).toLocaleString()}`
                          : "—"}
                        {req.decision_notes && (
                          <div className="muted">{req.decision_notes}</div>
                        )}
                      </td>
                    )}
                    <td>
                      {tab !== "pending" ? null : mine ? (
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
                            onClick={() => setConfirmAction({ id: req.id, action: "approve" })}
                          >
                            Approve
                          </button>
                          <button
                            className="btn-secondary"
                            data-testid={`reject-${req.id}`}
                            disabled={busyId === req.id}
                            onClick={() => setConfirmAction({ id: req.id, action: "reject" })}
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
        </div>
      </Card>

      <ConfirmationDialog
        open={confirmAction != null}
        title={
          confirmAction?.action === "approve"
            ? `Approve request #${confirmAction.id}?`
            : `Reject request #${confirmAction?.id}?`
        }
        confirmLabel={confirmAction?.action === "approve" ? "Approve" : "Reject"}
        destructive={confirmAction?.action === "reject"}
        busy={busyId === confirmAction?.id}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => {
          if (!confirmAction) return;
          const { id, action } = confirmAction;
          setConfirmAction(null);
          void decide(id, action);
        }}
      >
        {confirmAction && (
          <>
            <strong>{rows?.find((r) => r.id === confirmAction.id)?.action_type}</strong>
            {" — "}
            {rows?.find((r) => r.id === confirmAction.id) &&
              payloadSummary(rows.find((r) => r.id === confirmAction.id)!)}
          </>
        )}
      </ConfirmationDialog>
    </div>
  );
}
