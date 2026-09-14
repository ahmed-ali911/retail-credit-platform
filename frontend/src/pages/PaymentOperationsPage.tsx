// Payment Operations Dashboard (Mock Payment Gateway feature) — the staff
// view across every PaymentIntent, whatever contract it belongs to. A
// companion to the per-contract "Pay via Payment Gateway" card on
// ContractPage; this is the cross-contract operational list.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { PaymentIntentOut, PaymentIntentStatusValue } from "../api/types";
import { Card, EmptyState, ErrorNote, RefCode, ResultSummary, money } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { FilterBar } from "../components/FilterBar";
import { MetricGrid, MetricTile } from "../components/MetricTile";
import { SkeletonTable } from "../components/Skeleton";
import { StatusBadge } from "../components/StatusBadge";

const STATUS_FILTERS: ("" | PaymentIntentStatusValue)[] = [
  "",
  "INITIATED",
  "PENDING",
  "AUTHORIZED",
  "CAPTURED",
  "SETTLED",
  "FAILED",
  "CANCELLED",
  "EXPIRED",
  "REVERSED",
  "PARTIALLY_REFUNDED",
  "REFUNDED",
  "CHARGEBACK",
];

// Which statuses count as "settled money" vs "still in flight" vs "did not
// complete" — purely a display grouping, no business logic of its own.
const SETTLED = new Set(["SETTLED"]);
const IN_FLIGHT = new Set(["INITIATED", "PENDING", "AUTHORIZED", "CAPTURED"]);
const DID_NOT_COMPLETE = new Set(["FAILED", "CANCELLED", "EXPIRED"]);
const REVERSED_LIKE = new Set(["REVERSED", "PARTIALLY_REFUNDED", "REFUNDED", "CHARGEBACK"]);

export function PaymentOperationsPage() {
  const [rows, setRows] = useState<PaymentIntentOut[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | PaymentIntentStatusValue>("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const q = statusFilter ? `?status=${statusFilter}&limit=100` : "?limit=100";
      setRows(await api<PaymentIntentOut[]>(`/payments/intents${q}`));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = rows
    ? {
        settled: rows.filter((r) => SETTLED.has(r.status)).length,
        inFlight: rows.filter((r) => IN_FLIGHT.has(r.status)).length,
        didNotComplete: rows.filter((r) => DID_NOT_COMPLETE.has(r.status)).length,
        reversedLike: rows.filter((r) => REVERSED_LIKE.has(r.status)).length,
      }
    : null;

  return (
    <div className="stack page-wide">
      <PageHeader
        title="Payment Operations"
        description="Every payment intent through the Mock Payment Gateway, across every contract."
      />
      <ErrorNote message={error} />

      {counts && (
        <MetricGrid>
          <MetricTile label="Settled" value={counts.settled} tone="good" />
          <MetricTile label="In flight" value={counts.inFlight} tone="warn" />
          <MetricTile label="Did not complete" value={counts.didNotComplete} tone="neutral" />
          <MetricTile label="Reversed / refunded" value={counts.reversedLike} tone="bad" />
        </MetricGrid>
      )}

      <Card title="Payment intents">
        <FilterBar>
          <label className="field" style={{ maxWidth: 220 }}>
            <span>Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as "" | PaymentIntentStatusValue)}
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
          <SkeletonTable rows={6} cols={6} />
        ) : rows.length === 0 ? (
          <EmptyState testId="intents-empty" message="No payment intents yet." />
        ) : (
          <>
            <ResultSummary total={rows.length} shown={rows.length} noun="payment intent" />
            <table className="data" aria-label="Payment intents">
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Contract</th>
                  <th>Purpose</th>
                  <th className="num">Amount</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} data-testid={`intent-row-${r.id}`}>
                    <td>{r.payment_reference}</td>
                    <td>
                      <RefCode code={r.contract_reference} to={`/contracts/${r.contract_id}`} />
                    </td>
                    <td>{r.payment_purpose.replace(/_/g, " ")}</td>
                    <td className="num">{money(r.requested_amount)}</td>
                    <td>
                      <StatusBadge status={r.status} />
                    </td>
                    <td>{new Date(r.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          Open a contract's page for the full transaction timeline of one
          payment, or the{" "}
          <Link to="/payments/reconciliation">Gateway Reconciliation</Link>{" "}
          screen to compare settled payments against the gateway's own daily
          feed.
        </p>
      </Card>
    </div>
  );
}
