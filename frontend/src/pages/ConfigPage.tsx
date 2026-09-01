import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ConfigParameterOut } from "../api/types";
import { Card, ErrorNote } from "../components/ui";

export function ConfigPage() {
  const [params, setParams] = useState<ConfigParameterOut[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setParams(await api<ConfigParameterOut[]>("/config/parameters"));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function startEdit(p: ConfigParameterOut) {
    setEditing(p.key);
    setValue(p.value);
    setPending(null);
    setError(null);
  }

  function coerce(raw: string, type: string): unknown {
    if (type === "int") return Number.parseInt(raw, 10);
    if (type === "float") return Number.parseFloat(raw);
    if (type === "bool") return raw === "true" || raw === "1";
    if (type === "json") return JSON.parse(raw);
    return raw;
  }

  async function submit(e: FormEvent, p: ConfigParameterOut) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api(`/config/parameters/${p.key}`, {
        method: "PUT",
        body: { value: coerce(value, p.value_type) },
      });
      setPending(p.key);
      setEditing(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Configuration</h1>
      <p className="muted">
        Business-rule parameters. Changes are <strong>maker-checker gated</strong>{" "}
        — a save creates a pending request, it is not applied until a different
        approver approves it in <Link to="/approvals">Approvals</Link>.
      </p>
      <ErrorNote message={error} />

      {pending && (
        <div className="alert alert--info" data-testid="config-pending">
          Change to <strong>{pending}</strong> requested — awaiting a different
          approver. Nothing has changed yet.{" "}
          <Link to="/approvals">Go to Approvals →</Link>
        </div>
      )}

      <Card>
        {params == null ? (
          <p className="muted">Loading…</p>
        ) : (
          <table className="data" aria-label="Config parameters">
            <thead>
              <tr>
                <th>Key</th>
                <th>Value</th>
                <th>Type</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {params.map((p) => (
                <tr key={p.key} data-testid={`config-row-${p.key}`}>
                  <td>
                    <strong>{p.key}</strong>
                    {p.description && (
                      <div className="muted">{p.description}</div>
                    )}
                  </td>
                  <td>
                    {editing === p.key ? (
                      <form
                        onSubmit={(e) => submit(e, p)}
                        aria-label={`Edit ${p.key}`}
                        style={{ display: "flex", gap: "0.4rem" }}
                      >
                        <input
                          value={value}
                          onChange={(e) => setValue(e.target.value)}
                          aria-label={`New value for ${p.key}`}
                        />
                        <button
                          className="btn-primary"
                          type="submit"
                          disabled={busy}
                        >
                          Request change
                        </button>
                      </form>
                    ) : (
                      <code>{p.value}</code>
                    )}
                  </td>
                  <td>{p.value_type}</td>
                  <td>
                    {editing === p.key ? (
                      <button
                        className="btn-link"
                        onClick={() => setEditing(null)}
                      >
                        Cancel
                      </button>
                    ) : (
                      <button
                        className="btn-link"
                        data-testid={`config-edit-${p.key}`}
                        onClick={() => startEdit(p)}
                      >
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
