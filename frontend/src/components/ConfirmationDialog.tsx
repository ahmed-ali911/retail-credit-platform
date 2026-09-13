import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

/**
 * Phase 1 (frontend redesign) — a reusable confirmation dialog for
 * operationally significant actions (Run ECL Calculation, Post run, approve/
 * reject with context, etc.) where "the existing workflow warrants" a
 * confirmation step (section 22). No page currently has a real modal — this
 * is new UI chrome only; it never changes what an action does, only whether
 * the user is asked to confirm first.
 *
 * Deliberately plain: no entrance animation beyond a short fade (see CSS),
 * no floating/bouncing — matches the "calm micro-interactions" rule.
 */
export function ConfirmationDialog({
  open,
  title,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: ReactNode;
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Renders the confirm button in the danger tone. */
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  return (
    <div className="dialog-scrim" role="presentation" onClick={onCancel}>
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dialog-title" className="dialog__title">
          {title}
        </h2>
        {children != null && <div className="dialog__body">{children}</div>}
        <div className="dialog__actions">
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            ref={confirmRef}
            className={destructive ? "btn-danger" : "btn-primary"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
