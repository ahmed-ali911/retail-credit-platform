import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { Link } from "react-router-dom";
import { Inbox, Printer, type LucideIcon } from "lucide-react";
import { formatReference, type RefEntity } from "../lib/reference";

export function Card({
  children,
  soft,
  title,
}: {
  children: ReactNode;
  soft?: boolean;
  title?: ReactNode;
}) {
  return (
    <section className={`card hover-raise${soft ? " card--soft" : ""}`}>
      {title != null && <h2>{title}</h2>}
      {children}
    </section>
  );
}

export function Field({
  label,
  ...props
}: { label: string } & InputHTMLAttributes<HTMLInputElement>) {
  // Step 16, Part E — one required-field indicator across every form.
  return (
    <label className={`field${props.required ? " field--required" : ""}`}>
      <span>{label}</span>
      <input {...props} />
    </label>
  );
}

export function SelectField({
  label,
  children,
  ...props
}: { label: string } & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <label className={`field${props.required ? " field--required" : ""}`}>
      <span>{label}</span>
      <select {...props}>{children}</select>
    </label>
  );
}

/**
 * Step 16, Part E — a titled group of fields. Use where a form has more than
 * ~5 fields, so it reads as sections (Personal / Employment / Financial …)
 * matching the grouped Customer detail view.
 */
export function FormSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <fieldset className="form-section">
      <legend>{title}</legend>
      {children}
    </fieldset>
  );
}

/**
 * Step 16, Part D — the one empty state every data table uses when it can
 * legitimately show zero rows. Keeps the caller's `testId` + message so
 * existing assertions keep working; adds an icon and consistent framing.
 */
export function EmptyState({
  message,
  testId,
  icon: Icon = Inbox,
}: {
  message: ReactNode;
  testId?: string;
  icon?: LucideIcon;
}) {
  return (
    <div className="empty-state" data-testid={testId} role="status">
      <span className="empty-state__icon" aria-hidden>
        <Icon size={20} />
      </span>
      <p className="empty-state__message muted">{message}</p>
    </div>
  );
}

/**
 * Step 16, Part D — consistent "showing X of Y" line, with Prev/Next when the
 * result set is actually paginated by the API (offset/limit). Most tables in
 * the app return a full list, so the pager simply doesn't render for them.
 */
export function ResultSummary({
  total,
  shown,
  noun,
  testId,
  page,
}: {
  total: number;
  shown: number;
  noun: string;
  testId?: string;
  page?: { offset: number; limit: number; onPage: (offset: number) => void };
}) {
  const label =
    page && total > shown
      ? `${page.offset + 1}–${page.offset + shown} of ${total} ${noun}`
      : `${total} ${noun}${total === 1 ? "" : "s"}`;
  const canPrev = page != null && page.offset > 0;
  const canNext = page != null && page.offset + shown < total;
  return (
    <div className="result-summary" data-testid={testId}>
      <span>{label}</span>
      {page && (canPrev || canNext) && (
        <span className="result-summary__pager">
          <button
            type="button"
            disabled={!canPrev}
            onClick={() => page.onPage(Math.max(0, page.offset - page.limit))}
          >
            ‹ Prev
          </button>
          <button
            type="button"
            disabled={!canNext}
            onClick={() => page.onPage(page.offset + page.limit)}
          >
            Next ›
          </button>
        </span>
      )}
    </div>
  );
}

/**
 * Step 16, Part F — "Print / PDF view" trigger. The print layout itself is
 * pure CSS (styles/print.css): the browser's print dialog renders the page
 * with the app chrome hidden and the `.print-only` report blocks shown.
 */
export function PrintButton({ label = "Print / PDF view" }: { label?: string }) {
  return (
    <button
      type="button"
      className="btn-secondary no-print"
      data-testid="print-view-button"
      onClick={() => window.print()}
    >
      <Printer size={14} aria-hidden style={{ verticalAlign: "-2px", marginRight: 4 }} />
      {label}
    </button>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="alert alert--error" role="alert">
      {message}
    </div>
  );
}

/**
 * A structured reference code (Step 14, Part C) — e.g. `CN-000012`. Replaces
 * every `#<id>` display. Pass the backend's `reference_code` string directly,
 * or an `entity` + `id` when the frontend only has the raw id (route params,
 * report rows). Optionally wraps in a router `<Link to={to}>`.
 */
export function RefCode({
  code,
  entity,
  id,
  to,
}: {
  code?: string;
  entity?: RefEntity;
  id?: number | string;
  to?: string;
}) {
  const text =
    code ?? (entity != null && id != null ? formatReference(entity, id) : "—");
  const inner = <span className="ref-code">{text}</span>;
  return to ? (
    <Link to={to} className="ref-code-link">
      {inner}
    </Link>
  ) : (
    inner
  );
}

/** Money — the backend stores 2dp; format consistently. */
export function money(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}
