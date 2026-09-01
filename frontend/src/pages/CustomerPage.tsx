import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { CustomerExposure, CustomerOut } from "../api/types";
import { Card, ErrorNote, money } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

export function CustomerPage() {
  const { customerId } = useParams();
  const [customer, setCustomer] = useState<CustomerOut | null>(null);
  const [exposure, setExposure] = useState<CustomerExposure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exposureError, setExposureError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      setError(null);
      try {
        setCustomer(await api<CustomerOut>(`/customers/${customerId}`));
      } catch (err) {
        setError(errorMessage(err));
      }
      try {
        setExposure(
          await api<CustomerExposure>(`/customers/${customerId}/exposure`),
        );
      } catch (err) {
        setExposureError(errorMessage(err));
      }
    })();
  }, [customerId]);

  if (!customer) {
    return (
      <div className="stack">
        <h1>Customer</h1>
        <ErrorNote message={error} />
        {!error && <p className="muted">Loading…</p>}
      </div>
    );
  }

  return (
    <div className="stack">
      <h1>
        {customer.name} <span className="muted">#{customer.id}</span>
      </h1>
      <ErrorNote message={error} />

      <Card title="Details">
        <dl className="kv">
          <dt>National ID</dt>
          <dd>{customer.national_id}</dd>
          <dt>Status</dt>
          <dd>
            <StatusBadge status={customer.status} />
          </dd>
          <dt>Risk score</dt>
          <dd>{customer.risk_score ?? "—"}</dd>
          <dt>Phone</dt>
          <dd>{customer.phone ?? "—"}</dd>
          <dt>Email</dt>
          <dd>{customer.email ?? "—"}</dd>
          <dt>Monthly income</dt>
          <dd>{money(customer.profile?.monthly_income)}</dd>
          <dt>Existing obligations</dt>
          <dd>{money(customer.profile?.existing_monthly_obligations)}</dd>
        </dl>
      </Card>

      <Card title="Exposure" soft>
        {exposureError ? (
          <p className="muted">{exposureError}</p>
        ) : !exposure ? (
          <p className="muted">Loading…</p>
        ) : (
          <>
            <dl className="kv">
              <dt>Aggregation level</dt>
              <dd>{exposure.aggregation_level}</dd>
              <dt>Total outstanding</dt>
              <dd data-testid="exposure-total">
                {money(exposure.total_outstanding)}
              </dd>
            </dl>
            {exposure.contracts.length === 0 ? (
              <p className="muted">No open contracts.</p>
            ) : (
              <table
                className="data"
                aria-label="Per-contract exposure"
                style={{ marginTop: "0.75rem" }}
              >
                <thead>
                  <tr>
                    <th>Contract #</th>
                    <th>Status</th>
                    <th className="num">Principal</th>
                    <th className="num">Profit</th>
                    <th className="num">Late fees</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {exposure.contracts.map((c) => (
                    <tr key={c.contract_id} data-testid={`exposure-row-${c.contract_id}`}>
                      <td>
                        <Link to={`/contracts/${c.contract_id}`}>
                          {c.contract_id}
                        </Link>
                      </td>
                      <td>
                        <StatusBadge status={c.status} />
                      </td>
                      <td className="num">{money(c.outstanding_principal)}</td>
                      <td className="num">{money(c.outstanding_profit)}</td>
                      <td className="num">{money(c.outstanding_late_fees)}</td>
                      <td className="num">{money(c.outstanding_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
