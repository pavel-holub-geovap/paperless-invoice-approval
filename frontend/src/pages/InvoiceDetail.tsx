import { useEffect, useMemo, useState } from "react";
import { api, money } from "../lib/api";
import type { CostCenter, Invoice, User, UserReference } from "../types";
import { StatusBadge } from "../components/StatusBadge";

const editableFields = [
  ["supplier_name", "Dodavatel"], ["supplier_ico", "IČO"], ["supplier_dic", "DIČ"], ["supplier_address", "Adresa"], ["invoice_number", "Číslo faktury"],
  ["variable_symbol", "Variabilní symbol"], ["issue_date", "Datum vystavení"], ["taxable_supply_date", "DUZP"],
  ["due_date", "Splatnost"], ["currency", "Měna"], ["bank_account", "Bankovní účet"], ["bank_code", "Kód banky"], ["iban", "IBAN"], ["swift_bic", "SWIFT/BIC"],
  ["total_without_vat", "Základ bez DPH"], ["total_vat", "DPH celkem"], ["total_amount", "Celkem"], ["description", "Popis"],
] as const;

const formFromInvoice = (invoice: Invoice) => Object.fromEntries(editableFields.map(([key]) => [key, String(invoice.data[key] ?? "")]));

function shown(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function InvoiceDetail({ invoice, user, onBack, onRefresh }: { invoice: Invoice; user: User; onBack: () => void; onRefresh: () => void }) {
  const [form, setForm] = useState<Record<string, string>>(() => formFromInvoice(invoice));
  const [centres, setCentres] = useState<CostCenter[]>([]);
  const [approvers, setApprovers] = useState<UserReference[]>([]);
  const [approverChoices, setApproverChoices] = useState<Record<string, string[]>>(() => Object.fromEntries(invoice.allocations.map((a) => [a.id, a.assignments.map((x) => x.approver_subject)])));
  const [allocationRows, setAllocationRows] = useState(() => invoice.allocations.map((a) => ({ cost_center_id: a.cost_center.id, amount: String(a.amount) })));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { void Promise.all([api<CostCenter[]>("/cost-centers"), api<UserReference[]>("/users?role=APPROVER")]).then(([c,a])=>{setCentres(c);setApprovers(a)}); }, []);
  useEffect(() => { setForm(formFromInvoice(invoice)); }, [invoice]);
  useEffect(() => {
    if (!["AI_PENDING", "AI_PROCESSING"].includes(invoice.ai_status)) return;
    const timer = window.setInterval(onRefresh, 2000);
    return () => window.clearInterval(timer);
  }, [invoice.ai_status, onRefresh]);
  const evidence = useMemo(() => Object.fromEntries(invoice.extracted_fields.map((f) => [f.field_name, f.source_text])), [invoice]);
  const latestAI = invoice.ai.latest;
  const isManager = user.roles.includes("QUEUE_MANAGER");
  const call = async (path: string, init: RequestInit) => {
    try { await api(path, init); setMessage("Uloženo."); setError(""); onRefresh(); } catch (e) { setError((e as Error).message); }
  };
  const saveData = () => {
    const changes = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v === "" ? null : v]));
    void call(`/invoices/${invoice.id}`, { method: "PATCH", body: JSON.stringify({ changes }) });
  };
  const saveAllocations = () => void call(`/invoices/${invoice.id}/allocations`, { method: "PUT", body: JSON.stringify({ allocations: allocationRows.map((row) => ({ ...row, amount: row.amount })) }) });
  const saveApprovers = (allocationId: string) => void call(`/invoices/${invoice.id}/allocations/${allocationId}/approvers`, { method: "PUT", body: JSON.stringify({ approver_subjects: approverChoices[allocationId] || [] }) });
  return <section>
    <button className="back" onClick={onBack}>← Zpět na frontu</button>
    <div className="section-heading"><div><p className="eyebrow">Paperless #{invoice.paperless_document_id} · revize {invoice.current_revision_number}</p><h1>{String(invoice.data.invoice_number || invoice.paperless.title || "Faktura bez názvu")}</h1><p className="muted">{String(invoice.data.supplier_name || invoice.paperless.correspondent || "Neznámý dodavatel")} · {money(String(invoice.data.total_amount || ""), String(invoice.data.currency || "CZK"))}</p></div><div className="heading-badges"><StatusBadge value={invoice.paperless.sync_status} /><StatusBadge value={invoice.ai_status} /><StatusBadge value={invoice.status} /></div></div>
    {message && <div className="alert success">{message}</div>}{error && <div className="alert danger">{error}</div>}
    <div className="detail-grid">
      <div className="pdf-panel"><iframe title="Originální faktura" src={`/api/invoices/${invoice.id}/pdf`} /><a className="button secondary" href={`/api/invoices/${invoice.id}/pdf`} target="_blank" rel="noreferrer">Otevřít PDF v novém okně</a></div>
      <div className="work-panel">
        <div className="card"><div className="card-title"><div><h2>Zdrojová metadata</h2><p>Načteno výhradně přes Paperless REST API</p></div></div>
          <dl className="metadata-grid"><div><dt>Název</dt><dd>{invoice.paperless.title || "—"}</dd></div><div><dt>Vytvořeno</dt><dd>{invoice.paperless.created_at ? new Date(invoice.paperless.created_at).toLocaleString("cs-CZ") : "—"}</dd></div><div><dt>Korespondent</dt><dd>{invoice.paperless.correspondent || "—"}</dd></div><div><dt>Původní soubor</dt><dd>{invoice.paperless.original_filename || "—"}</dd></div><div><dt>Tagy</dt><dd>{invoice.paperless.tags.join(", ") || "—"}</dd></div><div><dt>Poslední synchronizace</dt><dd>{invoice.paperless.last_synced_at ? new Date(invoice.paperless.last_synced_at).toLocaleString("cs-CZ") : "—"}</dd></div></dl>
          {invoice.paperless.sync_error && <div className="alert danger">{invoice.paperless.sync_error}</div>}
        </div>
        <div className="card ai-card"><div className="card-title"><div><h2>AI extrakce</h2><p>Technický stav je oddělený od workflow faktury.</p></div><StatusBadge value={invoice.ai_status}/></div>
          {latestAI ? <>
            <dl className="metadata-grid"><div><dt>Model</dt><dd>{latestAI.model}</dd></div><div><dt>Verze</dt><dd>{latestAI.schema_version} · {latestAI.prompt_version}</dd></div><div><dt>Doba inference</dt><dd>{latestAI.duration_ms != null ? `${(latestAI.duration_ms / 1000).toFixed(2)} s` : "—"}</dd></div><div><dt>Běh</dt><dd>#{latestAI.extraction_revision}{latestAI.applied ? " · použit" : latestAI.requires_confirmation ? " · čeká na potvrzení" : ""}</dd></div></dl>
            {latestAI.error_message && <div className="alert danger"><strong>{latestAI.error_code}</strong>: {latestAI.error_message}</div>}
            {latestAI.parsed_result && <details className="candidate"><summary>Strukturovaný výsledek běhu #{latestAI.extraction_revision}</summary><dl>{Object.entries(latestAI.parsed_result).filter(([key])=>key!=="schema_version").map(([key,value])=><div key={key}><dt>{key}</dt><dd>{shown(value && typeof value === "object" && "value" in value ? (value as {value: unknown}).value : value)}</dd></div>)}</dl></details>}
            {latestAI.requires_confirmation && isManager && <button className="button warning" onClick={()=>{if(window.confirm("Nová extrakce přepíše aktuální pracovní údaje a vytvoří auditní záznam. Pokračovat?")) void call(`/invoices/${invoice.id}/ai-extractions/${latestAI.id}/apply`,{method:"POST",body:JSON.stringify({confirm_overwrite:true})})}}>Potvrdit převzetí kandidáta</button>}
          </> : <p>Extrakce zatím nebyla spuštěna.</p>}
          {isManager && <button className="button secondary" disabled={["AI_PENDING","AI_PROCESSING"].includes(invoice.ai_status)} onClick={()=>void call(`/invoices/${invoice.id}/ai-extractions`,{method:"POST"})}>Spustit bezpečnou re-extrakci</button>}
          {invoice.ai.history.length > 1 && <details><summary>Historie AI běhů ({invoice.ai.history.length})</summary><ol>{invoice.ai.history.map((run)=><li key={run.id}>#{run.extraction_revision} · {run.model} · {run.status} · {run.duration_ms != null ? `${(run.duration_ms/1000).toFixed(2)} s` : "bez času"}{run.applied ? " · použit" : ""}</li>)}</ol></details>}
        </div>
        <div className="card"><div className="card-title"><div><h2>OCR text</h2><p>{invoice.paperless.ocr_text.length.toLocaleString("cs-CZ")} znaků · zdroj pro LLM je nedůvěryhodný vstup</p></div></div><pre className="ocr-text">{invoice.paperless.ocr_text || "Paperless zatím nevrátil OCR text."}</pre></div>
        <div className="card"><div className="card-title"><h2>Údaje faktury</h2><button className="button secondary" onClick={saveData}>Uložit změny</button></div>
          <div className="form-grid">{editableFields.map(([key, label]) => <label key={key}>{label}<input value={form[key]} onChange={(e)=>setForm({...form,[key]:e.target.value})} />{evidence[key] && <small title={evidence[key]}>AI zdroj: {evidence[key]}</small>}</label>)}</div>
        </div>
        <div className="card"><div className="card-title"><h2>Deterministická validace</h2><span>{invoice.validations.length}</span></div><div className="validation-list">{invoice.validations.map((v)=><div key={`${v.code}-${v.field_name}`} className={`validation ${v.severity.toLowerCase()}`}><StatusBadge value={v.severity}/><span>{v.message}{(v.expected != null || v.actual != null) && <small> očekáváno: {shown(v.expected)} · skutečnost: {shown(v.actual)}</small>}</span></div>)}</div></div>
        <div className="card"><div className="card-title"><h2>Rozúčtování</h2><button className="button secondary" onClick={()=>setAllocationRows([...allocationRows,{cost_center_id:centres[0]?.id || "",amount:"0.00"}])}>Přidat řádek</button></div>
          {allocationRows.map((row,index)=><div className="allocation-row" key={index}><select value={row.cost_center_id} onChange={(e)=>setAllocationRows(allocationRows.map((r,i)=>i===index?{...r,cost_center_id:e.target.value}:r))}><option value="">Vyberte středisko</option>{centres.map((c)=><option value={c.id} key={c.id}>{c.code} — {c.name}</option>)}</select><input inputMode="decimal" value={row.amount} onChange={(e)=>setAllocationRows(allocationRows.map((r,i)=>i===index?{...r,amount:e.target.value}:r))}/><button className="icon-button" aria-label="Odebrat" onClick={()=>setAllocationRows(allocationRows.filter((_,i)=>i!==index))}>×</button></div>)}
          <button className="button secondary" onClick={saveAllocations}>Uložit rozúčtování</button>
          {invoice.allocations.map((allocation)=><div className="assignment-summary" key={allocation.id}><strong>{allocation.cost_center.code}: {money(allocation.amount,String(invoice.data.currency||"CZK"))}</strong><div className="approver-picker">{approvers.map((approver)=><label className="check-row" key={approver.subject}><input type="checkbox" checked={(approverChoices[allocation.id]||[]).includes(approver.subject)} onChange={(e)=>setApproverChoices({...approverChoices,[allocation.id]:e.target.checked?[...(approverChoices[allocation.id]||[]),approver.subject]:(approverChoices[allocation.id]||[]).filter(x=>x!==approver.subject)})}/><span>{approver.username}</span></label>)}<button className="button secondary" onClick={()=>saveApprovers(allocation.id)}>Uložit schvalovatele</button></div><span>{allocation.assignments.length ? allocation.assignments.map((a)=>`${approvers.find(x=>x.subject===a.approver_subject)?.username||a.approver_subject}${a.decision ? ` (${a.decision})`:""}`).join(", ") : "Schvalovatel není přiřazen"}</span></div>)}
        </div>
        <div className="card actions"><h2>Předání ke schválení</h2><p>{invoice.original_review_confirmed ? `Originál zkontroloval ${invoice.original_reviewed_by} (${invoice.original_reviewed_at ? new Date(invoice.original_reviewed_at).toLocaleString("cs-CZ") : "čas neuveden"}).` : "Originál zatím nebyl explicitně potvrzen jako zkontrolovaný."}</p><div><button className="button secondary" onClick={()=>void call(`/invoices/${invoice.id}/confirm-original`,{method:"POST"})}>Potvrdit kontrolu originálu</button><button className="button primary" onClick={()=>void call(`/invoices/${invoice.id}/submit`,{method:"POST"})}>Předat ke schválení</button></div></div>
      </div>
    </div>
  </section>;
}
