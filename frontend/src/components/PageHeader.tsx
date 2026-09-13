import type { ReactNode } from "react";

/**
 * Phase 1 (frontend redesign) — the one page-header shape every screen uses:
 * a title, an optional short functional description, and optional actions on
 * the right (Export, Run Calculation, New Application, …). Replaces the
 * hand-rolled `<h1>` + `<p className="muted">` pattern repeated on every page.
 *
 * Deliberately not wrapped in a `<Card>` — a page header is chrome, not a
 * content unit (section 12 / 49 of the redesign brief).
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div className="page-header__text">
        <h1 className="page-header__title">{title}</h1>
        {description != null && (
          <p className="page-header__desc muted">{description}</p>
        )}
      </div>
      {actions != null && <div className="page-header__actions">{actions}</div>}
    </div>
  );
}

/**
 * A smaller in-page section heading (e.g. "Stage distribution" inside the ECL
 * workbench, "Installments" inside a contract's tabs) — one step down from
 * PageHeader, still outside any card by default.
 */
export function SectionHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="section-header">
      <div className="section-header__text">
        <h2 className="section-header__title">{title}</h2>
        {description != null && (
          <p className="section-header__desc muted">{description}</p>
        )}
      </div>
      {actions != null && <div className="section-header__actions">{actions}</div>}
    </div>
  );
}
