import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type { AuditEventOut, ProductOut } from "../api/types";
import { Card, EmptyState, ErrorNote, Field, RefCode, money } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { SkeletonTable } from "../components/Skeleton";
import { useToast } from "../components/Toast";
import { formatReference } from "../lib/reference";

export function InventoryPage() {
  const [products, setProducts] = useState<ProductOut[] | null>(null);
  const [events, setEvents] = useState<AuditEventOut[]>([]);
  const [adjustFor, setAdjustFor] = useState<number | null>(null);
  const [form, setForm] = useState({ delta: "", reason: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const loadProducts = useCallback(async () => {
    try {
      // GET /products with no search term returns every product.
      setProducts(await api<ProductOut[]>("/products"));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  const loadEvents = useCallback(async () => {
    try {
      setEvents(
        await api<AuditEventOut[]>(
          "/audit/events?entity_type=Product&action=stock_adjustment",
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void loadProducts();
    void loadEvents();
  }, [loadProducts, loadEvents]);

  async function submit(e: FormEvent, productId: number) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const updated = await api<ProductOut>(
        `/products/${productId}/stock-adjustment`,
        {
          method: "POST",
          body: { delta: Number(form.delta), reason: form.reason },
        },
      );
      setProducts((ps) =>
        (ps ?? []).map((p) => (p.id === productId ? updated : p)),
      );
      toast.success(
        `${formatReference("Product", productId)} stock is now ${updated.stock_quantity}.`,
      );
      setAdjustFor(null);
      setForm({ delta: "", reason: "" });
      await loadEvents();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const adjusting = products?.find((p) => p.id === adjustFor) ?? null;
  const deltaNum = Number(form.delta);
  const previewBalance =
    adjusting && form.delta.trim() !== "" && !Number.isNaN(deltaNum)
      ? adjusting.stock_quantity + deltaNum
      : null;

  return (
    <div className="stack page-wide">
      <PageHeader
        title="Inventory Adjustment"
        description="Privileged — correct stock levels (restock, damage/loss). A positive delta adds stock; a negative delta cannot drop below the reserved quantity."
      />
      <ErrorNote message={error} />

      <Card title="Products">
        {products == null ? (
          <SkeletonTable rows={5} cols={7} />
        ) : (
          <table className="data" aria-label="Inventory">
            <thead>
              <tr>
                <th>Product</th>
                <th>Name</th>
                <th className="num">Stock</th>
                <th className="num">Reserved</th>
                <th className="num">Available</th>
                <th className="num">Cash price</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id} data-testid={`inv-row-${p.id}`}>
                  <td><RefCode code={p.reference_code} /></td>
                  <td>{p.name}</td>
                  <td className="num" data-testid={`inv-stock-${p.id}`}>
                    {p.stock_quantity}
                  </td>
                  <td className="num">{p.reserved_quantity}</td>
                  <td className="num">{p.available_quantity}</td>
                  <td className="num">{money(p.cash_price)}</td>
                  <td>
                    <button
                      className="btn-link"
                      data-testid={`inv-adjust-${p.id}`}
                      onClick={() =>
                        setAdjustFor(adjustFor === p.id ? null : p.id)
                      }
                    >
                      Adjust
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {adjustFor != null && (
          <div className="dialog-scrim" role="presentation" onClick={() => setAdjustFor(null)}>
            <div
              className="dialog"
              role="dialog"
              aria-label={`Adjust stock for product ${adjustFor}`}
              onClick={(e) => e.stopPropagation()}
            >
              <h2 className="dialog__title">Adjust stock</h2>
              <form className="stack" onSubmit={(e) => submit(e, adjustFor)}>
                <dl className="kv">
                  <dt>Current stock</dt>
                  <dd>{adjusting?.stock_quantity ?? "—"}</dd>
                  <dt>New balance (preview)</dt>
                  <dd data-testid="inv-preview-balance">
                    {previewBalance ?? "—"}
                  </dd>
                </dl>
                <Field
                  label="Delta (+/-)"
                  inputMode="numeric"
                  value={form.delta}
                  onChange={(e) => setForm((f) => ({ ...f, delta: e.target.value }))}
                  required
                />
                <Field
                  label="Reason"
                  value={form.reason}
                  onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
                  required
                />
                <div className="dialog__actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setAdjustFor(null)}
                    disabled={busy}
                  >
                    Cancel
                  </button>
                  <button className="btn-primary" type="submit" disabled={busy}>
                    Apply adjustment
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </Card>

      <Card title="Recent adjustments" soft>
        {events.length === 0 ? (
          <EmptyState
            testId="inv-events-empty"
            message="No stock adjustments recorded."
          />
        ) : (
          <table className="data" aria-label="Recent stock adjustments">
            <thead>
              <tr>
                <th>When</th>
                <th>User</th>
                <th>Product</th>
                <th className="num">Delta</th>
                <th className="num">Stock after</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} data-testid={`inv-event-${e.id}`}>
                  <td>{new Date(e.timestamp).toLocaleString()}</td>
                  <td>{e.user_id == null ? "system" : `user ${e.user_id}`}</td>
                  <td>
                    {e.entity_id ? (
                      <RefCode code={formatReference("Product", e.entity_id)} />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="num">
                    {(e.after_value?.delta as number) ?? "—"}
                  </td>
                  <td className="num">
                    {(e.after_value?.stock_quantity as number) ?? "—"}
                  </td>
                  <td>{(e.after_value?.reason as string) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
