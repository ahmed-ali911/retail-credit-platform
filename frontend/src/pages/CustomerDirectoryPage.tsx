import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, downloadFile, errorMessage } from "../api/client";
import type { CustomerListItem } from "../api/types";
import { Card, ErrorNote, Field } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

type StatusFilter = "all" | "active" | "inactive";

export function CustomerDirectoryPage() {
  const [term, setTerm] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [rows, setRows] = useState<CustomerListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useCallback(() => {
    const p = new URLSearchParams();
    if (term.trim()) p.set("search", term.trim());
    if (status !== "all") p.set("status", status);
    return p.toString();
  }, [term, status]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const q = query();
      setRows(
        await api<CustomerListItem[]>(`/customers${q ? `?${q}` : ""}`),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [query]);

  // full list on page load; re-load whenever the status filter changes
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  function onSearch(e: FormEvent) {
    e.preventDefault();
    void load();
  }

  return (
    <div className="stack">
      <h1>Customers</h1>
      <ErrorNote message={error} />

      <Card>
        <form className="inline-form" onSubmit={onSearch}>
          <Field
            label="Search by name or national ID"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
          />
          <label className="field">
            <span>Status</span>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as StatusFilter)}
            >
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
          <button className="btn-primary" type="submit">
            Search
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              const q = query();
              downloadFile(
                `/customers${q ? `?${q}&` : "?"}format=csv`,
                "customers.csv",
              ).catch((err) => setError(errorMessage(err)));
            }}
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
