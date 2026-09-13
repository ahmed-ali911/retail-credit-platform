import type { FormEvent, ReactNode } from "react";

/**
 * Phase 1 (frontend redesign) — the one filter-toolbar shape every list/
 * report screen uses: a row of filter controls (each caller supplies its own
 * <Field>/<SelectField>/<SearchSelect>/etc.) plus optional trailing actions
 * (Search, Export, Run). Not wrapped in a Card — a filter row is a control
 * strip, not a content unit (section 27 / 49).
 */
export function FilterBar({
  children,
  actions,
  as = "div",
  onSubmit,
}: {
  children: ReactNode;
  actions?: ReactNode;
  /** Render as a <form> when the bar itself should submit (Enter-to-search). */
  as?: "div" | "form";
  onSubmit?: (e: FormEvent) => void;
}) {
  const className = "filter-bar";
  if (as === "form") {
    return (
      <form className={className} onSubmit={onSubmit}>
        <div className="filter-bar__fields">{children}</div>
        {actions != null && <div className="filter-bar__actions">{actions}</div>}
      </form>
    );
  }
  return (
    <div className={className}>
      <div className="filter-bar__fields">{children}</div>
      {actions != null && <div className="filter-bar__actions">{actions}</div>}
    </div>
  );
}
