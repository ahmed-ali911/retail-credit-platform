import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { CustomerOut } from "../api/types";
import { Card, ErrorNote, Field, RefCode } from "../components/ui";

export function CreateCustomerPage() {
  const [form, setForm] = useState({
    name: "",
    national_id: "",
    phone: "",
    email: "",
    risk_score: "",
    monthly_income: "",
    existing_monthly_obligations: "0",
    employer_name: "",
    contact_phone: "",
  });
  const [created, setCreated] = useState<CustomerOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body = {
        name: form.name,
        national_id: form.national_id,
        phone: form.phone || null,
        email: form.email || null,
        risk_score: form.risk_score ? Number(form.risk_score) : null,
        profile: {
          monthly_income: Number(form.monthly_income),
          existing_monthly_obligations: Number(form.existing_monthly_obligations || 0),
          employer_name: form.employer_name || null,
          contact_phone: form.contact_phone || null,
        },
      };
      const res = await api<CustomerOut>("/customers", { method: "POST", body });
      setCreated(res);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Create customer</h1>
      {created ? (
        <Card soft title={<>Customer <RefCode code={created.reference_code} /> created</>}>
          <dl className="kv">
            <dt>Name</dt>
            <dd>{created.name}</dd>
            <dt>National ID</dt>
            <dd>{created.national_id}</dd>
            <dt>Risk score</dt>
            <dd>{created.risk_score ?? "—"}</dd>
            <dt>Monthly income</dt>
            <dd>{created.profile?.monthly_income}</dd>
          </dl>
          <p style={{ marginTop: "1rem" }}>
            <Link to={`/applications/new?customer_id=${created.id}`}>
              Use in a new application →
            </Link>
          </p>
          <button
            className="btn-link"
            onClick={() => {
              setCreated(null);
              setForm((f) => ({ ...f, name: "", national_id: "" }));
            }}
          >
            Create another
          </button>
        </Card>
      ) : (
        <Card>
          <form onSubmit={onSubmit}>
            <ErrorNote message={error} />
            <div className="field-row">
              <Field label="Name" value={form.name} onChange={set("name")} required />
              <Field
                label="National ID"
                value={form.national_id}
                onChange={set("national_id")}
                required
              />
            </div>
            <div className="field-row">
              <Field label="Phone" value={form.phone} onChange={set("phone")} />
              <Field label="Email" type="email" value={form.email} onChange={set("email")} />
            </div>
            <div className="field-row">
              <Field
                label="Risk score (0–1000, optional)"
                inputMode="numeric"
                value={form.risk_score}
                onChange={set("risk_score")}
              />
              <Field
                label="Employer (optional)"
                value={form.employer_name}
                onChange={set("employer_name")}
              />
            </div>
            <div className="field-row">
              <Field
                label="Monthly income"
                inputMode="decimal"
                value={form.monthly_income}
                onChange={set("monthly_income")}
                required
              />
              <Field
                label="Existing monthly obligations"
                inputMode="decimal"
                value={form.existing_monthly_obligations}
                onChange={set("existing_monthly_obligations")}
              />
            </div>
            <button className="btn-primary" type="submit" disabled={busy}>
              {busy ? "Creating…" : "Create customer"}
            </button>
          </form>
        </Card>
      )}
    </div>
  );
}
