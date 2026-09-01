import { useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type { ProductOut } from "../api/types";
import { Card, ErrorNote, Field, money } from "../components/ui";

function StockBadge({ available }: { available: number }) {
  const soldOut = available <= 0;
  return (
    <span
      className={`badge ${soldOut ? "badge--bad" : "badge--good"}`}
      data-testid="stock-badge"
    >
      {soldOut ? "Sold Out" : "Available"}
    </span>
  );
}

export function ProductDirectoryPage() {
  const [term, setTerm] = useState("");
  const [rows, setRows] = useState<ProductOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function search(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setRows(
        await api<ProductOut[]>(
          `/products?search=${encodeURIComponent(term.trim())}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="stack">
      <h1>Products</h1>
      <p className="muted">
        Read-only. Correcting stock levels is done on the{" "}
        <strong>Inventory</strong> screen.
      </p>
      <ErrorNote message={error} />

      <Card>
        <form className="inline-form" onSubmit={search}>
          <Field
            label="Search by name or category"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            required
          />
          <button className="btn-primary" type="submit">
            Search
          </button>
        </form>
      </Card>

      {rows != null && (
        <Card>
          {rows.length === 0 ? (
            <p className="muted" data-testid="products-empty">
              No products match.
            </p>
          ) : (
            <table className="data" aria-label="Product results">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>Category</th>
                  <th className="num">Cash price</th>
                  <th className="num">Stock</th>
                  <th className="num">Reserved</th>
                  <th>Availability</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} data-testid={`product-row-${p.id}`}>
                    <td>{p.id}</td>
                    <td>{p.name}</td>
                    <td>{p.category}</td>
                    <td className="num">{money(p.cash_price)}</td>
                    <td className="num">{p.stock_quantity}</td>
                    <td className="num">{p.reserved_quantity}</td>
                    <td>
                      <StockBadge available={p.available_quantity} />
                    </td>
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
