import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ContractReportPage, CustomerExposure, CustomerOut } from "../api/types";
import { Card, ErrorNote, RefCode, money } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

export function CustomerPage() {
  const { customerId } = useParams();
  const [customer, setCustomer] = useState<CustomerOut | null>(null);
  const [exposure, setExposure] = useState<CustomerExposure | null>(null);
  const [history, setHistory] = useState<ContractReportPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exposureError, setExposureError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

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
      try {
        // Part B — full contract history (every status, not just contracts
        // with an outstanding balance). Reuses the same GET /reports/contracts
        // query the Contracts Directory and Reports → Contracts use — no
        // second contract-listing query.
        setHistory(
          await api<ContractReportPage>(
            `/reports/contracts?customer_id=${customerId}&limit=200`,
          ),
        );
      } catch (err) {
        setHistoryError(errorMessage(err));
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
        {customer.name}{" "}
        <span className="muted">
          <RefCode code={customer.reference_code} />
        </span>
      </h1>
      <ErrorNote message={error} />

      {/* Part B — every field already in the API response, grouped instead of
          one flat list. Nothing here is a new field; profile fields simply
          weren't rendered before. */}
      <Card title="Personal">
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
          <dt>Address</dt>
          <dd>{customer.profile?.address_line ?? "—"}</dd>
          <dt>City</dt>
          <dd>{customer.profile?.city ?? "—"}</dd>
        </dl>
      </Card>

      <Card title="Employment">
        <dl className="kv">
          <dt>Employer</dt>
          <dd>{customer.profile?.employer_name ?? "—"}</dd>
          <dt>Employment type</dt>
          <dd>
            {customer.profile?.employment_type
              ? customer.profile.employment_type.replace(/_/g, " ")
              : "—"}
          </dd>
          <dt>Contact phone</dt>
          <dd>{customer.profile?.contact_phone ?? "—"}</dd>
        </dl>
      </Card>

      <Card title="Financial">
        <dl className="kv">
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
              <p className="muted">
                No open contracts — see full history below.
              </p>
            ) : (
              <table
                className="data"
                aria-label="Per-contract exposure"
                style={{ marginTop: "0.75rem" }}
              >
                <thead>
                  <tr>
                    <th>Contract</th>
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
                        <RefCode
                          entity="InstallmentContract"
                          id={c.contract_id}
                          to={`/contracts/${c.contract_id}`}
                        />
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

      {/* Part B — every contract regardless of status, so a customer whose
          only contract is fully paid and closed shows their real history
          instead of "no open contracts". */}
      <Card title="Contract history" soft>
        {historyError ? (
          <p className="muted">{historyError}</p>
        ) : !history ? (
          <p className="muted">Loading…</p>
        ) : history.items.length === 0 ? (
          <p className="muted" data-testid="history-empty">
            No contracts on record.
          </p>
        ) : (
          <table className="data" aria-label="Full contract history">
            <thead>
              <tr>
                <th>Contract</th>
                <th>Product</th>
                <th>Status</th>
                <th className="num">Sale price</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {history.items.map((r) => (
                <tr key={r.contract_id} data-testid={`history-row-${r.contract_id}`}>
                  <td>
                    <RefCode
                      entity="InstallmentContract"
                      id={r.contract_id}
                      to={`/contracts/${r.contract_id}`}
                    />
                  </td>
                  <td>{r.product_name}</td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="num">{money(r.installment_sale_price)}</td>
                  <td>{new Date(r.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
