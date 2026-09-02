import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, downloadFile, errorMessage } from "../api/client";
import type { CustomerListItem } from "../api/types";
import { Card, ErrorNote, Field } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

export function CustomerDirectoryPage() {
  const [term, setTerm] = useState("");
  const [rows, setRows] = useState<CustomerListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function search(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setRows(
        await api<CustomerListItem[]>(
          `/customers?search=${encodeURIComponent(term.trim())}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="stack">
      <h1>Customers</h1>
      <ErrorNote message={error} />

      <Card>
        <form className="inline-form" onSubmit={search}>
          <Field
            label="Search by name or national ID"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            required
          />
          <button className="btn-primary" type="submit">
            Search
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={!term.trim()}
            onClick={() =>
              downloadFile(
                `/customers?search=${encodeURIComponent(term.trim())}&format=csv`,
                "customers.csv",
              ).catch((err) => setError(errorMessage(err)))
            }
          >
            Export CSV
          </button>
        </form>
      </Card>

      {rows != null && (
        <Card>
          {rows.length === 0 ? (
            <p className="muted" data-testid="customers-empty">
              No customers match.
            </p>
          ) : (
            <table className="data" aria-label="Customer results">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>National ID</th>
                  <th>Status</th>
                  <th className="num">Risk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} data-testid={`customer-row-${c.id}`}>
                    <td>{c.id}</td>
                    <td>
                      <Link to={`/customers/${c.id}`}>{c.name}</Link>
                    </td>
                    <td>{c.national_id}</td>
                    <td>
                      <StatusBadge status={c.status} />
                    </td>
                    <td className="num">{c.risk_score ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  );
}
