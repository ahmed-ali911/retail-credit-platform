import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type {
  ApplicationOut,
  ContractOut,
  PaymentResult,
  ReceivableOut,
  SettleResult,
  SettlementQuoteOut,
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { Card, ErrorNote, Field, RefCode, money } from "../components/ui";

export function ContractPage() {
  const { contractId } = useParams();
  const [contract, setContract] = useState<ContractOut | null>(null);
  const [receivable, setReceivable] = useState<ReceivableOut | null>(null);
  const [origination, setOrigination] = useState<ApplicationOut | null>(null);
  const [originationError, setOriginationError] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [quote, setQuote] = useState<SettlementQuoteOut | null>(null);
  const [settleRef, setSettleRef] = useState("");
  const [rebatePct, setRebatePct] = useState("");

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
      try {
        // Part B — "Origination" section: channel and who created the sale
        // already exist on the originating application, just weren't shown
        // here. Degrades gracefully if the viewer's role can't read it.
        setOrigination(
          await api<ApplicationOut>(
            `/applications/${c.sales_order.application_id}`,
          ),
        );
      } catch (err) {
        setOriginationError(errorMessage(err));
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
          ? `Reference already recorded — no double allocation (payment ${res.payment.reference_code}).`
          : `Payment ${res.payment.reference_code} recorded; ${money(res.payment.allocated_amount)} allocated.`,
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

  /** The rebate the staff member typed, as a 0–1 fraction, or null if blank/0. */
  function rebateFraction(): number | null {
    const n = Number(rebatePct);
    if (!rebatePct.trim() || Number.isNaN(n) || n <= 0) return null;
    return n / 100;
  }

  async function getQuote() {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const frac = rebateFraction();
      const qs = frac != null ? `?requested_rebate_pct=${frac}` : "";
      setQuote(
        await api<SettlementQuoteOut>(
          `/contracts/${contractId}/settlement-quote${qs}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirmSettlement() {
    if (!quote) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const frac = rebateFraction();
      const res = await api<SettleResult>(`/contracts/${contractId}/settle`, {
        method: "POST",
        body: {
          amount: quote.final_payoff_amount,
          external_reference: settleRef,
          ...(frac != null ? { requested_rebate_pct: frac } : {}),
        },
      });
      if (res.status === "pending_approval") {
        setNotice(
          "Settlement with a profit rebate requested — a different approver " +
            "must approve it in Approvals before it takes effect. Nothing has " +
            "changed on the contract yet.",
        );
      } else {
        setNotice("Contract settled and closed.");
      }
      setQuote(null);
      setSettleRef("");
      setRebatePct("");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function closeAction(action: "cancel" | "return") {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await api(`/contracts/${contractId}/${action}`, { method: "POST", body: {} });
      setNotice(action === "cancel" ? "Contract cancelled." : "Product return recorded — contract closed.");
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
        Contract <RefCode code={contract.reference_code} />{" "}
        <StatusBadge status={contract.status} />
      </h1>
      <ErrorNote message={error} />
      {notice && <div className="alert alert--info">{notice}</div>}

      <Card title="Sales order">
        <dl className="kv">
          <dt>Sales order</dt>
          <dd><RefCode code={so.reference_code} /></dd>
          <dt>Application</dt>
          <dd><RefCode code={so.application_reference} /></dd>
          <dt>Product</dt>
          <dd><RefCode code={so.product_reference} /></dd>
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

      {/* Part B — channel and who created the sale already exist on the
          originating application; just weren't surfaced on this screen. */}
      <Card title="Origination" soft>
        {originationError ? (
          <p className="muted">{originationError}</p>
        ) : !origination ? (
          <p className="muted">Loading…</p>
        ) : (
          <dl className="kv">
            <dt>Channel</dt>
            <dd data-testid="origination-channel">{origination.channel}</dd>
            <dt>Created by</dt>
            <dd data-testid="origination-created-by">{origination.created_by}</dd>
            <dt>Application created</dt>
            <dd>{new Date(origination.created_at).toLocaleString()}</dd>
          </dl>
        )}
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

      <Card title="Closure">
        {contract.closure ? (
          <div data-testid="closure-info">
            <p>
              This contract is closed —{" "}
              <StatusBadge status={contract.closure.reason} />
            </p>
            <dl className="kv">
              <dt>Reason</dt>
              <dd>{contract.closure.reason.replace(/_/g, " ")}</dd>
              <dt>Financial adjustment</dt>
              <dd data-testid="closure-adjustment">
                {contract.closure.financial_adjustment == null
                  ? "—"
                  : money(contract.closure.financial_adjustment)}
              </dd>
            </dl>
            {contract.closure.notes && (
              <p className="muted">{contract.closure.notes}</p>
            )}
          </div>
        ) : (
          <div className="stack">
            {contract.status === "active" && (
              <form
                className="inline-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void getQuote();
                }}
              >
                <Field
                  label="Profit rebate % (optional)"
                  inputMode="decimal"
                  value={rebatePct}
                  onChange={(e) => setRebatePct(e.target.value)}
                  placeholder="0"
                  data-testid="rebate-pct"
                />
                <button
                  className="btn-secondary"
                  type="submit"
                  disabled={busy}
                  data-testid="get-quote"
                >
                  Get settlement quote
                </button>
              </form>
            )}

            {quote && (
              <div data-testid="settlement-quote">
                <dl className="kv">
                  <dt>Outstanding principal</dt>
                  <dd>{money(quote.outstanding_principal)}</dd>
                  <dt>Outstanding late fees</dt>
                  <dd>{money(quote.outstanding_late_fees)}</dd>
                  <dt>Unearned profit</dt>
                  <dd>{money(quote.unearned_profit_total)}</dd>
                  <dt>Profit rebate</dt>
                  <dd>
                    {money(quote.profit_rebate_amount)} (
                    {(quote.profit_rebate_pct * 100).toFixed(0)}%)
                  </dd>
                  <dt>Profit still charged</dt>
                  <dd>{money(quote.profit_still_charged)}</dd>
                  <dt>Final payoff amount</dt>
                  <dd>
                    <strong>{money(quote.final_payoff_amount)}</strong>
                  </dd>
                </dl>

                {quote.is_deviation && (
                  <div className="alert alert--info" data-testid="rebate-deviation-note">
                    This rebate deviates from the 0% default and{" "}
                    <strong>
                      requires approval from a different user before it takes
                      effect
                    </strong>
                    . Confirming will create a pending request in{" "}
                    <Link to="/approvals">Approvals</Link> — the contract is not
                    settled until a different {`credit_manager`}/finance_officer/admin
                    approves it.
                  </div>
                )}

                <form
                  className="inline-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void confirmSettlement();
                  }}
                >
                  <Field
                    label="Settlement reference"
                    value={settleRef}
                    onChange={(e) => setSettleRef(e.target.value)}
                    required
                  />
                  <button
                    className="btn-primary"
                    type="submit"
                    disabled={busy}
                    data-testid="confirm-settlement"
                  >
                    {quote.is_deviation
                      ? "Request settlement (needs approval)"
                      : "Confirm settlement"}
                  </button>
                </form>
              </div>
            )}

            {contract.status === "created" && (
              <div>
                <button
                  className="btn-secondary"
                  onClick={() => closeAction("cancel")}
                  disabled={busy}
                  data-testid="cancel-contract"
                >
                  Cancel contract
                </button>
              </div>
            )}

            {contract.status === "active" && (
              <div>
                <button
                  className="btn-secondary"
                  onClick={() => closeAction("return")}
                  disabled={busy}
                  data-testid="return-product"
                >
                  Return product
                </button>
              </div>
            )}
          </div>
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
