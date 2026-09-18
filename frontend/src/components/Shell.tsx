import { Fragment, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BarChart3,
  Boxes,
  CheckSquare,
  ChevronDown,
  ClipboardCheck,
  CreditCard,
  FileMinus2,
  FilePlus2,
  FileSignature,
  Gauge,
  GitCompareArrows,
  HandCoins,
  Landmark,
  LayoutDashboard,
  Menu,
  Package,
  PackagePlus,
  Plus,
  ScrollText,
  Settings,
  ShieldAlert,
  UserPlus,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { referenceForSegment } from "../lib/reference";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  /** Roles that see this item. Omit = every signed-in user. */
  roles?: string[];
}

interface NavGroup {
  id: string;
  title: string;
  items: NavItem[];
}

const DIRECTORY_ROLES = [
  "sales_employee",
  "credit_officer",
  "credit_manager",
  "finance_officer",
  "admin",
];

/**
 * The sidebar, grouped by business domain. Every route the app has today has a
 * home here; capabilities that have no screen yet (Payments, Accounting
 * Events, Settlements, portfolio Exposure, Overdue, User Management) are
 * tracked in docs/frontend-redesign-audit.md, not shown as dead links.
 *
 * Phase 1 (frontend redesign) — regrouped from the earlier flat "Operations"
 * bucket into Origination / Servicing / Collections / Risk & Finance /
 * Controls, matching how the business actually separates these functions.
 * Every `to` / `roles` / label is unchanged from before the regroup — this
 * only moves items between group headers; it does not change what any role
 * can see or reach.
 */
const NAV: NavGroup[] = [
  {
    id: "overview",
    title: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
    ],
  },
  {
    id: "origination",
    title: "Origination",
    items: [
      { to: "/customers", label: "Customers", icon: Users, end: true, roles: DIRECTORY_ROLES },
      { to: "/products", label: "Products", icon: Package, end: true, roles: DIRECTORY_ROLES },
      {
        to: "/review",
        label: "Review Queue",
        icon: ClipboardCheck,
        end: true,
        roles: ["credit_officer", "credit_manager", "admin"],
      },
    ],
  },
  {
    id: "servicing",
    title: "Servicing",
    items: [
      { to: "/contracts", label: "Contracts", icon: FileSignature, end: true, roles: DIRECTORY_ROLES },
      { to: "/inventory", label: "Inventory", icon: Boxes, roles: ["finance_officer", "admin"] },
      {
        to: "/payments",
        label: "Payment Operations",
        icon: CreditCard,
        end: true,
        // matches app/api/payment_gateway.py's _PAYMENT_STAFF_ROLES
        roles: ["sales_employee", "finance_officer", "admin"],
      },
    ],
  },
  {
    id: "collections",
    title: "Collections",
    items: [
      {
        to: "/collections",
        label: "Collections",
        icon: HandCoins,
        end: true,
        // matches app/api/collections.py's _VIEW_ROLES exactly
        roles: ["collections_officer", "credit_manager", "admin"],
      },
      {
        to: "/write-offs",
        label: "Write-offs & Recoveries",
        icon: FileMinus2,
        end: true,
        // matches app/api/write_off.py's _VIEW_ROLES exactly
        roles: ["collections_officer", "finance_officer", "credit_manager", "admin"],
      },
    ],
  },
  {
    id: "risk-finance",
    title: "Risk & Finance",
    items: [
      {
        to: "/ecl",
        label: "ECL & Provision",
        icon: ShieldAlert,
        end: true,
        // matches app/api/ecl.py's _VIEW_ROLES exactly
        roles: ["finance_officer", "credit_manager", "admin"],
      },
      {
        to: "/ecl/config",
        label: "ECL Model & Rules",
        icon: Settings,
        end: true,
        roles: ["finance_officer", "credit_manager", "admin"],
      },
      {
        to: "/reconciliation",
        label: "Bank Reconciliation",
        icon: Landmark,
        roles: ["finance_officer", "admin"],
      },
      {
        to: "/payments/reconciliation",
        label: "Gateway Reconciliation",
        icon: GitCompareArrows,
        end: true,
        roles: ["finance_officer", "admin"],
      },
      {
        to: "/reports",
        label: "Reports",
        icon: BarChart3,
        roles: ["finance_officer", "credit_manager", "admin"],
      },
      {
        to: "/snapshot",
        label: "Snapshot",
        icon: Gauge,
        roles: ["finance_officer", "credit_manager", "admin"],
      },
    ],
  },
  {
    id: "controls",
    title: "Controls",
    items: [
      {
        to: "/approvals",
        label: "Approvals",
        icon: CheckSquare,
        roles: ["finance_officer", "credit_manager", "admin"],
      },
      {
        to: "/audit",
        label: "Audit Logs",
        icon: ScrollText,
        roles: ["admin", "credit_manager"],
      },
      { to: "/config", label: "Configuration", icon: Settings, roles: ["admin"] },
    ],
  },
];

/**
 * Phase 1 — the compact "New" quick-create action (section 9 of the redesign
 * brief): these three used to be permanent, equal-weight sidebar items mixed
 * into the old "Operations" group. Same routes, same (unrestricted)
 * visibility — just no longer competing with the directory/lookup items for
 * top-level attention.
 */
const QUICK_CREATE: NavItem[] = [
  { to: "/applications/new", label: "New Application", icon: FilePlus2 },
  { to: "/customers/new", label: "New Customer", icon: UserPlus },
  { to: "/products/new", label: "New Product", icon: PackagePlus },
];

export function canSee(item: { roles?: string[] }, role: string | undefined): boolean {
  if (!item.roles) return true;
  return role != null && item.roles.includes(role);
}

const COLLAPSED_KEY = "rc.nav.collapsed";

function loadCollapsed(): Set<string> {
  try {
    const raw = localStorage.getItem(COLLAPSED_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function saveCollapsed(set: Set<string>): void {
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...set]));
  } catch {
    /* ignore */
  }
}

// --------------------------------------------------------------------------- //
// Breadcrumb
// --------------------------------------------------------------------------- //
const CRUMB_LABELS: Record<string, string> = {
  customers: "Customers",
  products: "Products",
  applications: "Applications",
  offers: "Offers",
  contracts: "Contracts",
  review: "Review Queue",
  reconciliation: "Reconciliation",
  approvals: "Approvals",
  collections: "Collections",
  snapshot: "Snapshot",
  inventory: "Inventory",
  reports: "Reports",
  ecl: "ECL & Provision",
  config: "Configuration",
  audit: "Audit Logs",
  new: "New",
  offer: "Offer",
};

function crumbLabel(seg: string, prev: string | undefined): string {
  if (CRUMB_LABELS[seg]) return CRUMB_LABELS[seg];
  if (/^\d+$/.test(seg)) return referenceForSegment(prev, seg); // e.g. CN-000012
  return seg.charAt(0).toUpperCase() + seg.slice(1);
}

function Breadcrumb() {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);
  const crumbs =
    segments.length === 0
      ? ["Dashboard"]
      : segments.map((s, i) => crumbLabel(s, segments[i - 1]));

  return (
    // a <div>, not <nav>: the sidebar is the single primary navigation landmark
    <div className="breadcrumb" aria-label="Breadcrumb">
      <span className="breadcrumb__root">Retail Credit</span>
      {crumbs.map((c, i) => (
        <Fragment key={i}>
          <span className="breadcrumb__sep" aria-hidden>
            /
          </span>
          <span
            className={
              i === crumbs.length - 1 ? "breadcrumb__current" : "breadcrumb__root"
            }
          >
            {c}
          </span>
        </Fragment>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// User menu
// --------------------------------------------------------------------------- //
function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const initials = (user?.username ?? "?").slice(0, 2).toUpperCase();

  return (
    <div className="usermenu" ref={ref}>
      {/* always in the DOM for a11y + tests */}
      <span className="sr-only">
        Signed in as <strong>{user?.username}</strong> ({user?.role})
      </span>
      <button
        type="button"
        className="usermenu__button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="usermenu__avatar" aria-hidden>
          {initials}
        </span>
        <span>
          <span className="usermenu__name">{user?.username}</span>
          <br />
          <span className="usermenu__role">{user?.role?.replace(/_/g, " ")}</span>
        </span>
        <ChevronDown size={14} aria-hidden />
      </button>
      {open && (
        <div className="usermenu__panel" role="menu">
          <div className="usermenu__identity">
            Signed in as <strong>{user?.username}</strong>
            <br />
            {user?.role?.replace(/_/g, " ")}
          </div>
          <button type="button" role="menuitem" onClick={logout}>
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Shell
// --------------------------------------------------------------------------- //
export function Shell() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed);
  const [navOpen, setNavOpen] = useState(false);

  // close the mobile drawer on navigation
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  function toggleGroup(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      saveCollapsed(next);
      return next;
    });
  }

  const visibleGroups = NAV.map((g) => ({
    ...g,
    items: g.items.filter((it) => canSee(it, user?.role)),
  })).filter((g) => g.items.length > 0);

  const visibleQuickCreate = QUICK_CREATE.filter((it) => canSee(it, user?.role));

  return (
    <div className={`appshell${navOpen ? " appshell--nav-open" : ""}`}>
      <div
        className="appshell__scrim"
        onClick={() => setNavOpen(false)}
        aria-hidden
      />

      <aside className="appshell__sidebar">
        <div className="appshell__brand">
          <span className="appshell__brand-mark" aria-hidden>
            RC
          </span>
          <span className="appshell__brand-text">
            <span className="appshell__brand-name">Retail Credit</span>
            <span className="appshell__brand-sub">Installment Platform</span>
          </span>
        </div>

        <nav className="appshell__nav" aria-label="Primary">
          {visibleQuickCreate.length > 0 && (
            <details className="nav-new">
              <summary className="nav-new__trigger">
                <Plus size={15} aria-hidden />
                New
              </summary>
              <div className="nav-new__items">
                {visibleQuickCreate.map((item) => {
                  const Icon = item.icon;
                  return (
                    <Link key={item.to} to={item.to} className="navlink">
                      <Icon size={16} className="navlink__icon" aria-hidden />
                      <span className="navlink__label">{item.label}</span>
                    </Link>
                  );
                })}
              </div>
            </details>
          )}
          {visibleGroups.map((group) => {
            const isCollapsed = collapsed.has(group.id);
            return (
              <div
                key={group.id}
                className={`navgroup${isCollapsed ? " navgroup--collapsed" : ""}`}
              >
                <button
                  type="button"
                  className="navgroup__header"
                  aria-expanded={!isCollapsed}
                  onClick={() => toggleGroup(group.id)}
                >
                  <span>{group.title}</span>
                  <ChevronDown
                    size={13}
                    className="navgroup__chevron"
                    aria-hidden
                  />
                </button>
                <div className="navgroup__items">
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <NavLink
                        key={item.to}
                        to={item.to}
                        end={item.end}
                        className={({ isActive }) =>
                          isActive ? "navlink active" : "navlink"
                        }
                      >
                        <Icon size={16} className="navlink__icon" aria-hidden />
                        <span className="navlink__label">{item.label}</span>
                      </NavLink>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>
      </aside>

      <div className="appshell__main">
        <header className="appshell__topbar">
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", minWidth: 0 }}>
            <button
              type="button"
              className="appshell__menu-toggle"
              aria-label="Open navigation"
              onClick={() => setNavOpen(true)}
            >
              <Menu size={18} aria-hidden />
            </button>
            <Breadcrumb />
          </div>
          <UserMenu />
        </header>

        <main className="appshell__content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
