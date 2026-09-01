import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type {
  ApprovalRequestOut,
  CollectionCaseOut,
  ReconciliationStatus,
} from "../api/types";
import { Card, ErrorNote } from "../components/ui";

// Accounting events reused from Step G — minimal shape.
interface AccountingEvent {
  accounting_status: "pending" | "posted" | "failed";
}

export function SnapshotPage() {
  const [recon, setRecon] = useState<ReconciliationStatus | null>(null);
  const [acct, setAcct] = useState<Record<string, number> | null>(null);
  const [approvals, setApprovals] = useState<number | null>(null);
  const [openCases, setOpenCases] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setRecon(await api<ReconciliationStatus>("/reconciliation/status"));
      } catch (err) {
        setError(errorMessage(err));
      }
      try {
        const events = await api<AccountingEvent[]>("/accounting/events");
        const by = { pending: 0, posted: 0, failed: 0 } as Record<string, number>;
        for (const e of events) by[e.accounting_status] = (by[e.accounting_status] ?? 0) + 1;
        setAcct(by);
      } catch {
        setAcct(null);
      }
      try {
        const pend = await api<ApprovalRequestOut[]>("/approvals?status=pending");
        setApprovals(pend.length);
      } catch {
        setApprovals(null);
      }
      try {
        const cases = await api<CollectionCaseOut[]>("/collections/cases?status=open");
        setOpenCases(cases.length);
      } catch {
        setOpenCases(null);
      }
    })();
  }, []);

  return (
    <div className="stack">
      <h1>Portfolio Snapshot</h1>
      <p className="muted" data-testid="snapshot-disclaimer">
        A snapshot of current counts assembled from data that already exists —{" "}
        <strong>not</strong> a reporting / KPI platform. Proper reporting needs
        its own design pass (see the Gap Matrix).
      </p>
      <ErrorNote message={error} />

      <Card title="Reconciliation health" soft>
        {recon ? (
          <dl className="kv">
            <dt>Unreconciled payments</dt>
            <dd>{recon.unreconciled_payments}</dd>
            <dt>Reconciled payments</dt>
            <dd>{recon.reconciled_payments}</dd>
            <dt>Open exceptions</dt>
            <dd data-testid="snap-open-exceptions">{recon.open_exceptions}</dd>
            <dt>Unmatched bank lines</dt>
            <dd>{recon.unmatched_bank_lines}</dd>
          </dl>
        ) : (
          <p className="muted">—</p>
        )}
      </Card>

      <Card title="Accounting events by status" soft>
        {acct ? (
          <dl className="kv">
            <dt>Pending</dt>
            <dd data-testid="snap-acct-pending">{acct.pending}</dd>
            <dt>Posted</dt>
            <dd data-testid="snap-acct-posted">{acct.posted}</dd>
            <dt>Failed</dt>
            <dd data-testid="snap-acct-failed">{acct.failed}</dd>
          </dl>
        ) : (
          <p className="muted">—</p>
        )}
      </Card>

      <Card title="Pending approvals" soft>
        <p>
          <strong data-testid="snap-approvals">{approvals ?? "—"}</strong> pending{" "}
          <Link to="/approvals">request(s)</Link>
        </p>
      </Card>

      <Card title="Open collection cases" soft>
        <p>
          <strong data-testid="snap-open-cases">{openCases ?? "—"}</strong> open{" "}
          <Link to="/collections">case(s)</Link>
        </p>
      </Card>
    </div>
  );
}
