import { useCallback, useEffect, useState } from "react";
import { api, setApiUser } from "./lib/api";
import { Approvals } from "./pages/Approvals";
import { CostCenters } from "./pages/CostCenters";
import { Dashboard } from "./pages/Dashboard";
import { Exports } from "./pages/Exports";
import { parseRoute, type AppRoute } from "./routing";
import type { User } from "./types";

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [route, setRoute] = useState<AppRoute>(() => parseRoute(window.location.pathname));

  const navigate = useCallback((path: string, replace = false) => {
    if (replace) window.history.replaceState({}, "", path);
    else window.history.pushState({}, "", path);
    setRoute(parseRoute(path));
  }, []);

  useEffect(() => {
    const onPopState = () => setRoute(parseRoute(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    api<User>("/auth/me")
      .then((current) => {
        setApiUser(current);
        setUser(current);
        if (!current.roles.includes("QUEUE_MANAGER") && route.page === "dashboard") {
          navigate("/approvals", true);
        }
      })
      .catch(() => setUser(null));
  }, []); // Identity and the initial deep link are intentionally evaluated once.

  const nav = (path: string) => (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    navigate(path);
  };

  if (user === undefined) return <main className="center"><div className="spinner"/><p>Načítám aplikaci…</p></main>;
  if (!user) return <main className="login"><div className="login-card"><div className="brand-mark">PI</div><p className="eyebrow">Paperless Invoice Approval</p><h1>Faktury pod kontrolou</h1><p>Bezpečné vytěžení, věcná kontrola, rozúčtování a dohledatelné schválení.</p><a className="button primary large" href="/api/auth/login">Přihlásit přes Keycloak</a></div></main>;

  const home = user.roles.includes("QUEUE_MANAGER") ? "/" : "/approvals";
  return <>
    <header>
      <a className="brand" href={home} onClick={nav(home)}><span>PI</span><strong>Schvalování faktur</strong></a>
      <nav>
        {user.roles.includes("QUEUE_MANAGER") && <>
          <a className={route.page === "dashboard" ? "active" : ""} href="/" onClick={nav("/")}>Fronta</a>
          <a className={route.page === "centres" ? "active" : ""} href="/cost-centers" onClick={nav("/cost-centers")}>Střediska</a>
          <a className={route.page === "exports" ? "active" : ""} href="/exports" onClick={nav("/exports")}>Exporty</a>
        </>}
        {user.roles.includes("APPROVER") && <a className={route.page === "approvals" ? "active" : ""} href="/approvals" onClick={nav("/approvals")}>Moje schválení</a>}
      </nav>
      <div className="user"><span>{user.username}</span><button onClick={() => void api("/auth/logout", { method: "POST" }).then(() => location.reload())}>Odhlásit</button></div>
    </header>
    <main className="app-shell">
      {route.page === "dashboard" && <Dashboard user={user} invoiceId={route.invoiceId} onNavigate={navigate}/>}
      {route.page === "approvals" && <Approvals/>}
      {route.page === "centres" && <CostCenters/>}
      {route.page === "exports" && <Exports/>}
    </main>
  </>;
}
