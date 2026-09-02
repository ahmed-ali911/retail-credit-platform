import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export type TileTone = "neutral" | "good" | "warn" | "bad";

/**
 * One dashboard metric. `tone`:
 *   good  → healthy (teal, --color-secondary)
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
}: {
  label: string;
  value: ReactNode;
  subLabel?: ReactNode;
  tone?: TileTone;
  trend?: { direction: "up" | "down"; text: string };
  icon?: LucideIcon;
}) {
  return (
    <div className={`metric-tile metric-tile--${tone}`} data-testid="metric-tile">
      <div className="metric-tile__label">
        {Icon && <Icon size={14} aria-hidden />}
        <span>{label}</span>
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
