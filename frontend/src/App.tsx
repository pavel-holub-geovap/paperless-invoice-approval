import { useEffect, useState } from "react";
import { api, setApiUser } from "./lib/api";
import type { User } from "./types";
import { Dashboard } from "./pages/Dashboard";
import { Approvals } from "./pages/Approvals";
import { Exports } from "./pages/Exports";

type Tab = "dashboard" | "approvals" | "exports";
export default function App(){const [user,setUser]=useState<User|null|undefined>(undefined);const [tab,setTab]=useState<Tab>("dashboard");useEffect(()=>{api<User>("/auth/me").then(u=>{setApiUser(u);setUser(u);if(!u.roles.includes("QUEUE_MANAGER"))setTab("approvals")}).catch(()=>setUser(null))},[]);if(user===undefined)return <main className="center"><div className="spinner"/><p>Načítám aplikaci…</p></main>;if(!user)return <main className="login"><div className="login-card"><div className="brand-mark">PI</div><p className="eyebrow">Paperless Invoice Approval</p><h1>Faktury pod kontrolou</h1><p>Bezpečné vytěžení, věcná kontrola, rozúčtování a dohledatelné schválení.</p><a className="button primary large" href="/api/auth/login">Přihlásit přes Keycloak</a></div></main>;return <><header><button className="brand" onClick={()=>setTab(user.roles.includes("QUEUE_MANAGER")?"dashboard":"approvals")}><span>PI</span><strong>Schvalování faktur</strong></button><nav>{user.roles.includes("QUEUE_MANAGER")&&<><button className={tab==="dashboard"?"active":""} onClick={()=>setTab("dashboard")}>Fronta</button><button className={tab==="exports"?"active":""} onClick={()=>setTab("exports")}>Exporty</button></>}{user.roles.includes("APPROVER")&&<button className={tab==="approvals"?"active":""} onClick={()=>setTab("approvals")}>Moje schválení</button>}</nav><div className="user"><span>{user.username}</span><button onClick={()=>void api("/auth/logout",{method:"POST"}).then(()=>location.reload())}>Odhlásit</button></div></header><main className="app-shell">{tab==="dashboard"&&<Dashboard user={user}/>} {tab==="approvals"&&<Approvals/>} {tab==="exports"&&<Exports/>}</main></>}

