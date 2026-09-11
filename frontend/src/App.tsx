import { useCallback, useEffect, useState } from "react";
import { api, setApiUser } from "./lib/api";
import { Approvals } from "./pages/Approvals";
import { AdminPage } from "./pages/AdminPage";
import { Dashboard } from "./pages/Dashboard";
import { Exports } from "./pages/Exports";
import { HelpPage } from "./help/HelpPage";
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
        if (!current.roles.includes("QUEUE_MANAGER") && route.page === "dashboard" && !route.invoiceId) {
          navigate(current.roles.includes("ADMIN") ? "/admin" : "/approvals", true);
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

  const home = user.roles.includes("QUEUE_MANAGER") ? "/" : user.roles.includes("ADMIN") ? "/admin" : "/approvals";
  return <>
    <header>
      <a className="brand" href={home} onClick={nav(home)}><span>PI</span><strong>Schvalování faktur</strong></a>
      <nav aria-label="Hlavní navigace">
        {user.roles.includes("QUEUE_MANAGER") && <>
          <a className={route.page === "dashboard" ? "active" : ""} href="/" onClick={nav("/")}>Fronta</a>
          <a className={route.page === "exports" ? "active" : ""} href="/exports" onClick={nav("/exports")}>Exporty</a>
        </>}
        {user.roles.includes("ADMIN") && <a className={route.page === "admin" ? "active" : ""} href="/admin" onClick={nav("/admin")}>Administrace</a>}
        {user.roles.includes("APPROVER") && <a className={route.page === "approvals" ? "active" : ""} href="/approvals" onClick={nav("/approvals")}>Moje schválení</a>}
        <a className={route.page === "help" ? "active" : ""} href="/help" onClick={nav("/help")}><span className="help-nav-icon" aria-hidden="true">?</span>Nápověda</a>
      </nav>
      <div className="user"><span>{user.username}</span><button onClick={() => void api("/auth/logout", { method: "POST" }).then(() => location.reload())}>Odhlásit</button></div>
    </header>
    <main className="app-shell">
      {route.page === "dashboard" && (user.roles.includes("QUEUE_MANAGER") ? <Dashboard user={user} invoiceId={route.invoiceId} onNavigate={navigate}/> : <section className="empty"><h1>Nemáte oprávnění k frontě</h1></section>)}
      {route.page === "approvals" && (user.roles.includes("APPROVER") ? <Approvals user={user} history={route.history} uploaded={route.uploaded} historyInvoiceId={route.historyInvoiceId} onNavigate={navigate}/> : <section className="empty"><h1>Nemáte oprávnění ke schvalování</h1></section>)}
      {route.page === "admin" && (user.roles.includes("ADMIN") ? <AdminPage/> : <section className="empty"><h1>Nemáte oprávnění k administraci</h1></section>)}
      {route.page === "exports" && (user.roles.includes("QUEUE_MANAGER") ? <Exports/> : <section className="empty"><h1>Nemáte oprávnění k exportům</h1></section>)}
      {route.page === "help" && <HelpPage/>}
    </main>
  </>;
}
