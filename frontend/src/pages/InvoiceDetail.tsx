import { useEffect, useMemo, useState } from "react";
import { api, money } from "../lib/api";
import type { AuditEvent, CostCenter, Invoice, User, UserReference } from "../types";
import { StatusBadge } from "../components/StatusBadge";

const editableFields = [
  ["supplier_name", "Dodavatel"], ["supplier_ico", "IČO"], ["supplier_dic", "DIČ"], ["supplier_address", "Adresa"], ["invoice_number", "Číslo faktury"],
  ["supplier_street", "Ulice pro POHODU"], ["supplier_city", "Město pro POHODU"], ["supplier_zip", "PSČ pro POHODU"],
  ["variable_symbol", "Variabilní symbol"], ["issue_date", "Datum vystavení"], ["taxable_supply_date", "DUZP"],
  ["due_date", "Splatnost"], ["currency", "Měna"], ["bank_account", "Účet [prefix-]číslo"], ["bank_code", "Kód banky"], ["iban", "IBAN"], ["swift_bic", "SWIFT/BIC"],
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
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [approverChoices, setApproverChoices] = useState<Record<string, string[]>>(() => Object.fromEntries(invoice.allocations.map((a) => [a.id, a.assignments.map((x) => x.approver_subject)])));
  const [allocationMode, setAllocationMode] = useState<"amount"|"percentage">(() => invoice.allocations.some(a=>a.percentage != null)?"percentage":"amount");
  const [allocationRows, setAllocationRows] = useState(() => invoice.allocations.map((a) => ({ cost_center_id: a.cost_center.id, amount: String(a.amount), percentage: String(a.percentage ?? ""), note: a.note ?? "", vat_breakdown: JSON.stringify(a.vat_breakdown ?? []) })));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [responseSummary, setResponseSummary] = useState("");
  useEffect(() => { void Promise.all([api<CostCenter[]>("/cost-centers"), api<UserReference[]>("/users?role=APPROVER"), api<AuditEvent[]>(`/invoices/${invoice.id}/audit`)]).then(([c,a,h])=>{setCentres(c);setApprovers(a);setAudit(h)}); }, [invoice.id]);
  useEffect(() => { setForm(formFromInvoice(invoice)); setAllocationRows(invoice.allocations.map((a)=>({cost_center_id:a.cost_center.id,amount:String(a.amount),percentage:String(a.percentage??""),note:a.note??"",vat_breakdown:JSON.stringify(a.vat_breakdown??[])}))); setApproverChoices(Object.fromEntries(invoice.allocations.map((a)=>[a.id,a.assignments.map((x)=>x.approver_subject)]))); }, [invoice]);
  useEffect(() => {
    if (!["AI_PENDING", "AI_PROCESSING"].includes(invoice.ai_status)) return;
    const timer = window.setInterval(onRefresh, 2000);
    return () => window.clearInterval(timer);
  }, [invoice.ai_status, onRefresh]);
  const evidence = useMemo(() => Object.fromEntries(invoice.extracted_fields.map((f) => [f.field_name, f.source_text])), [invoice]);
  const latestAI = invoice.ai.latest;
  const isManager = user.roles.includes("QUEUE_MANAGER");
  const sourceMissing = invoice.source.status === "MISSING";
  const actionable = invoice.disposition.status === "ACTIVE" && !sourceMissing;
  const duplicateCandidates = invoice.validations.find((row) => row.code === "DUPLICATE_INVOICE")?.details?.candidates;
  const call = async (path: string, init: RequestInit) => {
    try { await api(path, init); setMessage("Uloženo."); setError(""); onRefresh(); } catch (e) { setError((e as Error).message); }
  };
  const saveData = () => {
    const changes = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v === "" ? null : v]));
    void call(`/invoices/${invoice.id}`, { method: "PATCH", body: JSON.stringify({ changes }) });
  };
  const saveAllocations = () => {
    try {
      const allocations = allocationRows.map((row) => ({ cost_center_id: row.cost_center_id, note: row.note || null, vat_breakdown: row.vat_breakdown.trim() ? JSON.parse(row.vat_breakdown) : [], ...(allocationMode === "amount" ? { amount: row.amount } : { percentage: row.percentage }) }));
      void call(`/invoices/${invoice.id}/allocations`, { method: "PUT", body: JSON.stringify({ allocations }) });
    } catch { setError("DPH rozpad musí být platné JSON pole objektů rate/base/vat."); }
  };
  const saveApprovers = (allocationId: string) => void call(`/invoices/${invoice.id}/allocations/${allocationId}/approvers`, { method: "PUT", body: JSON.stringify({ approver_subjects: approverChoices[allocationId] || [] }) });
  const generateXml = async (reason: string | null = null) => {
    try { await api(`/exports/invoices/${invoice.id}/generate`, { method: "POST", body: JSON.stringify({ reason }) }); setMessage(reason ? "Re-export byl vytvořen." : "XML bylo vygenerováno a ověřeno."); setError(""); onRefresh(); } catch (e) { setError((e as Error).message); }
  };
  const createZip = async () => {
    try { const batch = await api<{id:string}>("/exports", { method: "POST", body: JSON.stringify({ invoice_ids: [invoice.id] }) }); window.location.href = `/api/exports/${batch.id}/download`; onRefresh(); } catch (e) { setError((e as Error).message); }
  };
  const markImported = async () => {
    if (!invoice.pohoda_export || !window.confirm("Potvrzujete, že tato konkrétní revize byla ručně importována do POHODY?")) return;
    try { await api(`/exports/artifacts/${invoice.pohoda_export.id}/mark-imported`, { method: "POST", body: JSON.stringify({ confirmed: true }) }); onRefresh(); } catch (e) { setError((e as Error).message); }
  };
  const uploadResponse = async (file?: File) => {
    if (!file || !invoice.pohoda_export) return;
    const body = new FormData(); body.append("response_file", file); body.append("export_artifact_id", invoice.pohoda_export.id);
    try { const result = await api<{parse_status:string;parsed_result:{state?:string;items?:unknown[]}}>("/exports/responses", { method: "POST", body }); setResponseSummary(`${result.parse_status}: ${result.parsed_result.state ?? "bez stavu"}, položek ${result.parsed_result.items?.length ?? 0}`); } catch (e) { setError((e as Error).message); }
  };
  const ignoreOther = () => {
    const reason = window.prompt("Důvod: Chybně nahraný dokument / Není faktura / Jiný důvod");
    if (!reason) return;
    const comment = window.prompt("Volitelný komentář") || null;
    void call(`/invoices/${invoice.id}/disposition`, { method: "POST", body: JSON.stringify({ disposition: "IGNORED_OTHER", reason, comment }) });
  };
  const markDuplicate = () => {
    const suggested = invoice.validations.find((row) => row.code === "DUPLICATE_INVOICE")?.details?.candidate_invoice_ids;
    const target = window.prompt("ID původní faktury", Array.isArray(suggested) ? String(suggested[0] || "") : "");
    if (!target) return;
    const comment = window.prompt("Volitelný komentář k porovnání") || null;
    void call(`/invoices/${invoice.id}/disposition`, { method: "POST", body: JSON.stringify({ disposition: "IGNORED_DUPLICATE", reason: "confirmed duplicate", comment, duplicate_of_invoice_id: target }) });
  };
  return <section>
    <button className="back" onClick={onBack}>← Zpět na frontu</button>
    <div className="section-heading"><div><p className="eyebrow">Paperless #{invoice.paperless_document_id} · revize {invoice.current_revision_number}</p><h1>{String(invoice.data.invoice_number || invoice.paperless.title || "Faktura bez názvu")}</h1><p className="muted">{String(invoice.data.supplier_name || invoice.paperless.correspondent || "Neznámý dodavatel")} · {money(String(invoice.data.total_amount || ""), String(invoice.data.currency || "CZK"))}</p></div><div className="heading-badges"><StatusBadge value={invoice.source.status} /><StatusBadge value={invoice.disposition.status} /><StatusBadge value={invoice.paperless.sync_status} /><StatusBadge value={invoice.ai_status} /><StatusBadge value={invoice.status} /></div></div>
    {sourceMissing && <div className="alert danger"><strong>Zdrojový dokument v Paperless chybí.</strong> Nové schválení, export, PDF a ZIP jsou zablokované; historie a již vytvořené artefakty zůstávají zachované.</div>}
    {message && <div className="alert success">{message}</div>}{error && <div className="alert danger">{error}</div>}
    <div className="detail-grid">
      <div className="pdf-panel">{sourceMissing ? <div className="source-missing">Originální PDF již není v Paperless dostupné.</div> : <><iframe title="Originální faktura" src={`/api/invoices/${invoice.id}/pdf`} /><a className="button secondary" href={`/api/invoices/${invoice.id}/pdf`} target="_blank" rel="noreferrer">Otevřít PDF v novém okně</a></>}</div>
      <div className="work-panel">
        <div className="card"><div className="card-title"><div><h2>Zdrojová metadata</h2><p>Načteno výhradně přes Paperless REST API</p></div></div>
          <dl className="metadata-grid"><div><dt>Název</dt><dd>{invoice.paperless.title || "—"}</dd></div><div><dt>Vytvořeno</dt><dd>{invoice.paperless.created_at ? new Date(invoice.paperless.created_at).toLocaleString("cs-CZ") : "—"}</dd></div><div><dt>Korespondent</dt><dd>{invoice.paperless.correspondent || "—"}</dd></div><div><dt>Původní soubor</dt><dd>{invoice.paperless.original_filename || "—"}</dd></div><div><dt>Tagy</dt><dd>{invoice.paperless.tags.join(", ") || "—"}</dd></div><div><dt>Poslední synchronizace</dt><dd>{invoice.paperless.last_synced_at ? new Date(invoice.paperless.last_synced_at).toLocaleString("cs-CZ") : "—"}</dd></div></dl>
          {invoice.paperless.sync_error && <div className="alert danger">{invoice.paperless.sync_error}</div>}
        </div>
        <div className="card"><div className="card-title"><div><h2>Evidence a dispozice</h2><p>Nezávislé na workflow; historický stav se při ignorování nemění.</p></div><StatusBadge value={invoice.disposition.status}/></div>
          {Array.isArray(duplicateCandidates) && duplicateCandidates.length > 0 && <div className="alert warning"><strong>Možná duplicita</strong>{duplicateCandidates.map((candidate, index) => { const row = candidate as {invoice_id?:string;matched_fields?:string[]}; return <p key={row.invoice_id || index}><a href={`/invoices/${row.invoice_id}`} target="_blank" rel="noreferrer">Otevřít kandidátní fakturu</a> · shoda: {(row.matched_fields || []).join(", ")}</p>; })}</div>}
          {invoice.disposition.status === "ACTIVE" ? <p>Faktura je aktivní.</p> : <dl className="metadata-grid"><div><dt>Důvod</dt><dd>{invoice.disposition.reason || "—"}</dd></div><div><dt>Rozhodl</dt><dd>{invoice.disposition.actor || "—"}</dd></div><div><dt>Čas</dt><dd>{invoice.disposition.changed_at ? new Date(invoice.disposition.changed_at).toLocaleString("cs-CZ") : "—"}</dd></div><div><dt>Duplicita faktury</dt><dd>{invoice.disposition.duplicate_of_invoice_id || "—"}</dd></div></dl>}
          {isManager && <div className="disposition-actions">{invoice.disposition.status === "ACTIVE" ? <><button className="button warning" onClick={markDuplicate}>Označit jako duplicitu</button><button className="button secondary" onClick={ignoreOther}>Označit jako nepotřebnou</button></> : <button className="button secondary" onClick={() => void call(`/invoices/${invoice.id}/restore`, { method: "POST", body: JSON.stringify({ comment: "Obnoveno ve frontě" }) })}>Obnovit do aktivní fronty</button>}</div>}
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
        <div className="card"><div className="card-title"><h2>Fakturační údaje</h2>{isManager&&<button className="button secondary" onClick={saveData}>Uložit změny</button>}</div>
          <dl className="metadata-grid"><div><dt>Účet – původní hodnota</dt><dd>{shown(invoice.data.bank_account_raw)}</dd></div><div><dt>Předčíslí</dt><dd>{shown(invoice.data.bank_account_prefix)}</dd></div><div><dt>Číslo účtu</dt><dd>{shown(invoice.data.bank_account_number)}</dd></div><div><dt>Kód banky</dt><dd>{shown(invoice.data.bank_code)}</dd></div></dl>
          <div className="form-grid">{editableFields.map(([key, label]) => <label key={key}>{label}<input disabled={!isManager} value={form[key]} onChange={(e)=>setForm({...form,[key]:e.target.value})} />{evidence[key] && <small title={evidence[key]}>AI zdroj: {evidence[key]}</small>}</label>)}</div>
        </div>
        <div className="card"><div className="card-title"><h2>Deterministická validace</h2><span>{invoice.validations.length}</span></div><div className="validation-list">{invoice.validations.map((v)=><div key={`${v.code}-${v.field_name}`} className={`validation ${v.severity.toLowerCase()}`}><StatusBadge value={v.severity}/><span>{v.message}{(v.expected != null || v.actual != null) && <small> očekáváno: {shown(v.expected)} · skutečnost: {shown(v.actual)}{v.details?.difference != null ? ` · rozdíl: ${shown(v.details.difference)}` : ""}</small>}</span></div>)}</div></div>
        <div className="card"><div className="card-title"><div><h2>Rozúčtování</h2><p>Částky jsou ukládány jako Decimal; nesoulad je blokující validace.</p></div>{isManager&&<button className="button secondary" onClick={()=>setAllocationRows([...allocationRows,{cost_center_id:centres[0]?.id || "",amount:"0.00",percentage:"0",note:"",vat_breakdown:"[]"}])}>Přidat řádek</button>}</div>
          <div className="allocation-totals"><span>Celkem faktura<strong>{money(invoice.allocation_summary.invoice_total,String(invoice.data.currency||"CZK"))}</strong></span><span>Rozúčtováno<strong>{money(invoice.allocation_summary.allocated,String(invoice.data.currency||"CZK"))}</strong></span><span>Zbývá rozúčtovat<strong>{money(invoice.allocation_summary.remaining,String(invoice.data.currency||"CZK"))}</strong></span></div>
          {isManager&&<div className="mode-switch"><label><input type="radio" checked={allocationMode==="amount"} onChange={()=>setAllocationMode("amount")}/> Částkou</label><label><input type="radio" checked={allocationMode==="percentage"} onChange={()=>setAllocationMode("percentage")}/> Procentem</label></div>}
          {isManager&&allocationRows.map((row,index)=><div className="allocation-row" key={index}><select value={row.cost_center_id} onChange={(e)=>setAllocationRows(allocationRows.map((r,i)=>i===index?{...r,cost_center_id:e.target.value}:r))}><option value="">Vyberte středisko</option>{centres.map((c)=><option value={c.id} key={c.id}>{c.code} — {c.name}</option>)}</select><input aria-label={allocationMode==="amount"?"Částka":"Procento"} inputMode="decimal" value={allocationMode==="amount"?row.amount:row.percentage} onChange={(e)=>setAllocationRows(allocationRows.map((r,i)=>i===index?{...r,[allocationMode]:e.target.value}:r))}/><input aria-label="Poznámka" placeholder="Poznámka" value={row.note} onChange={(e)=>setAllocationRows(allocationRows.map((r,i)=>i===index?{...r,note:e.target.value}:r))}/><textarea aria-label="DPH rozpad allocation" title="Povinné jen při více sazbách a více střediscích" value={row.vat_breakdown} onChange={(e)=>setAllocationRows(allocationRows.map((r,i)=>i===index?{...r,vat_breakdown:e.target.value}:r))}/><button className="icon-button" aria-label="Odebrat" onClick={()=>setAllocationRows(allocationRows.filter((_,i)=>i!==index))}>×</button></div>)}
          {isManager&&<button className="button secondary" onClick={saveAllocations}>Uložit rozúčtování</button>}
          {invoice.allocations.map((allocation)=><div className="assignment-summary" key={allocation.id}><strong>{allocation.cost_center.code}: {money(allocation.amount,String(invoice.data.currency||"CZK"))}{allocation.percentage!=null?` · ${allocation.percentage} %`:""}</strong>{allocation.note&&<small>{allocation.note}</small>}{isManager&&<div className="approver-picker">{approvers.map((approver)=><label className="check-row" key={approver.subject}><input type="checkbox" checked={(approverChoices[allocation.id]||[]).includes(approver.subject)} onChange={(e)=>setApproverChoices({...approverChoices,[allocation.id]:e.target.checked?[...(approverChoices[allocation.id]||[]),approver.subject]:(approverChoices[allocation.id]||[]).filter(x=>x!==approver.subject)})}/><span>{approver.username}</span></label>)}<button className="button secondary" onClick={()=>saveApprovers(allocation.id)}>Uložit schvalovatele</button></div>}<span>{allocation.assignments.length ? allocation.assignments.map((a)=>`${approvers.find(x=>x.subject===a.approver_subject)?.username||a.approver_subject} (${a.status})`).join(", ") : "Schvalovatel není přiřazen"}</span></div>)}
        </div>
        {isManager&&<div className="card actions"><h2>Kontrola originálu a workflow</h2><p>{invoice.original_review_confirmed ? `Originál zkontroloval ${invoice.original_reviewed_by} (${invoice.original_reviewed_at ? new Date(invoice.original_reviewed_at).toLocaleString("cs-CZ") : "čas neuveden"}).` : "Originál zatím nebyl explicitně potvrzen jako zkontrolovaný."}</p><div><button className="button secondary" disabled={!actionable} onClick={()=>void call(`/invoices/${invoice.id}/confirm-original`,{method:"POST"})}>Potvrdit kontrolu originálu</button><button className="button primary" disabled={!actionable} onClick={()=>void call(`/invoices/${invoice.id}/submit`,{method:"POST"})}>Předat ke schválení</button>{invoice.status==="REJECTED"&&<button className="button warning" disabled={!actionable} onClick={()=>void call(`/invoices/${invoice.id}/reopen`,{method:"POST"})}>Znovu otevřít zamítnutou fakturu</button>}</div></div>}
        {isManager&&<div className="card pohoda-export"><div className="card-title"><div><h2>POHODA export</h2><p>Deterministické XML pro ruční import; stažení samo nepotvrzuje import.</p></div>{invoice.pohoda_export&&<StatusBadge value={invoice.pohoda_export.status}/>}</div>
          {invoice.pohoda_export?<dl className="metadata-grid"><div><dt>XSD verze</dt><dd>{invoice.pohoda_export.xsd_bundle_version}</dd></div><div><dt>Generátor</dt><dd>{invoice.pohoda_export.generator_version}</dd></div><div><dt>Encoding</dt><dd>{invoice.pohoda_export.encoding}</dd></div><div><dt>Velikost XML</dt><dd>{invoice.pohoda_export.xml_size.toLocaleString("cs-CZ")} B</dd></div><div><dt>SHA-256</dt><dd className="hash">{invoice.pohoda_export.xml_sha256}</dd></div><div><dt>Vygenerováno</dt><dd>{new Date(invoice.pohoda_export.generated_at).toLocaleString("cs-CZ")}</dd></div></dl>:<p>XML zatím nebylo vytvořeno.</p>}
          {invoice.pohoda_export?.validation_errors.map((row,index)=><div className="alert danger" key={index}>{row.message} {row.path&&<small>{row.path}</small>}</div>)}
          <div className="decision-buttons">{invoice.status==="APPROVED"&&invoice.pohoda_export?.status!=="XSD_VALID"&&<button className="button primary" disabled={!actionable} onClick={()=>void generateXml()}>Vygenerovat XML</button>}{invoice.pohoda_export?.status==="XSD_VALID"&&<><a className="button secondary" href={`/api/exports/artifacts/${invoice.pohoda_export.id}/xml`}>Stáhnout XML</a>{actionable&&<><a className="button secondary" href={`/api/invoices/${invoice.id}/pdf`}>Stáhnout PDF</a><button className="button secondary" onClick={()=>void createZip()}>Stáhnout ZIP</button><button className="button warning" onClick={()=>{const reason=window.prompt("Důvod re-exportu (volitelný)");if(reason!==null)void generateXml(reason)}}>Re-export</button></>}{!actionable&&<span className="muted">Nový export, PDF a ZIP jsou zablokované.</span>}</>}{invoice.status==="EXPORT_CREATED"&&invoice.pohoda_export&&<button className="button primary" disabled={!actionable} onClick={()=>void markImported()}>OZNAČIT JAKO IMPORTOVÁNO DO POHODY</button>}</div>
          {invoice.pohoda_export&&<label className="response-upload">Nahrát POHODA response XML<input type="file" accept="application/xml,text/xml,.xml" onChange={(event)=>void uploadResponse(event.target.files?.[0])}/></label>}{responseSummary&&<div className="alert success">{responseSummary}</div>}
        </div>}
        <div className="card"><div className="card-title"><div><h2>Audit a historie</h2><p>Append-only události faktury a jejích revizí.</p></div><span>{audit.length}</span></div><ol className="audit-list">{audit.slice().reverse().map(event=><li key={event.id}><strong>{event.event_type}</strong><span>revize {event.revision??"—"} · {event.actor} · {new Date(event.timestamp).toLocaleString("cs-CZ")}</span>{event.comment&&<p>{event.comment}</p>}</li>)}</ol></div>
      </div>
    </div>
  </section>;
}
