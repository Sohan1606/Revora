import { Link } from "react-router-dom";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";

const NAV = [
  { to: "/console", label: "Control Center", end: true },
  { to: "/console/cases", label: "Recovery Cases" },
  { to: "/console/experiments", label: "Experiments" },
  { to: "/console/policies", label: "Policies" },
  { to: "/console/simulator", label: "Demo Lab" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="flex h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/40">
        <div className="border-b border-zinc-800 px-4 py-4">
          <Link to="/" className="text-lg font-semibold tracking-wide text-red-500 hover:text-red-400">
            REVORA
          </Link>
          <div className="text-[11px] text-zinc-500">Revenue Recovery Intelligence</div>
        </div>
        <nav className="flex-1 space-y-1 p-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `block rounded-md px-3 py-2 text-sm ${
                  isActive
                    ? "bg-zinc-800 text-white"
                    : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-zinc-800 p-3 text-xs text-zinc-500">
          <div className="truncate text-zinc-300">{user?.email}</div>
          <div className="mt-0.5">
            role: <span className="text-zinc-300">{user?.role}</span> ·{" "}
            <span className="text-zinc-500">auth: {user?.auth_mode}</span>
          </div>
          <button onClick={logout} className="mt-2 text-red-400 hover:text-red-300">
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
