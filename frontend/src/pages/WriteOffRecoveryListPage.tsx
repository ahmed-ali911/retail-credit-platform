// Write-offs & Recoveries — a simple, filterable list across every contract
// (not a duplicate of Collections; this is the write-off REQUEST/EXECUTION
// lifecycle specifically). The detailed review/approve/execute/recovery
// actions live on the Contract page's WriteOffCard and on the generic
// Approvals screen — this list exists to find an account, not to re-host
// those actions.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { WriteOffRequestOut, WriteOffRequestStatusValue } from "../api/types";
import { Card, EmptyState, ErrorNote, RefCode, ResultSummary, money } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { FilterBar } from "../components/FilterBar";
import { MetricGrid, MetricTile } from "../components/MetricTile";
import { SkeletonTable } from "../components/Skeleton";
import { StatusBadge } from "../components/StatusBadge";

const STATUS_FILTERS: ("" | WriteOffRequestStatusValue)[] = [
  "", "PENDING", "APPROVED", "REJECTED", "EXECUTED", "CANCELLED",
];

function requestTotal(r: WriteOffRequestOut): number {
  return r.requested_principal + r.requested_profit + r.requested_late_fee + r.requested_other_charges;
}

export function WriteOffRecoveryListPage() {
  const [rows, setRows] = useState<WriteOffRequestOut[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | WriteOffRequestStatusValue>("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const q = statusFilter ? `?status=${statusFilter}` : "";
      setRows(await api<WriteOffRequestOut[]>(`/write-offs/requests${q}`));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = rows
    ? {
        pending: rows.filter((r) => r.status === "PENDING").length,
        approved: rows.filter((r) => r.status === "APPROVED").length,
        executed: rows.filter((r) => r.status === "EXECUTED").length,
        writtenOffTotal: rows
          .filter((r) => r.status === "EXECUTED")
          .reduce((sum, r) => sum + requestTotal(r), 0),
      }
    : null;

  return (
    <div className="stack page-wide">
      <PageHeader
        title="Write-offs & Recoveries"
        description="Every write-off request across the portfolio. Review, approve, execute and record recoveries from the contract's own page."
      />
      <ErrorNote message={error} />

      {counts && (
        <MetricGrid>
          <MetricTile label="Pending approval" value={counts.pending} tone="warn" />
          <MetricTile label="Approved, awaiting execution" value={counts.approved} tone="warn" />
          <MetricTile label="Executed" value={counts.executed} tone="neutral" />
          <MetricTile label="Total written off (executed)" value={money(counts.writtenOffTotal)} tone="bad" />
        </MetricGrid>
      )}

      <Card title="Write-off requests">
        <FilterBar>
          <label className="field" style={{ maxWidth: 220 }}>
            <span>Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as "" | WriteOffRequestStatusValue)}
            >
              {STATUS_FILTERS.map((s) => (
                <option key={s || "all"} value={s}>
                  {s || "All"}
                </option>
              ))}
            </select>
          </label>
        </FilterBar>

        {rows == null ? (
          <SkeletonTable rows={6} cols={7} />
        ) : rows.length === 0 ? (
          <EmptyState testId="writeoffs-empty" message="No write-off requests yet." />
        ) : (
          <>
            <ResultSummary total={rows.length} shown={rows.length} noun="request" />
            <table className="data" aria-label="Write-off requests">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Contract</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Eligibility</th>
                  <th className="num">Requested total</th>
                  <th>Reason</th>
                  <th>Requested</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} data-testid={`writeoff-list-row-${r.id}`}>
                    <td>{r.id}</td>
                    <td>
                      <RefCode entity="InstallmentContract" id={r.contract_id} to={`/contracts/${r.contract_id}`} />
                    </td>
                    <td>{r.write_off_type}</td>
                    <td>
                      <StatusBadge status={r.status} />
                    </td>
                    <td>
                      <StatusBadge status={r.eligibility_status} />
                      {r.is_exception && <span className="muted"> · exception</span>}
                    </td>
                    <td className="num">{money(requestTotal(r))}</td>
                    <td>{r.reason_code}</td>
                    <td>{new Date(r.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          Pending requests are approved or rejected from{" "}
          <Link to="/approvals">Approvals</Link> — a different user than the
          requester. Open a contract's own page to execute an approved
          request or record a recovery.
        </p>
      </Card>
    </div>
  );
}
