import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export type TileTone = "neutral" | "good" | "warn" | "bad";

/**
 * One dashboard metric. `tone`:
 *   good  → healthy (green, --color-secondary)
 *   warn  → needs attention (open exceptions, overdue, high-risk) (--color-warm)
 *   bad   → genuinely broken state only (e.g. failed accounting events)
 *   neutral → plain fact, no judgment
 * `trend` is shown only when a real comparison exists — never fabricated.
 */
export function MetricTile({
  label,
  value,
  subLabel,
  tone = "neutral",
  trend,
  icon: Icon,
  emphasis = "normal",
}: {
  label: string;
  value: ReactNode;
  subLabel?: ReactNode;
  tone?: TileTone;
  trend?: { direction: "up" | "down"; text: string };
  icon?: LucideIcon;
  /**
   * Phase 1 (frontend redesign) — "primary" renders a visually dominant KPI
   * (larger value, e.g. Outstanding Receivable / Expected Credit Loss);
   * "normal" (default, unchanged) is every supporting statistic. Gives each
   * dashboard a clear primary-vs-supporting hierarchy instead of every tile
   * carrying identical weight — no new data, purely presentational.
   */
  emphasis?: "primary" | "normal";
}) {
  return (
    <div
      className={`metric-tile metric-tile--${tone} metric-tile--${emphasis} hover-raise`}
      data-testid="metric-tile"
    >
      <div className="metric-tile__head">
        {Icon && (
          <span className="metric-tile__badge" aria-hidden>
            <Icon size={16} />
          </span>
        )}
        <span className="metric-tile__label">{label}</span>
      </div>
      <div className="metric-tile__value">{value}</div>
      {subLabel != null && (
        <div className="metric-tile__sub">{subLabel}</div>
      )}
      {trend && (
        <div className="metric-tile__trend">
          {trend.direction === "up" ? "▲" : "▼"} {trend.text}
        </div>
      )}
    </div>
  );
}

export function MetricGrid({ children }: { children: ReactNode }) {
  return <div className="metric-grid">{children}</div>;
}
