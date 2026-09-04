import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type { ContractReportPage } from "../api/types";
import { Card, ErrorNote, RefCode, money } from "../components/ui";
import { SearchSelect } from "../components/SearchSelect";
import { StatusBadge } from "../components/StatusBadge";
import { coerceId } from "../lib/reference";

/**
 * Contracts Directory (Step 15, Part C) — a day-to-day lookup screen, same
 * pattern as the Customer/Product directories (Step 10). NOT a duplicate of
 * Reports → Contracts (a filtered export tool): this reuses that exact same
 * `GET /reports/contracts` query — including the `contract_id` filter added
 * this step so a reference-code search doesn't need a second endpoint.
 */
export function ContractDirectoryPage() {
  const [refTerm, setRefTerm] = useState("");
  const [customerTerm, setCustomerTerm] = useState("");
  const [page, setPage] = useState<ContractReportPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const qs = new URLSearchParams();
    const ref = coerceId(refTerm);
    if (ref) qs.set("contract_id", ref);
    const cust = coerceId(customerTerm);
    if (cust) qs.set("customer_id", cust);
    try {
      setPage(
        await api<ContractReportPage>(
          `/reports/contracts${qs.toString() ? `?${qs}` : ""}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [refTerm, customerTerm]);

  // full (first-page) list on load, same convention as Customers/Products
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function search(e: FormEvent) {
    e.preventDefault();
    void load();
  }

  return (
    <div className="stack">
      <h1>Contracts</h1>
      <p className="muted">
        Day-to-day lookup. For filtered exports (status, date range, category)
        see <strong>Reports → Contracts</strong> instead.
      </p>
      <ErrorNote message={error} />

      <Card>
        <form className="inline-form" onSubmit={search}>
          <label className="field" style={{ maxWidth: 220 }}>
            <span>Contract reference (CN-code)</span>
            <input
              value={refTerm}
              onChange={(e) => setRefTerm(e.target.value)}
              placeholder="CN-000012 or a raw id"
              data-testid="contract-directory-ref-input"
            />
          </label>
          <SearchSelect
            label="Customer"
            kind="customer"
            value={customerTerm}
            onChange={setCustomerTerm}
            placeholder="Search by name, or paste an id / CU-code"
          />
          <button className="btn-primary" type="submit">
            Search
          </button>
        </form>
      </Card>

      {page && (
        <Card>
          {page.items.length === 0 ? (
            <p className="muted" data-testid="contracts-directory-empty">
              No contracts match.
            </p>
          ) : (
            <>
              <p className="muted" data-testid="contracts-directory-count">
                {page.total} contract{page.total === 1 ? "" : "s"}
                {page.items.length < page.total
                  ? ` — showing ${page.items.length}`
                  : ""}
              </p>
              <table className="data" aria-label="Contract results">
                <thead>
                  <tr>
                    <th>Reference</th>
                    <th>Customer</th>
                    <th>Product</th>
                    <th>Status</th>
                    <th className="num">Outstanding</th>
                    <th>Next due</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r) => (
                    <tr key={r.contract_id} data-testid={`contract-directory-row-${r.contract_id}`}>
                      <td>
                        <RefCode
                          entity="InstallmentContract"
                          id={r.contract_id}
                          to={`/contracts/${r.contract_id}`}
                        />
                      </td>
                      <td>{r.customer_name}</td>
                      <td>{r.product_name}</td>
                      <td>
                        <StatusBadge status={r.status} />
                      </td>
                      <td className="num">{money(r.outstanding_total)}</td>
                      <td>{r.next_due_date ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Card>
      )}
    </div>
  );
}
