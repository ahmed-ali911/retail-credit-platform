import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type { AuditEventOut } from "../api/types";
import { Card, ErrorNote, Field } from "../components/ui";
import { auditEntityRef, coerceId } from "../lib/reference";

export function AuditLogPage() {
  const [events, setEvents] = useState<AuditEventOut[] | null>(null);
  const [filters, setFilters] = useState({ entity_type: "", entity_id: "", action: "" });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const qs = new URLSearchParams();
    if (filters.entity_type) qs.set("entity_type", filters.entity_type);
    if (filters.entity_id)
      qs.set("entity_id", coerceId(filters.entity_id) || filters.entity_id);
    if (filters.action) qs.set("action", filters.action);
    const suffix = qs.toString() ? `?${qs}` : "";
    try {
      setEvents(await api<AuditEventOut[]>(`/audit/events${suffix}`));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  function apply(e: FormEvent) {
    e.preventDefault();
    void load();
  }

  return (
    <div className="stack">
      <h1>Audit Log</h1>
      <ErrorNote message={error} />

      <Card title="Filters">
        <form className="inline-form" onSubmit={apply}>
          <Field
            label="Entity type"
            value={filters.entity_type}
            onChange={(e) =>
              setFilters((f) => ({ ...f, entity_type: e.target.value }))
            }
            placeholder="e.g. installment_contract"
          />
          <Field
            label="Entity id (or reference code)"
            value={filters.entity_id}
            onChange={(e) =>
              setFilters((f) => ({ ...f, entity_id: e.target.value }))
            }
          />
          <Field
            label="Action"
            value={filters.action}
            onChange={(e) =>
              setFilters((f) => ({ ...f, action: e.target.value }))
            }
            placeholder="e.g. contract.settled"
          />
          <button className="btn-secondary" type="submit">
            Apply
          </button>
        </form>
      </Card>

      <Card>
        {events == null ? (
          <p className="muted">Loading…</p>
        ) : events.length === 0 ? (
          <p className="muted" data-testid="audit-empty">
            No events match.
          </p>
        ) : (
          <table className="data" aria-label="Audit events">
            <thead>
              <tr>
                <th>Event</th>
                <th>When</th>
                <th>User</th>
                <th>Action</th>
                <th>Entity</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} data-testid={`audit-row-${e.id}`}>
                  <td className="num">{e.id}</td>
                  <td>{new Date(e.timestamp).toLocaleString()}</td>
                  <td>{e.user_id == null ? "system" : `user ${e.user_id}`}</td>
                  <td>{e.action}</td>
                  <td>{auditEntityRef(e.entity_type, e.entity_id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
