# Frontend Redesign — Phase 1 Audit

**Date:** 2026-09-04
**Scope of this document:** a full audit of the existing `frontend/` app, mapped
against the redesign brief (grouped shell, dashboard wishlist §8, screen list
§9-37). No code was changed to produce this document. Phase 2 (tokens + shell)
is summarised at the end.

---

## 1. What exists today

### Stack
- React 18 + Vite 5 + TypeScript, `react-router-dom` v6, `lucide-react` for icons.
- No state library — local `useState` per page; one React context (`AuthContext`).
- `src/api/client.ts` — thin `fetch` wrapper (`api<T>()`, `downloadFile()`),
  bearer token in `localStorage` (`rc.token`), global 401 → logout.
- Dev: Vite proxies `/api` → backend (no CORS change). Tests: Vitest + jsdom +
  Testing Library, `fetch` mocked.
- **69 frontend tests across 16 files, all passing.**

### Routes (`src/App.tsx`)
| Path | Page component | Notes |
|---|---|---|
| `/login` | `LoginPage` | outside the shell |
| `/` (index) | `DashboardPage` | 5-tab Executive Dashboard (Steps 11-13) |
| `/customers` | `CustomerDirectoryPage` | search + status filter (Step 12) |
| `/customers/new` | `CreateCustomerPage` | |
| `/customers/:customerId` | `CustomerPage` | detail + P0-4 exposure panel |
| `/products` | `ProductDirectoryPage` | search, loads all by default |
| `/products/new` | `CreateProductPage` | |
| `/applications/new` | `NewApplicationPage` | create + submit + AssessmentPanel |
| `/applications/:applicationId/offer` | `OfferPage` | generate offer → accept |
| `/offers/:offerId` | `OfferPage` | same component, offer-id entry |
| `/contracts/:contractId` | `ContractPage` | delivery, payments, **closure actions**, receivable, installments |
| `/review` | `ReviewQueuePage` | referred-application queue |
| `/review/:applicationId` | `ReviewApplicationPage` | manual decision form (P0-2) |
| `/reconciliation` | `ReconciliationPage` | status, bank lines, run matching, exceptions, request-match |
| `/approvals` | `ApprovalsPage` | pending maker-checker list, approve/reject |
| `/collections` | `CollectionsPage` | case list + filters + "run overdue" (admin) |
| `/collections/:caseId` | `CollectionCasePage` | case detail + log activity (PtP fields) |
| `/snapshot` | `SnapshotPage` | portfolio counts (Step 10) — overlaps the Dashboard |
| `/inventory` | `InventoryPage` | stock table + per-row adjust + audit panel |
| `/reports` | `ReportsPage` | Reports Center: 6 categories, per-category sub-reports, csv/xlsx/pdf |
| `/config` | `ConfigPage` | config parameters + maker-checker edit |
| `/audit` | `AuditLogPage` | audit events table + filters |
| `*` | → `/` | catch-all redirect |

### Components (`src/components/`)
| Component | Role | Reuse verdict |
|---|---|---|
| `Shell` | flat sidebar (14 links) + thin header; `NAV` array with per-item `roles`; `canSee()` helper | **Redesign in Phase 2** (this audit's main target) |
| `ui.tsx` — `Card`, `Field`, `SelectField`, `ErrorNote`, `money()` | the primitives every page uses | **Keep the API, restyle later.** `Card` (title + soft variant), `Field`/`SelectField` (label + input), `money()` (2dp). Missing: `Button`, `Table`, `PageHeader`, `Toolbar`, `EmptyState`, `Loading`, `KpiCard` (only exists as `MetricTile`), `Modal/Drawer`, `Tabs`, `Pagination`. |
| `StatusBadge` | maps a status string → one of 6 tone classes (`good/warn/bad/dark/cured/neutral`) | **Keep — the meaning→token map is the app's status system.** Coverage gap: many statuses fall through to `neutral` (draft, created, initiated, resolved, no_match, etc.); needs an explicit map per domain. |
| `MetricTile` / `MetricGrid` | dashboard KPI tile (label, value, sub, tone, optional lucide icon in a tinted badge — Step 12/13) | **Keep — this is the KpiCard.** Rename/formalise as the shared KPI card in a later phase. |
| `AssessmentPanel` | decision + DBR + triggered rules (Credit Officer's read) | Keep; used by `NewApplicationPage` + `ReviewApplicationPage`. |
| `ScheduleTable` | installment schedule preview (principal flat, profit declining) | Keep; used by `OfferPage`. |

### Design tokens (`src/styles/tokens.css`)
Exists and is used consistently. Colour tokens: `--color-primary-dark`,
`--color-primary`, `--color-secondary` (teal = good), `--color-accent`,
`--color-accent-soft`, `--color-warm` (gold = attention), `--color-natural`
(rare green), `--color-bg`, `--color-text`, `--color-danger` (red), plus derived
`--color-surface`, `--color-muted`, `--color-border`, `--radius` (8px),
`--shadow`.
**Gaps:** no spacing scale, no radius scale, no shadow scale, no typography
scale, no neutral/gray scale beyond one `--color-border`, no semantic
status-surface tokens (badges/alerts hardcode `#f4ecdf`, `#f8e4e1`, `#e6efe1`,
`rgba(47,184,198,…)`, `rgba(192,57,43,…)`, `#04343a`, `#cfe0ff`), no
sidebar/header dimension tokens. The font-family is inlined in `body`, not a
token.

### Global styles (`src/styles/app.css`)
One 300-line file: shell, cards, forms, buttons, `table.data`, badges, `.kv`,
alerts, `.metric-tile*`, Reports Center layout (`.report-cats`, `.split`,
`.subreport-nav`, `.export-group`), `.tabs`, login. Uses `color-mix()` already
(Step 12/13). **Ad-hoc hex/rgba outside the token set** live only in the badge,
alert, table-row-tint and sidebar-link rules — enumerated below for Phase 8.

### Auth & RBAC
- `AuthProvider` → `/auth/me` on load; `RequireAuth` guards the shell routes.
- 7 backend roles: `admin`, `credit_officer`, `credit_manager`,
  `sales_employee`, `finance_officer`, `customer`, `collections_officer`.
- Nav gating: `Shell.NAV[].roles` (client-side hint only; the backend is
  authoritative). Some items ungated (`New Customer/Product/Application`,
  `Dashboard`) → visible to every signed-in user including `customer`.

---

## 2. What already works correctly — preserve

- **All business flows and their API wiring.** Application → assessment → offer →
  accept → contract → deliver → pay → settle/cancel/return; reconciliation
  matching + maker-checker; collections case + activity + PtP; approvals
  (maker ≠ checker enforced server-side, mirrored client-side on the row);
  config maker-checker (202 → pending); inventory adjustment + audit; the whole
  Reports Center incl. profitability `level` drill-down and csv/xlsx/pdf export.
- **Token bearer auth + global 401 handling.**
- **`StatusBadge` meaning→colour mapping** and the `money()` 2dp formatter.
- **`MetricTile`** as the KPI primitive (icon badge, tone).
- **The Step 12 read-only Dashboard decision** (no mutating actions in tab
  panels) — pinned by a test.
- **RBAC nav gating model** (`roles` per item) — reuse it in the grouped shell.
- **Test infrastructure** (`renderWithProviders`, `mockFetch`, the fake
  `AuthContext` seam).

---

## 3. What should be visually redesigned

| Area | Current state | Redesign intent (later phases) |
|---|---|---|
| **Application shell** | flat 14-item sidebar, `Retail Credit` wordmark, one-line header ("Signed in as … / Log out"), 960px content column | grouped/collapsible sidebar by business domain, proper brand block, header with breadcrumb + user menu — **Phase 2 (this session)** |
| **Dashboard** | 5 tabs, `MetricTile` grid + 2 small tables; `/snapshot` duplicates part of it | consolidate `/snapshot` into the Dashboard; KPI hierarchy, pipeline funnel, portfolio-quality + collections + operations sections — **Phase 3** |
| **Tables** | one `table.data` class, no sort / pagination / density / sticky header / row actions / empty state | a reusable `<DataTable>` — **Phase 4** |
| **Forms** | `Field`/`SelectField` only; no textarea/number/currency/date field, no inline validation, no form layout system | form primitives + layout — **Phase 4** |
| **Status badges** | works but ~40% of real statuses fall through to `neutral` | complete the per-domain status map — **Phase 8** |
| **Page chrome** | each page hand-rolls `<h1>` + `<Card>`s; no consistent `PageHeader` / toolbar / breadcrumb-in-page | `PageHeader` + `Toolbar` components — **Phase 4** |
| **Empty / loading / error** | ad-hoc `<p class="muted">Loading…</p>` / `ErrorNote`; inconsistent | `EmptyState` / `Skeleton` / standard error panel — **Phase 4** |
| **Financial-term distinction** | amounts are all rendered the same (`money()` + `.num`); Cash Price, Down Payment, Receivable, Principal, Profit, Late Fee, Paid, Outstanding, Exposure, Settlement are visually identical | typographic/colour treatment per money concept — **Phase 4/8** |
| **Reports Center layout** | bespoke `.split` + `.report-cats` | fold into the redesigned table/page system — **Phase 6** |
| **Icons** | `lucide-react` used only on `MetricTile` and Reports category cards | consistent iconography across nav + pages — **Phase 2 (nav) + later** |
| **Login** | centered card on a gradient | keep concept, restyle to the design system — **Phase 4/7** |

---

## 4. Missing screens (no route today)

| Missing screen | Backend support that exists | Priority |
|---|---|---|
| **Applications list / queue** (all applications, filterable by status) | only `GET /applications?status=referred` (Step 9, used by Review Queue). A general list would need a widened endpoint — **backend gap**, flag before Phase 4. | High |
| **Contracts list** | `GET /reports/contracts` exists (Step 11, paginated/filterable) — a list screen can be built on it with no backend change. | High |
| **Payments screen** (portfolio-wide payments / a payment's allocations) | payments are recorded + shown only on `ContractPage`. No list endpoint. **Backend gap** for a portfolio view. | Medium |
| **Accounting Events screen** | `GET /accounting/events` + `POST /jobs/post-accounting-events` fully exist (G-07) — **UI just missing.** | High |
| **Settlements / Closures screen** | closure happens on `ContractPage`; `ContractClosureOut` is returned but there's no list endpoint. **Backend gap** for a list. | Medium |
| **Exposure screen** (portfolio exposure, not one customer) | `GET /reports/customers/by-exposure` exists (Step 13). Buildable with no backend change. | Medium |
| **Overdue screen** | `GET /reports/aging` + `POST /jobs/assess-overdue` exist. Buildable. | Medium |
| **Promise-to-Pay screen** | `GET /reports/collections/promise-performance` exists; PtP rows live on `CollectionCasePage`. No PtP list endpoint. Partial. | Low |
| **Credit Assessment screen** (standalone) | assessment is embedded in `NewApplicationPage` / `ReviewApplicationPage` via `AssessmentPanel`. May not need its own screen. | Low |
| **Risk screen** | `GET /reports/summary/credit-risk` + `/reports/customers/by-risk`. Buildable. | Low |
| **User Management** | `POST /auth/register` (admin) exists; no list-users endpoint. **Backend gap.** | Low |
| **Notifications** | no backend event/notification source at all. **Out of scope until a source exists.** | — |
| **Global search** | `GET /customers?search=` and `GET /products?search=` exist, but the directory pages hold `term` in local state, not the URL — a header search can't pre-fill them without a small page change. **Deferred to Phase 4** (when directories are redesigned to read `?search=`). | — |

---

## 5. Components that should become reusable (later phases — not Phase 2)

Formalise into a small design-system layer (`src/components/`):
`Button` (variants), `DataTable` (sort / paginate / density / empty / row-link),
`PageHeader` (title + breadcrumb + actions slot), `Toolbar` (filters row),
`KpiCard` (promote `MetricTile`), `StatusBadge` (extend the map), `Field` set
(text / number / currency / date / select / textarea + inline error),
`EmptyState`, `Skeleton`/`Loading`, `Money` (a component that renders a monetary
value with a concept modifier so Principal ≠ Profit ≠ Fee visually),
`Tabs` (promote the Dashboard/Reports patterns), `Drawer`/`Modal`,
`DefinitionList` (promote `.kv`), `Pagination`.

---

## 6. §8 Dashboard wishlist vs. what exists today

Legend: **✅ built** · **🟡 partial / different shape** · **🔧 buildable from an
existing endpoint, no backend change** · **⛔ needs backend work** · **➖ no data
source**

### A. Portfolio KPIs
| Wishlist metric | Status | Source |
|---|---|---|
| Active Contracts | ✅ | `summary/executive.active_contracts` |
| Outstanding Receivables | ✅ | `summary/executive.total_outstanding_receivable` |
| Total Exposure | 🟡 | `summary/credit-risk.top_customers_by_exposure` (top-10 only) + `reports/customers/by-exposure` (full). No single "portfolio exposure" number → 🔧 sum client-side or ⛔ add a field |
| Collection Rate | ⛔ | no endpoint. Derivable from payments vs. scheduled — not currently computed |
| Overdue Amount | 🔧 | `reports/aging` returns `outstanding_amount` per bucket → sum |
| Overdue Contracts | 🟡 | `summary/operations.overdue_installments` is per-installment, not per-contract; `summary/portfolio.dpd_distribution` counts contracts per bucket → 🔧 |
| Approval Rate | ✅ | `summary/executive.approval_rate` |
| Recovery Rate | ⛔ | no write-off / recovery domain exists (assessment G-17 not built) |

### B. Application Pipeline (Applications → Assessment → Approved → Referred → Rejected → Contracted → Delivered)
🟡 — `summary/portfolio.contracts_by_status` covers `created`/`active`/`closed`;
application-status counts are **not** in any summary endpoint (only `referred`
is listable). ⛔ small backend gap: an application-status count endpoint, or a
widened `GET /applications`.

### C. Portfolio Quality
| | Status |
|---|---|
| Exposure by risk category | 🔧 `reports/customers/by-risk` (risk-band counts) + join with exposure |
| Outstanding receivables by status | 🟡 `summary/portfolio.contracts_by_status`; not by receivable status |
| Delinquency distribution | ✅ `summary/portfolio.dpd_distribution` / `reports/aging` |
| Collection performance | 🟡 `summary/collections` (cases, PtP, late fees) — not a rate |

### D. Collections
| | Status |
|---|---|
| Due Today | ⛔ no "due today" endpoint (installments have `due_date` but no aggregate) |
| Overdue | ✅ `summary/operations.overdue_installments`, `reports/aging` |
| Promise-to-Pay | ✅ `summary/collections.promise_to_pay_kept/broken`, `reports/collections/promise-performance` |
| Collection Rate | ⛔ (as A) |
| Recovery Rate | ⛔ (as A) |

### E. Operations
| | Status |
|---|---|
| Pending Credit Reviews | 🔧 count of `GET /applications?status=referred` |
| Pending Approvals | ✅ `GET /approvals?status=pending` (length) |
| Payment Reconciliation Exceptions | ✅ `summary/operations.open_reconciliation_exceptions` |
| Failed Payments | ➖ `PaymentStatus` is only `applied`/`overpaid` — no failed state (payments are recorded, not gateway-processed) |
| Contracts awaiting delivery | 🔧 `summary/portfolio.contracts_by_status.created` |

### F. Recent Activity
🔧 — `GET /audit/events` exists (newest-first, filterable). A feed can be built
with no backend change.

**Conclusion for Phase 3 scoping:** the existing 5-tab Dashboard already covers
most of A/B(partial)/C/D/E via the 5 `summary/*` endpoints. The genuinely new
work is: (1) consolidate `/snapshot`; (2) a pipeline/funnel view (needs a small
backend count endpoint); (3) a recent-activity feed (buildable now); (4) decide
whether Collection Rate / Recovery Rate / Total Exposure get real derivations or
"future capability" placeholders. **No new metric is built in this session.**

---

## 7. §9-37 screen list — what exists today

| § topic | Exists? | Where / state |
|---|---|---|
| KPI Card design | 🟡 | `MetricTile` — has icon badge + tone; not a formal system |
| Applications screen | ⛔ | no list; only `NewApplicationPage` (create+submit) and `ReviewQueuePage` (referred only) |
| Application Detail | 🟡 | `ReviewApplicationPage` shows details + assessment for **referred** apps only; no generic detail route |
| Customer Profile | ✅ | `CustomerPage` (`GET /customers/{id}` + exposure panel) |
| Product Management | 🟡 | `ProductDirectoryPage` (read) + `CreateProductPage` + `InventoryPage` (stock) — split across 3 screens |
| Pricing / Installment Offer | ✅ | `OfferPage` (generate offer, pricing fields, accept) |
| Installment Schedule | ✅ | `ScheduleTable` component (on `OfferPage`) |
| Contract Detail | ✅ | `ContractPage` — the largest page (394 lines): sales order, delivery, receivable, record payment, closure (quote/settle/cancel/return), installments |
| Payments | 🟡 | recording + allocations shown only inside `ContractPage`; no payments screen |
| Bank Reconciliation | ✅ | `ReconciliationPage` (status, add line, run, exceptions, request-match) |
| Collections | ✅ | `CollectionsPage` + `CollectionCasePage` |
| Credit & Risk | 🟡 | Dashboard "Credit & Risk" tab + `reports/summary/credit-risk`; no dedicated screen |
| Approvals / Maker-Checker | ✅ | `ApprovalsPage` |
| Reports | ✅ | `ReportsPage` (Reports Center, 6 categories, sub-reports, export) |
| Audit Log | ✅ | `AuditLogPage` |
| Configuration | ✅ | `ConfigPage` |
| Status Badges | ✅ | `StatusBadge` (needs map completion) |
| Table System | ⛔ | only a `table.data` CSS class; no component |
| Forms | 🟡 | `Field` / `SelectField` only |
| Empty / Loading / Error | 🟡 | `ErrorNote` + ad-hoc `Loading…` text |
| Responsive | ⛔ | fixed 220px sidebar, 960px content, no breakpoints |
| Iconography | 🟡 | `lucide-react` present, used in 2 places |
| Component Architecture | 🟡 | `components/` has 6 files; no design-system layer |
| Financial-terms-never-mix | ⛔ | all amounts render identically |
| Centralised Design System | 🟡 | `tokens.css` + `app.css` — colour tokens only, no scales |

---

## 8. Ad-hoc colours to normalise (Phase 8, not now)

All inside `src/styles/app.css`, all in shared component rules (not page code):
- `.badge--warn` bg `#f4ecdf`; `.badge--bad` bg `#f8e4e1`; `.badge--cured` bg
  `#e6efe1`; `.badge--good` fg `#04343a`
- `.alert--error` bg `#f8e4e1`
- `table.data tr.is-paid` `rgba(47,184,198,0.07)`; `tr.is-overdue`
  `rgba(192,57,43,0.06)`
- `.shell__nav a` colour `#cfe0ff`; hover `rgba(255,255,255,0.08)`
- `.field input` bg `#fff` (should be `--color-surface`)

Plan: replace each with a `color-mix()` of the nearest meaning token (the new
`--status-*-bg/fg` tokens added in Phase 2 cover the badge/alert cases). Deferred
so the change lands with the Phase 8 consistency pass and one visual review,
not piecemeal.

---

## 9. Phase 2 — what this session changed (tokens + shell only)

See the commit for detail. Summary:

### `src/styles/tokens.css` — extended, nothing removed
Added, all additive (every existing `--color-*`, `--radius`, `--shadow` kept and
unchanged):
- **Typography:** `--font-family-base` (the existing system stack, now a token),
  `--font-family-mono`, `--text-2xs … --text-2xl`, `--font-weight-{regular,
  medium,semibold,bold}`, `--leading-{tight,normal}`.
- **Spacing:** `--space-1 … --space-10` (4px grid).
- **Radius:** `--radius-sm/md/lg/pill` (`--radius` kept = `--radius-md`).
- **Shadow:** `--shadow-sm/md/lg` (`--shadow` kept = `--shadow-sm`).
- **Neutral surfaces / borders / text:** `--surface-page`, `--surface-1/2`,
  `--surface-raised`, `--border-subtle`, `--border-strong`, `--text-strong`,
  `--text-subtle` (existing `--color-surface/bg/border/muted/text` all kept).
- **Semantic status pairs** (derived from the 4 meaning tokens via `color-mix`,
  no new hues): `--status-good-bg/fg`, `--status-warn-bg/fg`,
  `--status-bad-bg/fg`, `--status-info-bg/fg`, `--status-neutral-bg/fg`.
- **Shell dimensions:** `--sidebar-width`, `--sidebar-width-collapsed`,
  `--header-height`, `--content-max`.

### `src/styles/shell.css` — new file (imported after `app.css` in `main.tsx`)
The redesigned shell: grouped, collapsible sidebar + a header with breadcrumb +
user menu, using `.appshell*` class names and only design tokens. The old
`.shell*` rules in `app.css` were **removed** (nothing rendered them any more);
`app.css` now carries a one-line pointer to `shell.css`. A `.sr-only` utility
was added.

### `src/components/Shell.tsx` — rewritten (shell only)
- Sidebar grouped by business domain (Overview / Operations / Credit & Risk /
  Collections / Finance / Portfolio / Control / Administration). Every existing
  route has a home; items with no route (Contracts list, Payments, Accounting
  Events, Settlements, portfolio Exposure, Overdue, User Management) are **not**
  shown — they are the §4 missing-screen backlog.

  | Group | Items (route) — roles as before |
  |---|---|
  | Overview | Dashboard `/` |
  | Operations | New Application · Customers · New Customer · Products · New Product · Inventory (finance/admin) |
  | Credit & Risk | Review Queue (credit_officer/credit_manager/admin) |
  | Collections | Collections (collections_officer/credit_manager/admin) |
  | Finance | Reconciliation (finance_officer/admin) |
  | Portfolio | Snapshot · Reports (finance_officer/credit_manager/admin) |
  | Control | Approvals (finance_officer/credit_manager/admin) · Audit Logs (admin/credit_manager) |
  | Administration | Configuration (admin) |

- Same `roles` gating model as before; a group with no visible items is hidden
  entirely.
- Collapsible groups (collapsed set persisted in `localStorage` under
  `rc.nav.collapsed`).
- Lucide icon per item.
- Header: a breadcrumb derived from the route + a user menu (avatar initials,
  name, role, click-outside/Escape to close, logout inside). An always-rendered
  `.sr-only` "Signed in as **{username}** ({role})" for screen readers and to
  keep the existing login test green.
- Mobile (`<900px`): sidebar becomes an off-canvas drawer toggled from a header
  hamburger + scrim. Full responsive work is Phase 7.
- `canSee()` still exported.
- **No page content touched.** Every page renders exactly as before, inside the
  new shell. All 41 frontend + 202 backend tests pass unmodified; `tsc` and
  `vite build` clean.

### Not done in Phase 2 (deferred, by instruction)
Global search, notifications, environment chip, dashboard consolidation, any
page-body change, ad-hoc-colour normalisation, new components, responsive
breakpoints beyond a basic sidebar toggle.

---

## 10. Step 14 — light-theme revision, typography, reference codes, charts, appearance

Direct user feedback after seeing Phase 2 live. Everything Phase 1/2 built
(grouped nav structure, token system, breadcrumb header, mobile drawer) **stays**
— only the colour treatment of the shell changed.

- **A — light sidebar.** `.appshell__sidebar` is now `var(--surface-1)` with a
  `--color-border` right edge (was `--color-primary-dark`). Section headers are
  `--text-subtle` (muted gray-navy, not white-on-navy). The active nav item is a
  12% `color-mix` tint of `--color-primary` behind `--color-primary` text/icon,
  not a solid fill. `--color-primary` / `--color-primary-dark` keep their values
  and still drive page headers, primary buttons and links — only the shell's
  *usage* changed. Mobile scrim `rgba(15,35,80,.4)` kept (overlay, not text).
- **B — numeric legibility.** New `.ref-code` class (mono, `tabular-nums`,
  600 weight, `white-space: nowrap`) and a `tabular-nums` rule on `.num`,
  `.metric-tile__value`, `table.data .num` and `.kv dd`. `.kv` label↔value
  spacing/weight tightened. Not a font-family change.
- **C — reference codes.** `#<id>` is gone from every screen. Codes are
  `{PREFIX}-{id:06d}` (`AP-000004`, `CN-000012`), computed at serialization
  backend-side (`app/core/references.py`) and mirrored in `src/lib/reference.ts`.
  `<RefCode>` component renders them; breadcrumbs and the audit-log entity column
  use them. ID-lookup inputs accept **either** the raw number or the code
  (`coerceId()`), and their labels say so.
- **D — dashboard charts.** `recharts` added. Portfolio tab: contracts-by-status
  donut + DPD aging bar chart. Credit & Risk tab: customers-by-risk-band donut.
  Every chart is fed only by data an existing `summary` endpoint already returns
  — no fabricated time-series. The old DPD detail table is kept below the chart.
- **E — Appearance.** New tab **inside** Configuration (admin), not a nav item.
  Colour pickers for `--color-primary/-secondary/-warm/-danger` with live
  preview + Reset. Stored in `localStorage["rc.appearance"]` only, applied as
  `:root` custom properties at runtime (`initAppearance()` in `main.tsx` before
  first paint). Explicitly **not** server-synced and **not** maker-checker
  gated; the panel says so.

Tests: `frontend/src/test/step14.test.tsx` (14) + `tests/test_references.py` (7).
Full suite green — backend 209, frontend 55, `tsc` + `vite build` clean.
