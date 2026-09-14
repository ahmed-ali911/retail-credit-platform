// Maps a domain status string onto one of the token colour roles.
// "good" = --color-secondary (green as of Step 15), warnings use --color-warm
// sparingly, rejections use the separate --color-danger red.

const GOOD = new Set([
  "approved", "paid", "active", "accepted", "applied", "closed_good",
  // Mock Payment Gateway feature
  "settled", "matched", "resolved",
]);
const WARN = new Set([
  "referred",
  "pending",
  "under_assessment",
  "submitted",
  "partially_paid",
  "presented",
  // Mock Payment Gateway feature — in progress / needs a look, not yet good or bad
  "initiated", "authorized", "captured", "partially_refunded", "unresolved", "open",
  "status_mismatch", "date_mismatch", "missing_in_gateway",
]);
const BAD = new Set([
  "rejected", "overdue", "expired", "broken", "overpaid",
  // Mock Payment Gateway feature
  "failed", "cancelled", "reversed", "refunded", "chargeback",
  "amount_mismatch", "missing_in_internal_system", "duplicate",
]);
const DARK = new Set(["closed", "waived"]);
const CURED = new Set(["cured", "kept"]);

function toneFor(status: string): string {
  const s = status.toLowerCase();
  if (GOOD.has(s)) return "badge--good";
  if (CURED.has(s)) return "badge--cured";
  if (WARN.has(s)) return "badge--warn";
  if (BAD.has(s)) return "badge--bad";
  if (DARK.has(s)) return "badge--dark";
  return "badge--neutral"; // draft, created, ...
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge ${toneFor(status)}`}>{status.replace(/_/g, " ")}</span>
  );
}
