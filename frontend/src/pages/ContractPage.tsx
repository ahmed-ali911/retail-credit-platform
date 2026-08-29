import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ContractOut, PaymentResult, ReceivableOut } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { Card, ErrorNote, Field, money } from "../components/ui";

export function ContractPage() {
  const { contractId } = useParams();
  const [contract, setContract] = useState<ContractOut | null>(null);
  const [receivable, setReceivable] = useState<ReceivableOut | null>(null);
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const c = await api<ContractOut>(`/contracts/${contractId}`);
      setContract(c);
      try {
        setReceivable(await api<ReceivableOut>(`/contracts/${contractId}/receivable`));
      } catch {
        setReceivable(null); // role may not permit the receivable view
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [contractId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function confirmDelivery() {
    setError(null);
    setBusy(true);
    try {
      await api<ContractOut>(`/contracts/${contractId}/confirm-delivery`, {
        method: "POST",
      });
      setNotice("Delivery confirmed — contract is now active.");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function recordPayment(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const res = await api<PaymentResult>(`/contracts/${contractId}/payments`, {
        method: "POST",
        body: { amount: Number(amount), external_reference: reference },
      });
      setNotice(
        res.replayed
          ? `Reference already recorded — no double allocation (payment #${res.payment.id}).`
          : `Payment #${res.payment.id} recorded; ${money(res.payment.allocated_amount)} allocated.`,
      );
      setAmount("");
      setReference("");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!contract) {
    return (
      <div className="stack">
        <h1>Contract</h1>
        <ErrorNote message={error} />
        {!error && <p className="muted">Loading…</p>}
      </div>
    );
  }

  const so = contract.sales_order;

  return (
    <div className="stack">
      <h1>
        Contract #{contract.id} <StatusBadge status={contract.status} />
      </h1>
      <ErrorNote message={error} />
      {notice && <div className="alert alert--info">{notice}</div>}

      <Card title="Sales order">
        <dl className="kv">
          <dt>Sales order #</dt>
          <dd>{so.id}</dd>
          <dt>Application #</dt>
          <dd>{so.application_id}</dd>
          <dt>Product #</dt>
          <dd>{so.product_id}</dd>
          <dt>Sale price</dt>
          <dd>{money(so.sale_price)}</dd>
          <dt>Down payment</dt>
          <dd>{money(so.down_payment_amount)}</dd>
          <dt>Total profit</dt>
          <dd>{money(contract.total_profit)}</dd>
          <dt>Unearned profit</dt>
          <dd>{money(contract.unearned_profit_balance)}</dd>
        </dl>
      </Card>

      <Card title="Delivery">
        {contract.status === "created" ? (
          <button className="btn-primary" onClick={confirmDelivery} disabled={busy}>
            Confirm delivery
          </button>
        ) : (
          <p className="muted">
            Delivered{" "}
            {contract.activated_at
              ? `on ${new Date(contract.activated_at).toLocaleString()}`
              : ""}
            .
          </p>
        )}
      </Card>

      {receivable && (
        <Card title="Receivable" soft>
          <dl className="kv">
            <dt>Outstanding principal</dt>
            <dd data-testid="rec-principal">{money(receivable.outstanding_principal)}</dd>
            <dt>Outstanding profit</dt>
            <dd data-testid="rec-profit">{money(receivable.outstanding_profit)}</dd>
            <dt>Outstanding late fees</dt>
            <dd data-testid="rec-latefees">{money(receivable.outstanding_late_fees)}</dd>
            <dt>Installments paid / remaining</dt>
            <dd>
              {receivable.total_installments_paid} /{" "}
              {receivable.total_installments_remaining}
            </dd>
          </dl>
        </Card>
      )}

      <Card title="Record payment">
        {contract.status === "active" ? (
          <form className="inline-form" onSubmit={recordPayment}>
            <Field
              label="Amount"
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
            <Field
              label="External reference"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="idempotency key"
              required
            />
            <button className="btn-primary" type="submit" disabled={busy}>
              Record payment
            </button>
          </form>
        ) : (
          <p className="muted">
            Payments can only be recorded against an <strong>active</strong> contract.
          </p>
        )}
      </Card>

      <Card title="Installments">
        <table className="data" aria-label="Contract installments">
          <thead>
            <tr>
              <th>#</th>
              <th>Due</th>
              <th className="num">Principal</th>
              <th className="num">Profit</th>
              <th className="num">Paid (P / Pr)</th>
              <th className="num">Total</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {contract.installments.map((i) => (
              <tr
                key={i.id}
                className={
                  i.status === "paid"
                    ? "is-paid"
                    : i.status === "overdue"
                      ? "is-overdue"
                      : undefined
                }
              >
                <td>{i.sequence_number}</td>
                <td>{i.due_date}</td>
                <td className="num">{money(i.principal_component)}</td>
                <td className="num">{money(i.profit_component)}</td>
                <td className="num">
                  {money(i.principal_paid)} / {money(i.profit_paid)}
                </td>
                <td className="num">{money(i.total_due)}</td>
                <td>
                  <StatusBadge status={i.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
