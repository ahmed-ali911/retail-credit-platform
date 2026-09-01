import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  /** Roles that see this item. Omit = every signed-in user. */
  roles?: string[];
}

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/customers/new", label: "New Customer" },
  { to: "/products/new", label: "New Product" },
  { to: "/applications/new", label: "New Application" },
  {
    to: "/review",
    label: "Review Queue",
    roles: ["credit_officer", "credit_manager", "admin"],
  },
  {
    to: "/reconciliation",
    label: "Reconciliation",
    roles: ["finance_officer", "admin"],
  },
  {
    to: "/approvals",
    label: "Approvals",
    roles: ["finance_officer", "credit_manager", "admin"],
  },
  { to: "/config", label: "Configuration", roles: ["admin"] },
  { to: "/audit", label: "Audit Log", roles: ["admin", "credit_manager"] },
];

export function canSee(item: NavItem, role: string | undefined): boolean {
  if (!item.roles) return true;
  return role != null && item.roles.includes(role);
}

export function Shell() {
  const { user, logout } = useAuth();
  const items = NAV.filter((item) => canSee(item, user?.role));

  return (
    <div className="shell">
      <nav className="shell__nav">
        <div className="shell__brand">Retail Credit</div>
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => (isActive ? "active" : undefined)}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="shell__main">
        <header className="shell__header">
          <div className="shell__user">
            Signed in as <strong>{user?.username}</strong> ({user?.role})
          </div>
          <button className="btn-secondary" onClick={logout}>
            Log out
          </button>
        </header>
        <main className="shell__content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
