import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { CostCenter, SectionPermission, UserReference } from "../types";

type Draft = Pick<CostCenter, "code" | "name" | "pohoda_code" | "active">;
const empty: Draft = { code: "", name: "", pohoda_code: "", active: true };

export function CostCenters() {
  const [rows, setRows] = useState<CostCenter[]>([]);
  const [draft, setDraft] = useState<Draft>(empty);
  const [approvers, setApprovers] = useState<UserReference[]>([]);
  const [permissions, setPermissions] = useState<SectionPermission[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(() => Promise.all([
    api<CostCenter[]>("/cost-centers?include_inactive=true"),
    api<UserReference[]>("/users?role=APPROVER"),
    api<SectionPermission[]>("/section-permissions?include_inactive=true"),
  ]).then(([centres, users, grants]) => {
    setRows(centres);
    setApprovers(users.filter((user) => Boolean(user.subject)));
    setPermissions(grants.filter((grant) => Boolean(grant.cost_center?.id)));
  }).catch((e) => setError(e.message)), []);
  useEffect(() => { void load(); }, [load]);
  const create = async () => {
    try { await api("/cost-centers", { method: "POST", body: JSON.stringify(draft) }); setDraft(empty); setError(""); await load(); }
    catch (e) { setError((e as Error).message); }
  };
  const update = async (row: CostCenter, changes: Partial<Draft>) => {
    try { await api(`/cost-centers/${row.id}`, { method: "PUT", body: JSON.stringify({ code: row.code, name: row.name, pohoda_code: row.pohoda_code, active: row.active, ...changes }) }); setError(""); await load(); }
    catch (e) { setError((e as Error).message); }
  };
  const permitted = (subject: string, centerId: string) => permissions.some((row) => row.approver_subject === subject && row.cost_center?.id === centerId && row.active);
  const setPermission = async (subject: string, centerId: string, active: boolean) => {
    try {
      await api("/section-permissions", { method: "PUT", body: JSON.stringify({ approver_subject: subject, cost_center_id: centerId, active }) });
      setError(""); await load();
    } catch (e) { setError((e as Error).message); }
  };
  return <section>
    <div className="section-heading"><div><p className="eyebrow">Číselníky a oprávnění</p><h1>Sekce</h1><p className="muted">Sekce používají existující číselník středisek. Oprávnění schvalovatele je explicitní a lze je auditovaně odebrat.</p></div></div>
    {error && <div className="alert danger">{error}</div>}
    <div className="card centre-create"><input aria-label="Kód" placeholder="Kód" value={draft.code} onChange={e=>setDraft({...draft,code:e.target.value})}/><input aria-label="Název" placeholder="Název" value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})}/><input aria-label="POHODA kód" placeholder="POHODA kód" value={draft.pohoda_code} onChange={e=>setDraft({...draft,pohoda_code:e.target.value})}/><button className="button primary" onClick={()=>void create()}>Přidat sekci</button></div>
    <div className="table-wrap"><table><thead><tr><th>Kód</th><th>Název</th><th>POHODA</th><th>Stav</th><th>Akce</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}><td>{row.code}</td><td>{row.name}</td><td>{row.pohoda_code}</td><td>{row.active?"Aktivní":"Neaktivní"}</td><td><button className="button secondary" onClick={()=>void update(row,{active:!row.active})}>{row.active?"Deaktivovat":"Aktivovat"}</button></td></tr>)}</tbody></table></div>
    <div className="card"><div className="card-title"><div><h2>Schvalovatelé podle sekcí</h2><p>Změna platí okamžitě pro každé nové rozhodnutí.</p></div></div>
      <div className="table-wrap"><table><thead><tr><th>Schvalovatel</th>{rows.filter(row=>row.active).map(row=><th key={row.id}>{row.code}</th>)}</tr></thead><tbody>{approvers.map(approver=><tr key={approver.subject}><td><strong>{approver.username}</strong><small>{approver.email || approver.subject}</small></td>{rows.filter(row=>row.active).map(row=><td key={row.id}><label className="check-row"><input aria-label={`${approver.username} – ${row.code}`} type="checkbox" checked={permitted(approver.subject,row.id)} onChange={event=>void setPermission(approver.subject,row.id,event.target.checked)}/><span>Povoleno</span></label></td>)}</tr>)}</tbody></table></div>
    </div>
  </section>;
}
