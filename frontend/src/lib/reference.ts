// Human-readable reference codes — mirrors app/core/references.py exactly.
// The backend already returns `reference_code` on entity responses; this is for
// the cases where the frontend only has a raw id (route params, audit-log
// entity references, ID-lookup inputs).

export const REFERENCE_PREFIXES = {
  Customer: "CU",
  Product: "PR",
  CreditApplication: "AP",
  InstallmentOffer: "OF",
  SalesOrder: "SO",
  InstallmentContract: "CN",
  Payment: "PY",
  CollectionCase: "CC",
} as const;

export type RefEntity = keyof typeof REFERENCE_PREFIXES;

export function formatReference(entity: RefEntity, id: number | string): string {
  const prefix = REFERENCE_PREFIXES[entity];
  const n = typeof id === "string" ? parseInt(id, 10) : id;
  if (!Number.isFinite(n)) return String(id);
  return `${prefix}-${String(n).padStart(6, "0")}`;
}

/** "CN-000012" -> { entity: "InstallmentContract", id: 12 }, else null. */
export function parseReference(
  code: string,
): { entity: RefEntity; id: number } | null {
  if (typeof code !== "string" || !code.includes("-")) return null;
  const [rawPrefix, rawNum] = code.trim().toUpperCase().split("-");
  const entry = (Object.entries(REFERENCE_PREFIXES) as [RefEntity, string][]).find(
    ([, p]) => p === rawPrefix,
  );
  if (!entry || !/^\d+$/.test(rawNum ?? "")) return null;
  return { entity: entry[0], id: parseInt(rawNum, 10) };
}

/**
 * For an "enter an ID" input — accept either the raw number ("12") or the
 * reference code ("CN-000012" / "cn-12"). Returns the numeric id, or "".
 */
export function coerceId(input: string): string {
  const s = input.trim();
  if (/^\d+$/.test(s)) return s;
  const parsed = parseReference(s);
  return parsed ? String(parsed.id) : "";
}

// --- route breadcrumb: a URL path segment -> its reference entity ----------- //
const SEGMENT_ENTITY: Record<string, RefEntity> = {
  customers: "Customer",
  products: "Product",
  applications: "CreditApplication",
  review: "CreditApplication",
  offers: "InstallmentOffer",
  contracts: "InstallmentContract",
  collections: "CollectionCase",
};

export function referenceForSegment(
  parentSegment: string | undefined,
  numericSegment: string,
): string {
  const entity = parentSegment ? SEGMENT_ENTITY[parentSegment] : undefined;
  return entity ? formatReference(entity, numericSegment) : `#${numericSegment}`;
}

// --- audit log: an AuditEvent.entity_type -> its reference entity ----------- //
const AUDIT_ENTITY: Record<string, RefEntity> = {
  customer: "Customer",
  product: "Product",
  Product: "Product",
  credit_application: "CreditApplication",
  installment_offer: "InstallmentOffer",
  sales_order: "SalesOrder",
  installment_contract: "InstallmentContract",
  payment: "Payment",
  collection_case: "CollectionCase",
};

/** Render an audit event's entity reference, e.g. "installment_contract #12"
 *  -> "CN-000012". Falls back to "<type> #<id>" for entities with no code. */
export function auditEntityRef(
  entityType: string,
  entityId: string | null,
): string {
  const label = entityType.replace(/_/g, " ");
  if (entityId == null) return label;
  const entity = AUDIT_ENTITY[entityType];
  if (entity && /^\d+$/.test(entityId)) return formatReference(entity, entityId);
  return `${label} #${entityId}`;
}
