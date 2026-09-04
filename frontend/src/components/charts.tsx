import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DEFAULT_APPEARANCE } from "../lib/appearance";

// --------------------------------------------------------------------------- //
// Colour tokens read live from :root so charts follow the Appearance overrides
// --------------------------------------------------------------------------- //
const FALLBACKS: Record<string, string> = {
  "--color-primary": DEFAULT_APPEARANCE.primary,
  "--color-secondary": DEFAULT_APPEARANCE.secondary,
  "--color-warm": DEFAULT_APPEARANCE.warm,
  "--color-danger": DEFAULT_APPEARANCE.danger,
  "--color-muted": "#5b6b82",
  "--color-border": "#d8e6ef",
};

function readVar(name: string): string {
  try {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return v || FALLBACKS[name] || "#888";
  } catch {
    return FALLBACKS[name] || "#888";
  }
}

export function useTokenColors(): Record<string, string> {
  const [colors, setColors] = useState(() => {
    const out: Record<string, string> = {};
    for (const n of Object.keys(FALLBACKS)) out[n] = readVar(n);
    return out;
  });
  // re-read after mount (styles/appearance are applied by then)
  useEffect(() => {
    const out: Record<string, string> = {};
    for (const n of Object.keys(FALLBACKS)) out[n] = readVar(n);
    setColors(out);
  }, []);
  return colors;
}

// --------------------------------------------------------------------------- //
// Pure data mappers — the unit under test. Every chart is built from data an
// existing summary/report endpoint already returns; nothing is fabricated.
// --------------------------------------------------------------------------- //
export interface Slice {
  name: string;
  value: number;
}

/** contracts_by_status -> [{name:"created", value:1}, …] (only non-zero). */
export function statusToData(byStatus: Record<string, number>): Slice[] {
  return Object.entries(byStatus)
    .map(([name, value]) => ({ name, value }))
    .filter((s) => s.value > 0);
}

/** customers_by_risk_band -> ordered slices (low/medium/high/unscored). */
export function riskToData(bands: Record<string, number>): Slice[] {
  const order = ["low", "medium", "high", "unscored"];
  return order
    .filter((k) => k in bands)
    .map((name) => ({ name, value: bands[name] }))
    .filter((s) => s.value > 0);
}

/** dpd_distribution {current, buckets} -> ordered bars. */
export function agingToData(dist: {
  current: number;
  buckets: Record<string, number>;
}): Slice[] {
  return [
    { name: "current", value: dist.current },
    ...Object.entries(dist.buckets).map(([name, value]) => ({ name, value })),
  ];
}

// --------------------------------------------------------------------------- //
// Chart components
// --------------------------------------------------------------------------- //
const W = 300;
const H = 220;

function Donut({
  data,
  colors,
  ariaLabel,
}: {
  data: Slice[];
  colors: string[];
  ariaLabel: string;
}) {
  if (data.length === 0) {
    return (
      <p className="muted" data-testid="chart-empty">
        No data to chart.
      </p>
    );
  }
  return (
    <div className="chart" role="img" aria-label={ariaLabel}>
      <PieChart width={W} height={H}>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          innerRadius={45}
          outerRadius={80}
          paddingAngle={2}
          isAnimationActive={false}
        >
          {data.map((_, i) => (
            <Cell key={i} fill={colors[i % colors.length]} />
          ))}
        </Pie>
        <Tooltip />
        <Legend />
      </PieChart>
    </div>
  );
}

export function StatusDonut({
  byStatus,
}: {
  byStatus: Record<string, number>;
}) {
  const c = useTokenColors();
  const data = statusToData(byStatus);
  const palette = [
    c["--color-primary"],
    c["--color-secondary"],
    c["--color-muted"],
    c["--color-warm"],
  ];
  return <Donut data={data} colors={palette} ariaLabel="Contracts by status" />;
}

export function RiskBandDonut({
  bands,
}: {
  bands: Record<string, number>;
}) {
  const c = useTokenColors();
  const data = riskToData(bands);
  // low = healthy, medium = neutral, high = attention, unscored = muted
  const byName: Record<string, string> = {
    low: c["--color-secondary"],
    medium: c["--color-primary"],
    high: c["--color-warm"],
    unscored: c["--color-muted"],
  };
  return (
    <Donut
      data={data}
      colors={data.map((s) => byName[s.name] ?? c["--color-muted"])}
      ariaLabel="Customers by risk band"
    />
  );
}

export function AgingBarChart({
  distribution,
}: {
  distribution: { current: number; buckets: Record<string, number> };
}) {
  const c = useTokenColors();
  const data = agingToData(distribution);
  return (
    <div className="chart" role="img" aria-label="DPD aging distribution">
      <BarChart width={W + 40} height={H} data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke={c["--color-border"]} />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
        <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={28} />
        <Tooltip />
        <Bar dataKey="value" isAnimationActive={false}>
          {data.map((s, i) => (
            <Cell
              key={i}
              fill={s.name === "current" ? c["--color-secondary"] : c["--color-warm"]}
            />
          ))}
        </Bar>
      </BarChart>
    </div>
  );
}
