import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { Link } from "react-router-dom";
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
    <section className={soft ? "card card--soft" : "card"}>
      {title != null && <h2>{title}</h2>}
      {children}
    </section>
  );
}

export function Field({
  label,
  ...props
}: { label: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="field">
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
    <label className="field">
      <span>{label}</span>
      <select {...props}>{children}</select>
    </label>
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
