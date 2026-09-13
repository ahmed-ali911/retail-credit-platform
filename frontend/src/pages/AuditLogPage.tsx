import { Fragment, useCallback, useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type { AuditEventOut } from "../api/types";
import { Card, EmptyState, ErrorNote, Field } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { FilterBar } from "../components/FilterBar";
import { SkeletonTable } from "../components/Skeleton";
import { auditEntityRef, coerceId } from "../lib/reference";

export function AuditLogPage() {
  const [events, setEvents] = useState<AuditEventOut[] | null>(null);
  const [filters, setFilters] = useState({ entity_type: "", entity_id: "", action: "" });
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

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
    <div className="stack page-wide">
      <PageHeader title="Audit Log" description="Every state change, filterable by entity and action." />
      <ErrorNote message={error} />

      <FilterBar
        as="form"
        onSubmit={apply}
        actions={
          <button className="btn-secondary" type="submit">
            Apply
          </button>
        }
      >
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
      </FilterBar>

      <Card>
        {events == null ? (
          <SkeletonTable rows={6} cols={6} />
        ) : events.length === 0 ? (
          <EmptyState testId="audit-empty" message="No events match." />
        ) : (
          <table className="data" aria-label="Audit events">
            <thead>
              <tr>
                <th className="num">Event</th>
                <th>Timestamp</th>
                <th>User</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => {
                const hasDetail = e.before_value != null || e.after_value != null;
                const isOpen = expanded === e.id;
                return (
                  <Fragment key={e.id}>
                    <tr data-testid={`audit-row-${e.id}`}>
                      <td className="num">{e.id}</td>
                      <td>{new Date(e.timestamp).toLocaleString()}</td>
                      <td>{e.user_id == null ? "system" : `user ${e.user_id}`}</td>
                      <td>{e.action}</td>
                      <td>{auditEntityRef(e.entity_type, e.entity_id)}</td>
                      <td>
                        {hasDetail ? (
                          <button
                            type="button"
                            className="btn-link"
                            data-testid={`audit-expand-${e.id}`}
                            onClick={() => setExpanded(isOpen ? null : e.id)}
                          >
                            {isOpen ? "Hide" : "View"}
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                    {isOpen && hasDetail && (
                      <tr>
                        <td />
                        <td colSpan={5}>
                          <div className="audit-detail" data-testid={`audit-detail-${e.id}`}>
                            {e.before_value != null && (
                              <div>
                                <span className="muted">Before</span>
                                <pre>{JSON.stringify(e.before_value, null, 2)}</pre>
                              </div>
                            )}
                            {e.after_value != null && (
                              <div>
                                <span className="muted">After</span>
                                <pre>{JSON.stringify(e.after_value, null, 2)}</pre>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
