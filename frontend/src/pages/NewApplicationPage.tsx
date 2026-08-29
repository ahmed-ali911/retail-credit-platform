import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ApplicationOut } from "../api/types";
import { AssessmentPanel } from "../components/AssessmentPanel";
import { Card, ErrorNote, Field, SelectField } from "../components/ui";
import { StatusBadge } from "../components/StatusBadge";

export function NewApplicationPage() {
  const [params] = useSearchParams();
  const [form, setForm] = useState({
    customer_id: params.get("customer_id") ?? "",
    product_id: params.get("product_id") ?? "",
    requested_amount: "",
    requested_tenor_months: "12",
    channel: "branch",
  });
  const [result, setResult] = useState<ApplicationOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    setResult(null);
    try {
      const created = await api<ApplicationOut>("/applications", {
        method: "POST",
        body: {
          customer_id: Number(form.customer_id),
          product_id: Number(form.product_id),
          requested_amount: Number(form.requested_amount),
          requested_tenor_months: Number(form.requested_tenor_months),
          channel: form.channel,
        },
      });
      const submitted = await api<ApplicationOut>(
        `/applications/${created.id}/submit`,
        { method: "POST" },
      );
      setResult(submitted);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>New application</h1>

      <Card>
        <form onSubmit={onSubmit}>
          <ErrorNote message={error} />
          <div className="field-row">
            <Field
              label="Customer #"
              inputMode="numeric"
              value={form.customer_id}
              onChange={set("customer_id")}
              required
            />
            <Field
              label="Product #"
              inputMode="numeric"
              value={form.product_id}
              onChange={set("product_id")}
              required
            />
          </div>
          <div className="field-row">
            <Field
              label="Requested amount"
              inputMode="decimal"
              value={form.requested_amount}
              onChange={set("requested_amount")}
              required
            />
            <Field
              label="Tenor (months)"
              inputMode="numeric"
              value={form.requested_tenor_months}
              onChange={set("requested_tenor_months")}
              required
            />
            <SelectField label="Channel" value={form.channel} onChange={set("channel")}>
              <option value="branch">branch</option>
              <option value="online">online</option>
            </SelectField>
          </div>
          <button className="btn-primary" type="submit" disabled={busy}>
            {busy ? "Submitting…" : "Create & submit for assessment"}
          </button>
        </form>
      </Card>

      {result && (
        <Card
          title={
            <>
              Application #{result.id} <StatusBadge status={result.status} />
            </>
          }
        >
          {result.latest_assessment ? (
            <AssessmentPanel assessment={result.latest_assessment} />
          ) : (
            <p className="muted">No assessment result returned.</p>
          )}

          {result.status === "approved" && (
            <p style={{ marginTop: "1.25rem" }}>
              <Link className="btn-link" to={`/applications/${result.id}/offer`}>
                Generate an offer →
              </Link>
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
