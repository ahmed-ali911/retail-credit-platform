// Customer Payment Screen (Mock Payment Gateway feature) — lets staff start a
// gateway payment on a contract's behalf: shows the same options a customer
// would see (next installment, overdue, total outstanding, minimum partial),
// opens a PaymentIntent + checkout session on the SEPARATE mock-payment-gateway
// service, and hands the staff member a link to the gateway's own hosted
// "Demo Gateway" checkout page — this app never renders that page itself
// (it belongs to the gateway, a genuinely separate service; see
// mock-payment-gateway/README.md).
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type {
  CheckoutSessionOut,
  PaymentIntentOut,
  PaymentOptionsOut,
  PaymentStatusOut,
} from "../api/types";
import { Card, ErrorNote, Field, SelectField, money } from "./ui";
import { SkeletonText } from "./Skeleton";
import { StatusBadge } from "./StatusBadge";
import { useToast } from "./Toast";

const PURPOSES = [
  { value: "current_installment", label: "Current installment" },
  { value: "overdue_amount", label: "Overdue amount" },
  { value: "full_outstanding", label: "Full outstanding" },
  { value: "partial", label: "Partial amount" },
] as const;
type Purpose = (typeof PURPOSES)[number]["value"];

export function GatewayPaymentCard({
  contractId,
  onSettled,
}: {
  contractId: number;
  onSettled?: () => void;
}) {
  const toast = useToast();
  const [options, setOptions] = useState<PaymentOptionsOut | null>(null);
  const [history, setHistory] = useState<PaymentIntentOut[] | null>(null);
  const [purpose, setPurpose] = useState<Purpose>("current_installment");
  const [partialAmount, setPartialAmount] = useState("");
  const [intent, setIntent] = useState<PaymentIntentOut | null>(null);
  const [checkout, setCheckout] = useState<CheckoutSessionOut | null>(null);
  const [statusDetail, setStatusDetail] = useState<PaymentStatusOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadOptions = useCallback(async () => {
    try {
      setOptions(await api<PaymentOptionsOut>(`/contracts/${contractId}/payment-options`));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [contractId]);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(
        await api<PaymentIntentOut[]>(`/payments/intents?contract_id=${contractId}&limit=10`),
      );
    } catch {
      setHistory([]); // role may not permit the operations list — degrade quietly
    }
  }, [contractId]);

  useEffect(() => {
    void loadOptions();
    void loadHistory();
  }, [loadOptions, loadHistory]);

  async function startPayment(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        contract_id: contractId,
        payment_purpose: purpose,
        idempotency_key: `gw-${contractId}-${Date.now()}`,
      };
      if (purpose === "partial") body.amount = Number(partialAmount);

      const newIntent = await api<PaymentIntentOut>("/payments/intents", {
        method: "POST",
        body,
      });
      const co = await api<CheckoutSessionOut>(
        `/payments/intents/${newIntent.id}/checkout`,
        { method: "POST" },
      );
      setIntent(newIntent);
      setCheckout(co);
      setStatusDetail(null);
      toast.success(`Payment ${newIntent.payment_reference} — checkout session opened.`);
      await loadHistory();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function refreshStatus(reference: string) {
    setError(null);
    setBusy(true);
    try {
      const detail = await api<PaymentStatusOut>(`/payments/${reference}/status`);
      setStatusDetail(detail);
      await loadHistory();
      await loadOptions();
      onSettled?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Pay via Payment Gateway">
      <p className="muted">
        Entirely simulated — the customer is redirected to the separate Demo
        Gateway's hosted checkout page; no real card, bank, or payment
        credentials are ever collected here.
      </p>
      <ErrorNote message={error} />

      {!options ? (
        <SkeletonText lines={3} />
      ) : (
        <dl className="kv" data-testid="payment-options">
          <dt>Next installment</dt>
          <dd>
            {options.next_installment_amount != null
              ? `${money(options.next_installment_amount)} due ${options.next_installment_due_date}`
              : "—"}
          </dd>
          <dt>Overdue amount</dt>
          <dd data-testid="opt-overdue">{money(options.overdue_amount)}</dd>
          <dt>Late fees outstanding</dt>
          <dd>{money(options.late_fees_outstanding)}</dd>
          <dt>Total outstanding</dt>
          <dd data-testid="opt-total">{money(options.total_outstanding)}</dd>
          <dt>Minimum acceptable payment</dt>
          <dd>{money(options.minimum_partial_amount)}</dd>
        </dl>
      )}

      {options && (
        <form className="inline-form" onSubmit={startPayment}>
          <SelectField
            label="Amount to pay"
            value={purpose}
            onChange={(e) => setPurpose(e.target.value as Purpose)}
          >
            {PURPOSES.map((p) => {
              const disabled =
                (p.value === "current_installment" && !options.can_pay_current_installment) ||
                (p.value === "overdue_amount" && !options.can_pay_overdue) ||
                (p.value === "full_outstanding" && !options.can_pay_full_outstanding) ||
                (p.value === "partial" && !options.can_pay_partial);
              return (
                <option key={p.value} value={p.value} disabled={disabled}>
                  {p.label}
                </option>
              );
            })}
          </SelectField>
          {purpose === "partial" && (
            <Field
              label={`Amount (min ${money(options.minimum_partial_amount)})`}
              inputMode="decimal"
              value={partialAmount}
              onChange={(e) => setPartialAmount(e.target.value)}
              required
            />
          )}
          <button className="btn-primary" type="submit" disabled={busy}>
            Start gateway payment
          </button>
        </form>
      )}

      {checkout && intent && (
        <div className="alert alert--info" data-testid="gateway-checkout-panel">
          <p>
            Payment <strong>{intent.payment_reference}</strong> — checkout
            session open, expires{" "}
            {checkout.expires_at ? new Date(checkout.expires_at).toLocaleTimeString() : "—"}.
          </p>
          <p>
            <a
              className="btn-primary"
              href={checkout.checkout_url}
              target="_blank"
              rel="noreferrer"
              data-testid="open-checkout-link"
            >
              Open Demo Gateway checkout ↗
            </a>{" "}
            <button
              type="button"
              className="btn-secondary"
              onClick={() => refreshStatus(intent.payment_reference)}
              disabled={busy}
            >
              Refresh status
            </button>
          </p>
        </div>
      )}

      {statusDetail && (
        <div data-testid="payment-status-timeline">
          <p>
            <StatusBadge status={statusDetail.status} /> for{" "}
            <strong>{statusDetail.payment_reference}</strong>
          </p>
          {statusDetail.transactions.length > 0 && (
            <table className="data" aria-label="Gateway transaction timeline">
              <thead>
                <tr>
                  <th>Gateway status</th>
                  <th className="num">Authorized</th>
                  <th className="num">Captured</th>
                  <th className="num">Settled</th>
                  <th className="num">Fee</th>
                </tr>
              </thead>
              <tbody>
                {statusDetail.transactions.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <StatusBadge status={t.gateway_status} />
                    </td>
                    <td className="num">{money(t.authorized_amount)}</td>
                    <td className="num">{money(t.captured_amount)}</td>
                    <td className="num">{money(t.settled_amount)}</td>
                    <td className="num">{money(t.gateway_fee)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {history && history.length > 0 && (
        <div style={{ marginTop: "1rem" }}>
          <h3 style={{ fontSize: "0.85rem", marginBottom: "0.4rem" }}>
            Payment intent history
          </h3>
          <table className="data" aria-label="Payment intent history">
            <thead>
              <tr>
                <th>Reference</th>
                <th>Purpose</th>
                <th className="num">Amount</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id} data-testid={`intent-row-${h.id}`}>
                  <td>{h.payment_reference}</td>
                  <td>{h.payment_purpose.replace(/_/g, " ")}</td>
                  <td className="num">{money(h.requested_amount)}</td>
                  <td>
                    <StatusBadge status={h.status} />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn-link"
                      onClick={() => refreshStatus(h.payment_reference)}
                      disabled={busy}
                    >
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
