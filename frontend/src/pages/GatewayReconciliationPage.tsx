// Gateway Reconciliation Screen (Mock Payment Gateway feature) — a distinct
// screen from Bank Reconciliation (/reconciliation): this compares the
// separate mock-payment-gateway's own daily settlement feed against what
// this app recorded from its webhooks, not the company's bank statement.
// See app/models/gateway_settlement.py for the reuse-decision writeup.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type {
  GatewayReconciliationItemOut,
  ReconciliationOutcomeValue,
  SettlementBatchImportResult,
  SettlementBatchOut,
} from "../api/types";
import { Card, EmptyState, ErrorNote, Field, ResultSummary, money } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { FilterBar } from "../components/FilterBar";
import { SkeletonTable } from "../components/Skeleton";
import { StatusBadge } from "../components/StatusBadge";
import { useToast } from "../components/Toast";

const OUTCOME_FILTERS: ("" | ReconciliationOutcomeValue)[] = [
  "",
  "MATCHED",
  "MISSING_IN_GATEWAY",
  "MISSING_IN_INTERNAL_SYSTEM",
  "AMOUNT_MISMATCH",
  "STATUS_MISMATCH",
  "DUPLICATE",
  "DATE_MISMATCH",
  "UNRESOLVED",
];

export function GatewayReconciliationPage() {
  const toast = useToast();
  const [batches, setBatches] = useState<SettlementBatchOut[] | null>(null);
  const [items, setItems] = useState<GatewayReconciliationItemOut[] | null>(null);
  const [outcomeFilter, setOutcomeFilter] = useState<"" | ReconciliationOutcomeValue>("");
  const [statusFilter, setStatusFilter] = useState<"" | "open" | "resolved">("open");
  const [pullResult, setPullResult] = useState<SettlementBatchImportResult | null>(null);
  const [resolveFor, setResolveFor] = useState<number | null>(null);
  const [resolveForm, setResolveForm] = useState({ reason: "", comments: "" });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadBatches = useCallback(async () => {
    try {
      setBatches(await api<SettlementBatchOut[]>("/payments/settlement-batches"));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  const loadItems = useCallback(async () => {
    try {
      const qs = new URLSearchParams();
      if (outcomeFilter) qs.set("outcome", outcomeFilter);
      if (statusFilter) qs.set("status", statusFilter);
      setItems(
        await api<GatewayReconciliationItemOut[]>(
          `/payments/reconciliation-items${qs.toString() ? `?${qs}` : ""}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [outcomeFilter, statusFilter]);

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);
  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  async function pullBatch() {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const res = await api<SettlementBatchImportResult>(
        "/payments/settlement-batches/pull",
        { method: "POST" },
      );
      setPullResult(res);
      toast.success(
        `Pulled ${res.batch.batch_reference} — ${res.matched} matched, ${res.exceptions} exception(s).`,
      );
      await Promise.all([loadBatches(), loadItems()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function requestResolution(e: FormEvent, itemId: number) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await api(`/payments/reconciliation-items/${itemId}/resolve`, {
        method: "POST",
        body: { reason: resolveForm.reason, comments: resolveForm.comments || null },
      });
      setNotice(
        "Resolution requested — a different approver must approve it in Approvals " +
          "before it takes effect (a confirmed monetary variance books a " +
          "SETTLEMENT_DIFFERENCE accounting event on approval).",
      );
      setResolveFor(null);
      setResolveForm({ reason: "", comments: "" });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack page-wide">
      <PageHeader
        title="Gateway Reconciliation"
        description="Compare the payment gateway's daily settlement feed against this app's own records."
      />
      <ErrorNote message={error} />
      {notice && <div className="alert alert--info">{notice}</div>}

      <Card title="Pull today's settlement batch" soft>
        <p className="muted">
          Fetches the separate mock-payment-gateway's own daily feed (
          <code>GET /gateway/settlement-batches/generate</code>) and compares
          it line-for-line against this app's internal records — nothing is
          assumed, and a mismatch never silently self-corrects.
        </p>
        <button className="btn-primary" onClick={pullBatch} disabled={busy}>
          Pull today's batch
        </button>
        {pullResult && (
          <p className="alert alert--info" style={{ marginTop: "0.75rem" }} data-testid="pull-result">
            {pullResult.batch.batch_reference} — {pullResult.items_processed} processed ·{" "}
            {pullResult.matched} matched · {pullResult.exceptions} exception(s) ·{" "}
            {pullResult.missing_in_gateway} missing in gateway
          </p>
        )}
      </Card>

      <Card title="Settlement batches" soft>
        {batches == null ? (
          <SkeletonTable rows={3} cols={5} />
        ) : batches.length === 0 ? (
          <EmptyState message="No settlement batches imported yet." />
        ) : (
          <table className="data" aria-label="Settlement batches">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Date</th>
                <th className="num">Items</th>
                <th className="num">Gross</th>
                <th className="num">Fee</th>
                <th className="num">Net</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => (
                <tr key={b.id}>
                  <td>{b.batch_reference}</td>
                  <td>{b.settlement_date}</td>
                  <td className="num">{b.item_count}</td>
                  <td className="num">{money(b.total_gross_amount)}</td>
                  <td className="num">{money(b.total_gateway_fee)}</td>
                  <td className="num">{money(b.total_net_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Reconciliation items">
        <FilterBar>
          <label className="field" style={{ maxWidth: 220 }}>
            <span>Outcome</span>
            <select
              value={outcomeFilter}
              onChange={(e) => setOutcomeFilter(e.target.value as "" | ReconciliationOutcomeValue)}
            >
              {OUTCOME_FILTERS.map((o) => (
                <option key={o || "all"} value={o}>
                  {o || "All"}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ maxWidth: 180 }}>
            <span>Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as "" | "open" | "resolved")}
            >
              <option value="">All</option>
              <option value="open">Open</option>
              <option value="resolved">Resolved</option>
            </select>
          </label>
        </FilterBar>

        {items == null ? (
          <SkeletonTable rows={5} cols={7} />
        ) : items.length === 0 ? (
          <EmptyState testId="items-empty" message="No reconciliation items." />
        ) : (
          <>
            <ResultSummary total={items.length} shown={items.length} noun="item" />
            <table className="data" aria-label="Reconciliation items">
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Merchant ref</th>
                  <th className="num">Gross</th>
                  <th className="num">Variance</th>
                  <th>Outcome</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.id} data-testid={`recon-item-row-${it.id}`}>
                    <td className="num">{it.id}</td>
                    <td>{it.merchant_reference ?? "—"}</td>
                    <td className="num">{money(it.gross_amount)}</td>
                    <td className="num">{money(it.variance_amount)}</td>
                    <td>
                      <StatusBadge status={it.outcome} />
                    </td>
                    <td>
                      <StatusBadge status={it.status} />
                    </td>
                    <td>
                      {it.status === "open" && (
                        <button
                          className="btn-link"
                          onClick={() => setResolveFor(resolveFor === it.id ? null : it.id)}
                        >
                          Resolve
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {resolveFor != null && (
          <form
            className="inline-form"
            style={{ marginTop: "1rem" }}
            onSubmit={(e) => requestResolution(e, resolveFor)}
            aria-label={`Resolve reconciliation item ${resolveFor}`}
          >
            <Field
              label="Reason"
              value={resolveForm.reason}
              onChange={(e) => setResolveForm((f) => ({ ...f, reason: e.target.value }))}
              required
            />
            <Field
              label="Comments (optional)"
              value={resolveForm.comments}
              onChange={(e) => setResolveForm((f) => ({ ...f, comments: e.target.value }))}
            />
            <button className="btn-primary" type="submit" disabled={busy}>
              Submit resolution request
            </button>
          </form>
        )}
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          A requested resolution becomes a pending item in{" "}
          <Link to="/approvals">Approvals</Link> — a different approver
          decides it there.
        </p>
      </Card>
    </div>
  );
}
