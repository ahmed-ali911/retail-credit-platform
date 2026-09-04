import { Fragment, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BarChart3,
  Boxes,
  CheckSquare,
  ChevronDown,
  ClipboardCheck,
  FilePlus2,
  Gauge,
  HandCoins,
  Landmark,
  LayoutDashboard,
  Menu,
  Package,
  PackagePlus,
  ScrollText,
  Settings,
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
 * home here; capabilities that have no screen yet (Contracts list, Payments,
 * Accounting Events, Settlements, portfolio Exposure, Overdue, User Management)
 * are tracked in docs/frontend-redesign-audit.md, not shown as dead links.
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
    id: "operations",
    title: "Operations",
    items: [
      { to: "/applications/new", label: "New Application", icon: FilePlus2 },
      { to: "/customers", label: "Customers", icon: Users, end: true, roles: DIRECTORY_ROLES },
      { to: "/customers/new", label: "New Customer", icon: UserPlus },
      { to: "/products", label: "Products", icon: Package, end: true, roles: DIRECTORY_ROLES },
      { to: "/products/new", label: "New Product", icon: PackagePlus },
      { to: "/inventory", label: "Inventory", icon: Boxes, roles: ["finance_officer", "admin"] },
    ],
  },
  {
    id: "credit",
    title: "Credit & Risk",
    items: [
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
    ],
  },
  {
    id: "finance",
    title: "Finance",
    items: [
      {
        to: "/reconciliation",
        label: "Reconciliation",
        icon: Landmark,
        roles: ["finance_officer", "admin"],
      },
    ],
  },
  {
    id: "portfolio",
    title: "Portfolio",
    items: [
      {
        to: "/snapshot",
        label: "Snapshot",
        icon: Gauge,
        roles: ["finance_officer", "credit_manager", "admin"],
      },
      {
        to: "/reports",
        label: "Reports",
        icon: BarChart3,
        roles: ["finance_officer", "credit_manager", "admin"],
      },
    ],
  },
  {
    id: "control",
    title: "Control",
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
    ],
  },
  {
    id: "administration",
    title: "Administration",
    items: [
      { to: "/config", label: "Configuration", icon: Settings, roles: ["admin"] },
    ],
  },
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
