import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, money, pragueDateTime } from "../lib/api";
import type { AuditEvent, CostCenter, Invoice, PohodaConfig, User, UserReference } from "../types";
import { CzechDateInput } from "../components/CzechDateInput";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateCs, parseDateCs } from "../lib/dates";

const editableFields = [
  ["supplier_name", "Dodavatel"], ["supplier_ico", "IČO"], ["supplier_dic", "DIČ"], ["supplier_address_raw", "Adresa – původní text"], ["invoice_number", "Číslo faktury"],
  ["supplier_street", "Ulice pro POHODU"], ["supplier_city", "Město pro POHODU"], ["supplier_zip", "PSČ pro POHODU"],
  ["variable_symbol", "Variabilní symbol"], ["issue_date", "Datum vystavení"], ["taxable_supply_date", "DUZP"],
  ["due_date", "Splatnost"], ["currency", "Měna"], ["bank_account_raw", "Účet – původní hodnota"], ["bank_account_prefix", "Předčíslí účtu"], ["bank_account_number", "Číslo účtu"], ["bank_code", "Kód banky"], ["iban", "IBAN"], ["swift_bic", "SWIFT/BIC"],
  ["total_without_vat", "Základ bez DPH"], ["total_vat", "DPH celkem"], ["total_amount", "Celkem"], ["description", "Popis"],
] as const;

const dateFields = new Set(["issue_date", "taxable_supply_date", "due_date"]);
const formFromInvoice = (invoice: Invoice) => Object.fromEntries(editableFields.map(([key]) => {
  const value = invoice.data[key] ?? (key === "supplier_address_raw" ? invoice.data.supplier_address : undefined);
  return [key, dateFields.has(key) && value ? formatDateCs(String(value)) : String(value ?? "")];
}));

const draftSnapshot = (invoice: Invoice) => JSON.stringify({
  revision: invoice.current_revision_number,
  data: Object.fromEntries(editableFields.map(([key]) => [key, invoice.data[key] ?? null])),
  allocations: invoice.allocations.map((row) => ({
    id: row.id, amount: row.amount, percentage: row.percentage ?? null, note: row.note ?? null,
    vat_breakdown: row.vat_breakdown, approvers: row.assignments.map((item) => item.approver_subject),
  })),
});

function shown(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const auditLabels: Record<string, string> = {
  DOCUMENT_DISCOVERED: "Dokument nalezen v Paperless",
  AI_EXTRACTION_APPLIED: "Použita první AI extrakce",
  AI_REEXTRACTION_APPLIED: "Použita nová AI extrakce",
  INVOICE_FIELD_CHANGED: "Změněn údaj faktury",
  FIELD_CHANGED: "Změněn údaj faktury (historický záznam)",
  REVISION_CREATED: "Vytvořena nová revize",
  WORKFLOW_TRANSITION: "Změněn stav workflow",
  ORIGINAL_REVIEW_CONFIRMED: "Potvrzena kontrola originálu",
  ALLOCATION_CREATED: "Přidáno rozúčtování",
  ALLOCATIONS_REPLACED: "Upraveno rozúčtování",
  APPROVERS_REPLACED: "Upraveni schvalovatelé",
  APPROVED: "Faktura schválena",
  RETURNED: "Faktura vrácena",
  REJECTED: "Faktura zamítnuta",
  XML_GENERATION_REQUESTED: "Vyžádáno vytvoření POHODA XML",
  XML_GENERATED: "Vytvořeno POHODA XML",
  XML_VALIDATION_PASSED: "POHODA XML prošlo XSD validací",
  EXPORT_DOWNLOADED: "Staženo POHODA XML",
  PDF_DOWNLOADED: "Stažen originální PDF",
  ZIP_DOWNLOADED: "Stažen exportní ZIP",
};
const schemaFieldLabels: Record<string, string> = {
  vat_rate: "DPH sazba",
  taxable_base: "Základ DPH",
  vat_amount: "Částka DPH",
  gross_amount: "Částka s DPH",
  issue_date: "Datum vystavení",
  taxable_supply_date: "DUZP",
  due_date: "Splatnost",
  total_without_vat: "Základ bez DPH",
  total_vat: "DPH celkem",
  total_amount: "Celkem",
};
const schemaFieldLabel = (path: string) => schemaFieldLabels[path.split(".").at(-1) || path] || path;
const diagnosticValue = (value: unknown) => value === undefined ? "—" : JSON.stringify(value);

type StepState = "DONE" | "CURRENT" | "WAITING" | "BLOCKED" | "ERROR";

function workflowSteps(invoice: Invoice): { label: string; state: StepState; detail: string }[] {
  const blocked = invoice.validations.some((row) => row.severity === "BLOCKING_ERROR");
  const allocated = invoice.allocations.length > 0 && Number(invoice.allocation_summary.remaining) === 0;
  const assigned = invoice.allocations.length > 0 && invoice.allocations.every((row) => row.assignments.length > 0);
  const approved = ["APPROVED", "XML_READY", "READY_FOR_EXPORT", "EXPORT_CREATED", "IMPORTED_TO_POHODA"].includes(invoice.status);
  const exported = ["EXPORT_CREATED", "IMPORTED_TO_POHODA"].includes(invoice.status);
  return [
    { label: "Zdroj", state: invoice.source.status === "AVAILABLE" ? "DONE" : "ERROR", detail: invoice.source.status === "AVAILABLE" ? "Dokument je dostupný v Paperless." : "Zdroj není dostupný; navazující akce jsou blokované." },
    { label: "Data a validace", state: blocked ? "BLOCKED" : invoice.data.invoice_number ? "DONE" : "CURRENT", detail: blocked ? "Odstraňte blokující validační chyby." : invoice.data.invoice_number ? "Fakturační údaje jsou připravené." : "Doplňte fakturační údaje." },
    { label: "Rozúčtování", state: allocated ? "DONE" : blocked ? "WAITING" : "CURRENT", detail: allocated ? "Součet rozúčtování odpovídá faktuře." : "Doplňte rozúčtování do celé částky." },
    { label: "Kontrola originálu", state: invoice.original_review_confirmed ? "DONE" : !allocated ? "WAITING" : "CURRENT", detail: invoice.original_review_confirmed ? "Originál byl zkontrolován." : "Správce musí potvrdit kontrolu PDF." },
    { label: "Schválení", state: invoice.status === "REJECTED" ? "ERROR" : invoice.status === "RETURNED" ? "BLOCKED" : approved ? "DONE" : invoice.status === "AWAITING_APPROVAL" ? "CURRENT" : assigned ? "WAITING" : "BLOCKED", detail: invoice.status === "REJECTED" ? "Faktura byla zamítnuta." : invoice.status === "RETURNED" ? "Faktura byla vrácena k opravě." : approved ? "Všechna povinná schválení jsou hotová." : !assigned ? "Přiřaďte schvalovatele." : invoice.status === "AWAITING_APPROVAL" ? "Čeká se na schvalovatele." : "Předejte fakturu ke schválení." },
    { label: "POHODA export", state: invoice.pohoda_export?.status === "XSD_INVALID" ? "ERROR" : exported ? "DONE" : approved ? "CURRENT" : "WAITING", detail: invoice.pohoda_export?.status === "XSD_INVALID" ? "XML neprošlo XSD validací." : exported ? "Neměnný exportní artefakt byl vytvořen." : approved ? "Lze vytvořit a stáhnout XML/ZIP." : "Export čeká na finální schválení." },
    { label: "Ruční import", state: invoice.status === "IMPORTED_TO_POHODA" ? "DONE" : invoice.status === "EXPORT_CREATED" ? "CURRENT" : "WAITING", detail: invoice.status === "IMPORTED_TO_POHODA" ? "Import byl explicitně potvrzen." : invoice.status === "EXPORT_CREATED" ? "Po ručním importu potvrďte konkrétní artefakt." : "Čeká na exportní balíček." },
  ];
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
  const [pending, setPending] = useState("");
  const [dirty, setDirty] = useState(false);
  const [serverUpdateAvailable, setServerUpdateAvailable] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [sectionError, setSectionError] = useState("");
  const [pohodaConfig, setPohodaConfig] = useState<PohodaConfig | null>(null);
  const hydratedRevision = useRef(invoice.current_revision_number);
  const hydratedSnapshot = useRef(draftSnapshot(invoice));
  const hydrate = (next: Invoice) => {
    setForm(formFromInvoice(next));
    setAllocationRows(next.allocations.map((a)=>({cost_center_id:a.cost_center.id,amount:String(a.amount),percentage:String(a.percentage??""),note:a.note??"",vat_breakdown:JSON.stringify(a.vat_breakdown??[])})));
    setApproverChoices(Object.fromEntries(next.allocations.map((a)=>[a.id,a.assignments.map((x)=>x.approver_subject)])));
    hydratedRevision.current = next.current_revision_number;
    hydratedSnapshot.current = draftSnapshot(next);
    setDirty(false);
    setServerUpdateAvailable(false);
  };
  useEffect(() => { void Promise.all([api<CostCenter[]>("/cost-centers"), api<UserReference[]>("/users?role=APPROVER"), api<AuditEvent[]>(`/invoices/${invoice.id}/audit`), api<PohodaConfig>("/exports/config")]).then(([c,a,h,p])=>{setCentres(c);setApprovers(a);setAudit(h);setPohodaConfig(p)}); }, [invoice.id, invoice.current_revision_number]);
  useEffect(() => {
    const nextSnapshot = draftSnapshot(invoice);
    if (nextSnapshot === hydratedSnapshot.current) return;
    if (dirty) setServerUpdateAvailable(true);
    else hydrate(invoice);
  }, [invoice, dirty]);
  const evidence = useMemo(() => Object.fromEntries(invoice.extracted_fields.map((f) => [f.field_name, f.source_text])), [invoice]);
  const latestAI = invoice.ai.latest;
  const candidateDifferences = latestAI?.candidate_data ? editableFields.flatMap(([key, label]) => {
    const candidate = latestAI.candidate_data?.[key];
    if (candidate == null || String(candidate) === String(invoice.data[key] ?? "")) return [];
    return [{ key, label, current: invoice.data[key], candidate }];
  }) : [];
  const isManager = user.roles.includes("QUEUE_MANAGER");
  const targetUnitValid = invoice.pohoda_export?.pohoda_target_validation?.status === "TARGET_UNIT_VALID";
  const sourceMissing = invoice.source.status === "MISSING";
  const actionable = invoice.disposition.status === "ACTIVE" && !sourceMissing;
  const duplicateCandidates = invoice.validations.find((row) => row.code === "DUPLICATE_INVOICE")?.details?.candidates;
  const steps = workflowSteps(invoice);
  const changeAllocationRows = (rows: typeof allocationRows) => { setAllocationRows(rows); setDirty(true); };
  const call = async (key: string, path: string, init: RequestInit, success = "Uloženo.") => {
    if (pending) return;
    setPending(key); setMessage(""); setError(""); setSectionError(""); setFieldErrors({});
    try {
      const result = await api<unknown>(path, init);
      if (typeof result === "object" && result && "validations" in result) {
        const validations = (result as Invoice).validations;
        const first = validations.find((row) => row.severity === "BLOCKING_ERROR" && row.field_name);
        if (first?.field_name) {
          setFieldErrors({ [first.field_name]: first.message });
          window.setTimeout(() => document.querySelector<HTMLElement>(`[data-field="${first.field_name}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
          window.setTimeout(() => document.querySelector<HTMLElement>(`[data-field="${first.field_name}"]`)?.focus(), 100);
        }
      }
      setMessage(success);
      setDirty(false);
      onRefresh();
    } catch (e) {
      const apiError = e as ApiError;
      const detail = apiError.detail;
      if (apiError.status === 409 && typeof detail === "object" && detail && "code" in detail && (detail as {code:string}).code === "STALE_REVISION") {
        setServerUpdateAvailable(true);
        setSectionError(apiError.message);
      } else if (Array.isArray(detail) && detail.length) {
        const first = detail[0] as { loc?: (string | number)[]; msg?: string };
        const field = String(first.loc?.at(-1) || "form");
        setFieldErrors({ [field]: first.msg || apiError.message });
        window.setTimeout(() => document.querySelector<HTMLElement>(`[data-field="${field}"]`)?.focus(), 0);
      } else {
        setSectionError(apiError.message || (e as Error).message);
      }
    } finally { setPending(""); }
  };
  const saveData = () => {
    const dateErrors: Record<string, string> = {};
    const changes = Object.fromEntries(Object.entries(form).map(([key, value]) => {
      if (!dateFields.has(key)) return [key, value === "" ? null : value];
      const parsed = parseDateCs(value);
      if (parsed.error) dateErrors[key] = parsed.error;
      return [key, parsed.iso];
    }));
    if (Object.keys(dateErrors).length) {
      setFieldErrors(dateErrors);
      const first = Object.keys(dateErrors)[0];
      window.setTimeout(() => document.querySelector<HTMLElement>(`[data-field="${first}"]`)?.focus(), 0);
      return;
    }
    void call("data", `/invoices/${invoice.id}`, { method: "PATCH", body: JSON.stringify({ changes, expected_revision: hydratedRevision.current }) }, "Fakturační údaje byly uloženy.");
  };
  const saveAllocations = () => {
    try {
      const allocations = allocationRows.map((row) => ({ cost_center_id: row.cost_center_id, note: row.note || null, vat_breakdown: row.vat_breakdown.trim() ? JSON.parse(row.vat_breakdown) : [], ...(allocationMode === "amount" ? { amount: row.amount } : { percentage: row.percentage }) }));
      void call("allocations", `/invoices/${invoice.id}/allocations`, { method: "PUT", body: JSON.stringify({ allocations, expected_revision: hydratedRevision.current }) }, "Rozúčtování bylo uloženo.");
    } catch { setSectionError("DPH rozpad musí být platné JSON pole objektů rate/base/vat."); }
  };
  const saveApprovers = (allocationId: string) => void call(`approvers-${allocationId}`, `/invoices/${invoice.id}/allocations/${allocationId}/approvers`, { method: "PUT", body: JSON.stringify({ approver_subjects: approverChoices[allocationId] || [], expected_revision: hydratedRevision.current }) }, "Schvalovatelé byli uloženi.");
  const generateXml = (reason: string | null = null) => void call("xml", `/exports/invoices/${invoice.id}/generate`, { method: "POST", body: JSON.stringify({ reason }) }, reason ? "Re-export byl vytvořen." : "XML bylo vygenerováno a ověřeno.");
  const createZip = async () => {
    if (pending) return; setPending("zip");
    try { const batch = await api<{id:string}>("/exports", { method: "POST", body: JSON.stringify({ invoice_ids: [invoice.id] }) }); setMessage("ZIP byl vytvořen, stahování začíná."); window.location.href = `/api/exports/${batch.id}/download`; onRefresh(); } catch (e) { setSectionError((e as Error).message); } finally { setPending(""); }
  };
  const markImported = async () => {
    if (!invoice.pohoda_export || !window.confirm("Potvrzujete, že tato konkrétní revize byla ručně importována do POHODY?")) return;
    void call("import", `/exports/artifacts/${invoice.pohoda_export.id}/mark-imported`, { method: "POST", body: JSON.stringify({ confirmed: true }) }, "Ruční import konkrétního artefaktu byl potvrzen.");
  };
  const uploadResponse = async (file?: File) => {
    if (!file || !invoice.pohoda_export) return;
    const body = new FormData(); body.append("response_file", file); body.append("export_artifact_id", invoice.pohoda_export.id);
    if (pending) return; setPending("response");
    try { const result = await api<{parse_status:string;parsed_result:{state?:string;items?:unknown[]}}>("/exports/responses", { method: "POST", body }); setResponseSummary(`${result.parse_status}: ${result.parsed_result.state ?? "bez stavu"}, položek ${result.parsed_result.items?.length ?? 0}`); setMessage("Odpověď POHODA byla bezpečně zpracována."); } catch (e) { setSectionError((e as Error).message); } finally { setPending(""); }
  };
  const ignoreOther = () => {
    const reason = window.prompt("Důvod: Chybně nahraný dokument / Není faktura / Jiný důvod");
    if (!reason) return;
    const comment = window.prompt("Volitelný komentář") || null;
    void call("disposition", `/invoices/${invoice.id}/disposition`, { method: "POST", body: JSON.stringify({ disposition: "IGNORED_OTHER", reason, comment }) }, "Dokument byl vyřazen z aktivní fronty.");
  };
  const markDuplicate = () => {
    const suggested = invoice.validations.find((row) => row.code === "DUPLICATE_INVOICE")?.details?.candidate_invoice_ids;
    const target = window.prompt("ID původní faktury", Array.isArray(suggested) ? String(suggested[0] || "") : "");
    if (!target) return;
    const comment = window.prompt("Volitelný komentář k porovnání") || null;
    void call("disposition", `/invoices/${invoice.id}/disposition`, { method: "POST", body: JSON.stringify({ disposition: "IGNORED_DUPLICATE", reason: "confirmed duplicate", comment, duplicate_of_invoice_id: target }) }, "Dokument byl označen jako duplicita.");
  };
  const validationByField = Object.fromEntries(invoice.validations.filter((row)=>row.field_name&&row.severity!=="OK").map((row)=>[row.field_name!,row]));
  const allocationError = invoice.validations.find((row)=>row.code==="ALLOCATION_TOTAL_MISMATCH");
  const missingApprover = invoice.allocations.find((row)=>row.assignments.length===0);
  const vatLines = Array.isArray(invoice.data.vat_lines) ? invoice.data.vat_lines as Record<string, unknown>[] : [];
  const vatValidations = invoice.validations.filter((row)=>row.code.startsWith("VAT_")||row.code==="TOTAL_MATH_OK");
  const roundingLine = vatLines.find((row)=>row.adjustment_type==="ROUNDING");
  return <section>
    <button className="back" onClick={onBack}>← Zpět na frontu</button>
    <div className="section-heading"><div><p className="eyebrow">Paperless #{invoice.paperless_document_id} · revize {invoice.current_revision_number}</p><h1>{String(invoice.data.invoice_number || invoice.paperless.title || "Faktura bez názvu")}</h1><p className="muted">{String(invoice.data.supplier_name || invoice.paperless.correspondent || "Neznámý dodavatel")} · {money(String(invoice.data.total_amount || ""), String(invoice.data.currency || "CZK"))}</p></div><div className="heading-badges"><StatusBadge value={invoice.source.status} /><StatusBadge value={invoice.disposition.status} /><StatusBadge value={invoice.paperless.sync_status} /><StatusBadge value={invoice.ai_status} /><StatusBadge value={invoice.status} /></div></div>
    {sourceMissing && <div className="alert danger"><strong>Zdrojový dokument v Paperless chybí.</strong> Nové schválení, export, PDF a ZIP jsou zablokované; historie a již vytvořené artefakty zůstávají zachované.</div>}
    {serverUpdateAvailable && <div className="alert warning"><strong>Na serveru je novější revize.</strong> Vaše rozepsaná data zůstala zachována. <button className="button secondary" onClick={()=>hydrate(invoice)}>Načíst novou verzi a zahodit lokální změny</button></div>}
    {message && <div className="alert success">{message}</div>}{(error || sectionError) && <div className="alert danger" role="alert">{sectionError || error}</div>}
    <ol className="workflow-stepper" aria-label="Průběh zpracování faktury">{steps.map((step)=><li key={step.label} className={`step-${step.state.toLowerCase()}`}><StatusBadge value={step.state}/><strong>{step.label}</strong><span>{step.detail}</span></li>)}</ol>
    <div className="detail-grid">
      <div className="pdf-panel">{sourceMissing ? <div className="source-missing">Originální PDF již není v Paperless dostupné.</div> : <><iframe title="Originální faktura" src={`/api/invoices/${invoice.id}/pdf`} /><a className="button secondary" href={`/api/invoices/${invoice.id}/pdf`} target="_blank" rel="noreferrer">Otevřít PDF v novém okně</a></>}</div>
      <div className="work-panel">
        <div className="card"><div className="card-title"><div><h2>Zdrojová metadata</h2><p>Načteno výhradně přes Paperless REST API</p></div></div>
          <dl className="metadata-grid"><div><dt>Název</dt><dd>{invoice.paperless.title || "—"}</dd></div><div><dt>Vloženo do zdroje</dt><dd>{pragueDateTime(invoice.paperless.created_at)}</dd></div><div><dt>Korespondent</dt><dd>{invoice.paperless.correspondent || "—"}</dd></div><div><dt>Původní soubor</dt><dd>{invoice.paperless.original_filename || "—"}</dd></div><div><dt>Tagy</dt><dd>{invoice.paperless.tags.join(", ") || "—"}</dd></div><div><dt>Poslední synchronizace</dt><dd>{pragueDateTime(invoice.paperless.last_synced_at)}</dd></div></dl>
          {invoice.paperless.sync_error && <div className="alert danger">{invoice.paperless.sync_error}</div>}
        </div>
        <div className="card"><div className="card-title"><div><h2>Evidence a dispozice</h2><p>Nezávislé na workflow; historický stav se při ignorování nemění.</p></div><StatusBadge value={invoice.disposition.status}/></div>
          {Array.isArray(duplicateCandidates) && duplicateCandidates.length > 0 && <div className="alert warning"><strong>Možná duplicita</strong>{duplicateCandidates.map((candidate, index) => { const row = candidate as {invoice_id?:string;matched_fields?:string[]}; return <p key={row.invoice_id || index}><a href={`/invoices/${row.invoice_id}`} target="_blank" rel="noreferrer">Otevřít kandidátní fakturu</a> · shoda: {(row.matched_fields || []).join(", ")}</p>; })}</div>}
          {invoice.disposition.status === "ACTIVE" ? <p>Faktura je aktivní.</p> : <dl className="metadata-grid"><div><dt>Důvod</dt><dd>{invoice.disposition.reason || "—"}</dd></div><div><dt>Rozhodl</dt><dd>{invoice.disposition.actor || "—"}</dd></div><div><dt>Čas</dt><dd>{pragueDateTime(invoice.disposition.changed_at)}</dd></div><div><dt>Duplicita faktury</dt><dd>{invoice.disposition.duplicate_of_invoice_id || "—"}</dd></div></dl>}
          {isManager && <div className="disposition-actions">{invoice.disposition.status === "ACTIVE" ? <><button className="button warning" disabled={Boolean(pending)} onClick={markDuplicate}>Označit jako duplicitu</button><button className="button secondary" disabled={Boolean(pending)} onClick={ignoreOther}>Označit jako nepotřebnou</button></> : <button className="button secondary" disabled={Boolean(pending)} onClick={() => void call("restore", `/invoices/${invoice.id}/restore`, { method: "POST", body: JSON.stringify({ comment: "Obnoveno ve frontě" }) }, "Dokument byl vrácen do aktivní fronty.")}>{pending==="restore"?"Obnovuji…":"Obnovit do aktivní fronty"}</button>}</div>}
        </div>
        <div className="card ai-card"><div className="card-title"><div><h2>AI extrakce</h2><p>Technický stav je oddělený od workflow faktury.</p></div><StatusBadge value={invoice.ai_status}/></div>
          {latestAI ? <>
            <dl className="metadata-grid"><div><dt>Model</dt><dd>{latestAI.model}</dd></div><div><dt>Verze</dt><dd>{latestAI.schema_version} · {latestAI.prompt_version}</dd></div><div><dt>Doba inference</dt><dd>{latestAI.duration_ms != null ? `${(latestAI.duration_ms / 1000).toFixed(2)} s` : "—"}</dd></div><div><dt>Běh</dt><dd>#{latestAI.extraction_revision}{latestAI.applied ? " · použit" : latestAI.requires_confirmation ? " · čeká na potvrzení" : ""}</dd></div></dl>
            {latestAI.error_message && <div className="alert danger"><strong>{latestAI.error_code}</strong>: {latestAI.error_message}{latestAI.schema_validation_errors?.length ? <><p>AI vrátila hodnotu v neočekávaném formátu:</p><ul>{latestAI.schema_validation_errors.map((item,index)=><li key={`${item.attempt}-${item.path}-${index}`}><strong>{schemaFieldLabel(item.path)}</strong>: {diagnosticValue(item.actual)} · očekáváno {item.expected} · {item.message} (pokus {item.attempt})</li>)}</ul><small>Raw odpověď zachována: {latestAI.raw_response_preserved?"ano":"ne"} · opravný retry: {latestAI.corrective_retry_count||0}</small></> : null}</div>}
            {latestAI.parsed_result && <details className="candidate"><summary>Strukturovaný výsledek běhu #{latestAI.extraction_revision}</summary><dl>{Object.entries(latestAI.parsed_result).filter(([key])=>key!=="schema_version").map(([key,value])=><div key={key}><dt>{key}</dt><dd>{shown(value && typeof value === "object" && "value" in value ? (value as {value: unknown}).value : value)}</dd></div>)}</dl></details>}
            {latestAI.requires_confirmation && candidateDifferences.length > 0 && <details className="candidate"><summary>Porovnat kandidát s aktuálními údaji ({candidateDifferences.length})</summary><dl>{candidateDifferences.map((row)=><div key={row.key}><dt>{row.label}</dt><dd>aktuálně: {shown(row.current)} → kandidát: {shown(row.candidate)}</dd></div>)}</dl></details>}
            {latestAI.requires_confirmation && isManager && <button className="button warning" disabled={Boolean(pending)} onClick={()=>{if(window.confirm("Nová extrakce nahradí rozdílné pracovní údaje, vytvoří novou revizi a audit. Ruční změny nebudou přepsány bez tohoto potvrzení. Pokračovat?")) void call("ai-apply", `/invoices/${invoice.id}/ai-extractions/${latestAI.id}/apply`,{method:"POST",body:JSON.stringify({confirm_overwrite:true})},"Kandidát byl převzat do nové revize.")}}>{pending==="ai-apply"?"Přebírám…":"Použít novou extrakci"}</button>}
          </> : <p>Extrakce zatím nebyla spuštěna.</p>}
          {isManager && <button className="button secondary" disabled={Boolean(pending)||["AI_PENDING","AI_PROCESSING"].includes(invoice.ai_status)} onClick={()=>void call("ai", `/invoices/${invoice.id}/ai-extractions`,{method:"POST"},"Extrakce byla zařazena do fronty.")}>{pending==="ai"?"Zařazuji…":"Spustit bezpečnou re-extrakci"}</button>}
          {invoice.ai.history.length > 1 && <details><summary>Historie AI běhů ({invoice.ai.history.length})</summary><ol>{invoice.ai.history.map((run)=><li key={run.id}>#{run.extraction_revision} · {run.model} · {run.status} · {run.duration_ms != null ? `${(run.duration_ms/1000).toFixed(2)} s` : "bez času"}{run.applied ? " · použit" : ""}</li>)}</ol></details>}
        </div>
        <div className="card"><div className="card-title"><div><h2>OCR text</h2><p>{invoice.paperless.ocr_text.length.toLocaleString("cs-CZ")} znaků · zdroj pro LLM je nedůvěryhodný vstup</p></div></div><pre className="ocr-text">{invoice.paperless.ocr_text || "Paperless zatím nevrátil OCR text."}</pre></div>
        <div className="card"><div className="card-title"><h2>Fakturační údaje</h2>{isManager&&<button className="button secondary" disabled={Boolean(pending)} onClick={saveData}>{pending==="data"?"Ukládám…":"Uložit změny"}</button>}</div>
          <dl className="metadata-grid"><div><dt>Účet – původní hodnota</dt><dd>{shown(invoice.data.bank_account_raw)}</dd></div><div><dt>Předčíslí</dt><dd>{shown(invoice.data.bank_account_prefix)}</dd></div><div><dt>Číslo účtu</dt><dd>{shown(invoice.data.bank_account_number)}</dd></div><div><dt>Kód banky</dt><dd>{shown(invoice.data.bank_code)}</dd></div></dl>
          <div className="form-grid">{editableFields.map(([key, label]) => {const fieldMessage=fieldErrors[key]||validationByField[key]?.message;const change=(value:string)=>{setForm({...form,[key]:value});setDirty(true);setFieldErrors({...fieldErrors,[key]:""})};return <label key={key} className={fieldMessage?"field-invalid":""}>{label}{dateFields.has(key)?<CzechDateInput field={key} invalid={Boolean(fieldMessage)} disabled={!isManager||Boolean(pending)} value={form[key]} onChange={change}/>:<input data-field={key} aria-invalid={Boolean(fieldMessage)} disabled={!isManager||Boolean(pending)} value={form[key]} onChange={(event)=>change(event.target.value)} />}{fieldMessage&&<small className="field-error">{fieldMessage}</small>}{evidence[key] && <small title={evidence[key]}>AI zdroj: {evidence[key]}</small>}</label>})}</div>
        </div>
        <div className="card vat-card"><div className="card-title"><div><h2>DPH a zaokrouhlení</h2><p>Hodnoty vytištěné na faktuře zůstávají autoritativní; přepočet je pouze kontrola.</p></div></div>
          <dl className="metadata-grid"><div><dt>Základ z faktury</dt><dd>{money(String(invoice.data.total_without_vat||""),String(invoice.data.currency||"CZK"))}</dd></div><div><dt>DPH z faktury</dt><dd>{money(String(invoice.data.total_vat||""),String(invoice.data.currency||"CZK"))}</dd></div><div><dt>Celkem z faktury</dt><dd>{money(String(invoice.data.total_amount||""),String(invoice.data.currency||"CZK"))}</dd></div></dl>
          {vatLines.length>0&&<div className="vat-lines">{vatLines.map((row,index)=><div key={index} className={row.adjustment_type==="ROUNDING"?"vat-line rounding":"vat-line"}><strong>{row.adjustment_type==="ROUNDING"?"Zaokrouhlení":`DPH řádek ${index+1}`}</strong><span>Základ {shown(row.taxable_base??row.base)} · sazba {shown(row.vat_rate??row.rate)} % · DPH {shown(row.vat_amount??row.vat)} · celkem {shown(Number(row.taxable_base??row.base??0)+Number(row.vat_amount??row.vat??0))}</span></div>)}</div>}
          {roundingLine&&<div className="alert warning"><strong>⚠ Rozdíl je pravděpodobně způsoben položkou Zaokrouhlení.</strong> Částka {shown(Number(roundingLine.taxable_base??roundingLine.base??0)+Number(roundingLine.vat_amount??roundingLine.vat??0))} Kč.</div>}
          {vatValidations.filter((row)=>row.severity!=="OK").map((row)=><div key={`${row.code}-${row.field_name}`} className={`alert ${row.severity==="WARNING"?"warning":"danger"}`}><strong>{row.code}</strong>: {row.message}<small> hodnota z faktury: {shown(row.actual)} · vypočtená hodnota: {shown(row.expected)}{row.details?.difference!=null?` · rozdíl: ${shown(row.details.difference)}`:""}</small></div>)}
        </div>
        <div className="card"><div className="card-title"><h2>Deterministická validace</h2><span>{invoice.validations.length}</span></div><div className="validation-list">{invoice.validations.map((v)=><div key={`${v.code}-${v.field_name}`} className={`validation ${v.severity.toLowerCase()}`}><StatusBadge value={v.severity}/><span>{v.message}{(v.expected != null || v.actual != null) && <small> očekáváno: {shown(v.expected)} · skutečnost: {shown(v.actual)}{v.details?.difference != null ? ` · rozdíl: ${shown(v.details.difference)}` : ""}</small>}</span></div>)}</div></div>
        <div className="card"><div className="card-title"><div><h2>Rozúčtování</h2><p>Částky jsou ukládány jako Decimal; nesoulad je blokující validace.</p></div>{isManager&&<button className="button secondary" disabled={Boolean(pending)} onClick={()=>changeAllocationRows([...allocationRows,{cost_center_id:centres[0]?.id || "",amount:"0.00",percentage:"0",note:"",vat_breakdown:"[]"}])}>Přidat řádek</button>}</div>
          {allocationError&&<div className="alert danger" role="alert"><strong>Rozúčtování:</strong> {allocationError.message} Očekáváno {shown(allocationError.expected)}, zadáno {shown(allocationError.actual)}.</div>}
          {missingApprover&&<div className="alert warning" role="alert"><strong>Schvalovatelé:</strong> Středisko {missingApprover.cost_center.code} nemá přiřazeného schvalovatele.</div>}
          <div className="allocation-totals"><span>Celkem faktura<strong>{money(invoice.allocation_summary.invoice_total,String(invoice.data.currency||"CZK"))}</strong></span><span>Rozúčtováno<strong>{money(invoice.allocation_summary.allocated,String(invoice.data.currency||"CZK"))}</strong></span><span>Zbývá rozúčtovat<strong>{money(invoice.allocation_summary.remaining,String(invoice.data.currency||"CZK"))}</strong></span></div>
          {isManager&&<div className="mode-switch"><label><input type="radio" checked={allocationMode==="amount"} onChange={()=>{setAllocationMode("amount");setDirty(true)}}/> Částkou</label><label><input type="radio" checked={allocationMode==="percentage"} onChange={()=>{setAllocationMode("percentage");setDirty(true)}}/> Procentem</label></div>}
          {isManager&&allocationRows.map((row,index)=><div className="allocation-row" key={index}><select value={row.cost_center_id} onChange={(e)=>changeAllocationRows(allocationRows.map((r,i)=>i===index?{...r,cost_center_id:e.target.value}:r))}><option value="">Vyberte středisko</option>{centres.map((c)=><option value={c.id} key={c.id}>{c.code} — {c.name}</option>)}</select><input aria-label={allocationMode==="amount"?"Částka":"Procento"} inputMode="decimal" value={allocationMode==="amount"?row.amount:row.percentage} onChange={(e)=>changeAllocationRows(allocationRows.map((r,i)=>i===index?{...r,[allocationMode]:e.target.value}:r))}/><input aria-label="Poznámka" placeholder="Poznámka" value={row.note} onChange={(e)=>changeAllocationRows(allocationRows.map((r,i)=>i===index?{...r,note:e.target.value}:r))}/><textarea aria-label="DPH rozpad allocation" title="Povinné jen při více sazbách a více střediscích" value={row.vat_breakdown} onChange={(e)=>changeAllocationRows(allocationRows.map((r,i)=>i===index?{...r,vat_breakdown:e.target.value}:r))}/><button className="icon-button" aria-label="Odebrat" onClick={()=>changeAllocationRows(allocationRows.filter((_,i)=>i!==index))}>×</button></div>)}
          {isManager&&<button className="button secondary" disabled={Boolean(pending)} onClick={saveAllocations}>{pending==="allocations"?"Ukládám…":"Uložit rozúčtování"}</button>}
          {invoice.allocations.map((allocation)=><div className="assignment-summary" key={allocation.id}><strong>{allocation.cost_center.code}: {money(allocation.amount,String(invoice.data.currency||"CZK"))}{allocation.percentage!=null?` · ${allocation.percentage} %`:""}</strong>{allocation.note&&<small>{allocation.note}</small>}{isManager&&<div className="approver-picker">{approvers.map((approver)=><label className="check-row" key={approver.subject}><input type="checkbox" disabled={Boolean(pending)} checked={(approverChoices[allocation.id]||[]).includes(approver.subject)} onChange={(e)=>{setApproverChoices({...approverChoices,[allocation.id]:e.target.checked?[...(approverChoices[allocation.id]||[]),approver.subject]:(approverChoices[allocation.id]||[]).filter(x=>x!==approver.subject)});setDirty(true)}}/><span>{approver.username}</span></label>)}<button className="button secondary" disabled={Boolean(pending)} onClick={()=>saveApprovers(allocation.id)}>{pending===`approvers-${allocation.id}`?"Ukládám…":"Uložit schvalovatele"}</button></div>}<span>{allocation.assignments.length ? allocation.assignments.map((a)=>`${approvers.find(x=>x.subject===a.approver_subject)?.username||a.approver_subject} (${a.status})`).join(", ") : "Schvalovatel není přiřazen"}</span></div>)}
        </div>
        {isManager&&<div className="card actions"><h2>Kontrola originálu a workflow</h2><p>{invoice.original_review_confirmed ? `Originál zkontroloval ${invoice.original_reviewed_by} (${pragueDateTime(invoice.original_reviewed_at)}).` : "Originál zatím nebyl explicitně potvrzen jako zkontrolovaný."}</p><div><button className="button secondary" disabled={!actionable||Boolean(pending)} onClick={()=>void call("original", `/invoices/${invoice.id}/confirm-original`,{method:"POST"},"Kontrola originálu byla potvrzena.")}>{pending==="original"?"Potvrzuji…":"Potvrdit kontrolu originálu"}</button><button className="button primary" disabled={!actionable||Boolean(pending)} onClick={()=>void call("submit", `/invoices/${invoice.id}/submit`,{method:"POST"},"Faktura byla předána ke schválení.")}>{pending==="submit"?"Předávám…":"Předat ke schválení"}</button>{invoice.status==="REJECTED"&&<button className="button warning" disabled={!actionable||Boolean(pending)} onClick={()=>void call("reopen", `/invoices/${invoice.id}/reopen`,{method:"POST"},"Faktura byla znovu otevřena.")}>{pending==="reopen"?"Otevírám…":"Znovu otevřít zamítnutou fakturu"}</button>}</div></div>}
        {isManager&&<div className="card pohoda-export"><div className="card-title"><div><h2>POHODA export</h2><p>XSD ověřuje strukturu XML; cílová účetní jednotka se ověřuje samostatnou sémantickou kontrolou. Stažení samo nepotvrzuje import.</p></div>{invoice.pohoda_export&&<StatusBadge value={invoice.pohoda_export.status}/>}</div>
          <div className={pohodaConfig?.identification==="NOT_CONFIGURED"?"alert danger":"alert info"}><strong>Cílová účetní jednotka:</strong> {pohodaConfig?.pohoda_target_ico ? `IČO ${pohodaConfig.pohoda_target_ico}` : "není nakonfigurována"}{pohodaConfig?.pohoda_target_key_configured ? " · key je nakonfigurován" : " · bez key"}</div>
          {invoice.pohoda_export?<dl className="metadata-grid"><div><dt>Cílové IČO artefaktu</dt><dd>{invoice.pohoda_export.pohoda_target_ico || "—"}</dd></div><div><dt>Sémantika cílové jednotky</dt><dd>{invoice.pohoda_export.pohoda_target_validation?.status || "NOT_RECORDED"}</dd></div><div><dt>XSD stav</dt><dd>{invoice.pohoda_export.status}</dd></div><div><dt>XSD verze</dt><dd>{invoice.pohoda_export.xsd_bundle_version}</dd></div><div><dt>Generátor</dt><dd>{invoice.pohoda_export.generator_version}</dd></div><div><dt>Encoding</dt><dd>{invoice.pohoda_export.encoding}</dd></div><div><dt>Velikost XML</dt><dd>{invoice.pohoda_export.xml_size.toLocaleString("cs-CZ")} B</dd></div><div><dt>SHA-256</dt><dd className="hash">{invoice.pohoda_export.xml_sha256}</dd></div><div><dt>Vygenerováno</dt><dd>{pragueDateTime(invoice.pohoda_export.generated_at)}</dd></div></dl>:<p>XML zatím nebylo vytvořeno.</p>}
          {invoice.pohoda_export?.validation_errors.map((row,index)=><div className="alert danger" key={index}>{row.message} {row.path&&<small>{row.path}</small>}</div>)}
          {invoice.pohoda_export?.status==="XSD_VALID"&&!targetUnitValid&&<div className="alert danger">XSD je platné, ale cílová účetní jednotka tohoto artefaktu nebyla sémanticky ověřena. Vytvořte re-export.</div>}
          <div className="decision-buttons">{invoice.status==="APPROVED"&&invoice.pohoda_export?.status!=="XSD_VALID"&&<button className="button primary" disabled={!actionable||Boolean(pending)||pohodaConfig?.identification==="NOT_CONFIGURED"} onClick={()=>generateXml()}>{pending==="xml"?"Generuji a validuji…":"Vygenerovat XML"}</button>}{invoice.pohoda_export?.status==="XSD_VALID"&&<>{targetUnitValid&&<a className="button secondary" href={`/api/exports/artifacts/${invoice.pohoda_export.id}/xml`}>Stáhnout XML</a>}{actionable&&<><a className="button secondary" href={`/api/invoices/${invoice.id}/pdf`}>Stáhnout PDF</a>{targetUnitValid&&<button className="button secondary" disabled={Boolean(pending)} onClick={()=>void createZip()}>{pending==="zip"?"Vytvářím ZIP…":"Stáhnout ZIP"}</button>}<button className="button warning" disabled={Boolean(pending)} onClick={()=>{const reason=window.prompt("Důvod re-exportu (volitelný)");if(reason!==null)generateXml(reason)}}>{pending==="xml"?"Generuji…":"Re-export"}</button></>}{!actionable&&<span className="muted">Nový export, PDF a ZIP jsou zablokované.</span>}</>}{invoice.status==="EXPORT_CREATED"&&invoice.pohoda_export&&targetUnitValid&&<button className="button primary" disabled={!actionable||Boolean(pending)} onClick={()=>void markImported()}>{pending==="import"?"Potvrzuji…":"OZNAČIT JAKO IMPORTOVÁNO DO POHODY"}</button>}</div>
          {invoice.pohoda_export&&<label className="response-upload">Nahrát POHODA response XML<input type="file" accept="application/xml,text/xml,.xml" onChange={(event)=>void uploadResponse(event.target.files?.[0])}/></label>}{responseSummary&&<div className="alert success">{responseSummary}</div>}
        </div>}
        <div className="card"><div className="card-title"><div><h2>Audit a historie</h2><p>Append-only události faktury a jejích revizí.</p></div><span>{audit.length}</span></div><ol className="audit-list">{audit.slice().reverse().map(event=><li key={event.id}><strong>{auditLabels[event.event_type] || event.event_type}</strong><span>revize {event.revision??"—"} · {String(event.metadata.actor_username || event.actor)} · {pragueDateTime(event.timestamp)}</span>{event.comment&&<p>{event.comment}</p>}<details><summary>Technické podrobnosti</summary><pre>{JSON.stringify({code:event.event_type,old:event.old_value,new:event.new_value,metadata:event.metadata},null,2)}</pre></details></li>)}</ol></div>
      </div>
    </div>
  </section>;
}
