import type { ReactNode } from "react";

/**
 * Step 16, Part F — the print-only report header + KPI strip for the Customer
 * and Contract detail screens.
 *
 * It is always in the DOM but `display:none` on screen (`.print-only`); the
 * print stylesheet reveals it and hides the app chrome. Every value shown here
 * is already fetched and displayed by the normal screen — this is layout only.
 *
 * The reference / status are concatenated into one meta line (no standalone
 * text nodes) so this block never collides with the on-screen `<RefCode>` in
 * `screen.getByText(...)` assertions.
 */
export interface PrintKpi {
  label: string;
  value: ReactNode;
}

export function PrintHeader({
  title,
  reference,
  status,
  kpis,
  testId,
}: {
  title: string;
  reference?: string;
  status?: string;
  kpis: PrintKpi[];
  testId?: string;
}) {
  const meta = [
    reference ? `Reference ${reference}` : null,
    status ? `Status ${status}` : null,
    `Generated ${new Date().toLocaleDateString()}`,
  ]
    .filter(Boolean)
    .join("  ·  ");

  return (
    <section className="print-only" data-testid={testId}>
      <div className="print-header">
        <h1>{title}</h1>
        <div className="print-meta">{meta}</div>
      </div>
      {kpis.length > 0 && (
        <div className="print-kpis">
          {kpis.map((k) => (
            <div className="print-kpi" key={k.label}>
              <div className="print-kpi__label">{k.label}</div>
              <div className="print-kpi__value">{k.value}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
