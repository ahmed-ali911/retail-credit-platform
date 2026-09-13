/**
 * Phase 1 (frontend redesign) — a real, controlled Tabs component over the
 * existing `.tabs` CSS (previously each page — Dashboard, Reports sub-nav —
 * re-implemented its own button list + active-state logic by hand).
 *
 * Controlled by the caller (`value`/`onChange`) so pages keep driving their
 * own data-fetch-on-tab-change logic exactly as before; this only replaces
 * the markup/interaction, never a workflow.
 */
export interface TabItem {
  value: string;
  label: string;
  /** Optional count badge, e.g. pending-approvals count. Never fabricated —
   * omit when the number isn't already available from an API response. */
  count?: number;
}

export function Tabs({
  items,
  value,
  onChange,
  ariaLabel,
}: {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          aria-selected={item.value === value}
          className={item.value === value ? "active" : undefined}
          onClick={() => onChange(item.value)}
        >
          {item.label}
          {item.count != null && <span className="tabs__count">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}
