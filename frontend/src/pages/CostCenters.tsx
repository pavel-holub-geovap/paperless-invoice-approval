import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { CostCenter } from "../types";

type Draft = Pick<CostCenter, "code" | "name" | "pohoda_code" | "active">;
const empty: Draft = { code: "", name: "", pohoda_code: "", active: true };

export function CostCenters() {
  const [rows, setRows] = useState<CostCenter[]>([]);
  const [draft, setDraft] = useState<Draft>(empty);
  const [error, setError] = useState("");
  const load = useCallback(() => api<CostCenter[]>("/cost-centers?include_inactive=true").then(setRows).catch((e) => setError(e.message)), []);
  useEffect(() => { void load(); }, [load]);
  const create = async () => {
    try { await api("/cost-centers", { method: "POST", body: JSON.stringify(draft) }); setDraft(empty); setError(""); await load(); }
    catch (e) { setError((e as Error).message); }
  };
  const update = async (row: CostCenter, changes: Partial<Draft>) => {
    try { await api(`/cost-centers/${row.id}`, { method: "PUT", body: JSON.stringify({ code: row.code, name: row.name, pohoda_code: row.pohoda_code, active: row.active, ...changes }) }); setError(""); await load(); }
    catch (e) { setError((e as Error).message); }
  };
  return <section>
    <div className="section-heading"><div><p className="eyebrow">Číselníky</p><h1>Střediska</h1><p className="muted">Střediska nejsou hard-coded; POHODA kód se uchovává pro budoucí exportní etapu.</p></div></div>
    {error && <div className="alert danger">{error}</div>}
    <div className="card centre-create"><input aria-label="Kód" placeholder="Kód" value={draft.code} onChange={e=>setDraft({...draft,code:e.target.value})}/><input aria-label="Název" placeholder="Název" value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})}/><input aria-label="POHODA kód" placeholder="POHODA kód" value={draft.pohoda_code} onChange={e=>setDraft({...draft,pohoda_code:e.target.value})}/><button className="button primary" onClick={()=>void create()}>Přidat středisko</button></div>
    <div className="table-wrap"><table><thead><tr><th>Kód</th><th>Název</th><th>POHODA</th><th>Stav</th><th>Akce</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}><td>{row.code}</td><td>{row.name}</td><td>{row.pohoda_code}</td><td>{row.active?"Aktivní":"Neaktivní"}</td><td><button className="button secondary" onClick={()=>void update(row,{active:!row.active})}>{row.active?"Deaktivovat":"Aktivovat"}</button></td></tr>)}</tbody></table></div>
  </section>;
}
