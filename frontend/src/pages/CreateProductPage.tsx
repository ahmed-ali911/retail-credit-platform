import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ProductOut } from "../api/types";
import { Card, ErrorNote, Field, RefCode, SelectField, money } from "../components/ui";

const CATEGORIES = ["electronics", "appliances", "furniture", "automotive", "other"];

export function CreateProductPage() {
  const [name, setName] = useState("");
  const [cashPrice, setCashPrice] = useState("");
  const [category, setCategory] = useState("electronics");
  const [created, setCreated] = useState<ProductOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api<ProductOut>("/products", {
        method: "POST",
        body: { name, cash_price: Number(cashPrice), category, installment_eligible: true },
      });
      setCreated(res);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Create product</h1>
      {created ? (
        <Card soft title={<>Product <RefCode code={created.reference_code} /> created</>}>
          <dl className="kv">
            <dt>Name</dt>
            <dd>{created.name}</dd>
            <dt>Category</dt>
            <dd>{created.category}</dd>
            <dt>Cash price</dt>
            <dd>{money(created.cash_price)}</dd>
          </dl>
          <p style={{ marginTop: "1rem" }}>
            <Link to={`/applications/new?product_id=${created.id}`}>
              Use in a new application →
            </Link>
          </p>
          <button
            className="btn-link"
            onClick={() => {
              setCreated(null);
              setName("");
              setCashPrice("");
            }}
          >
            Create another
          </button>
        </Card>
      ) : (
        <Card>
          <form onSubmit={onSubmit}>
            <ErrorNote message={error} />
            <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
            <div className="field-row">
              <Field
                label="Cash price"
                inputMode="decimal"
                value={cashPrice}
                onChange={(e) => setCashPrice(e.target.value)}
                required
              />
              <SelectField
                label="Category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </SelectField>
            </div>
            <button className="btn-primary" type="submit" disabled={busy}>
              {busy ? "Creating…" : "Create product"}
            </button>
          </form>
        </Card>
      )}
    </div>
  );
}
