import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/customers/new", label: "New Customer" },
  { to: "/products/new", label: "New Product" },
  { to: "/applications/new", label: "New Application" },
];

export function Shell() {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <nav className="shell__nav">
        <div className="shell__brand">Retail Credit</div>
        {NAV.map((item) => (
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
