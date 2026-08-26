import { useCallback, useEffect, useRef, useState } from "react";
import { InvoiceTable } from "../components/InvoiceTable";
import { InvoiceUploadPanel, type InvoiceUploadPanelHandle } from "../components/InvoiceUploadPanel";
import { api } from "../lib/api";
import type { Invoice, InvoiceListItem, User } from "../types";
import { InvoiceDetail } from "./InvoiceDetail";

export function Dashboard({ user, invoiceId, onNavigate }: { user: User; invoiceId?: string; onNavigate: (path: string) => void }) {
  const [rows, setRows] = useState<InvoiceListItem[]>([]);
  const [selected, setSelected] = useState<Invoice | null>(null);
  const [status, setStatus] = useState("");
  const [view, setView] = useState<"active" | "ignored" | "missing" | "all">("active");
  const [supplier, setSupplier] = useState("");
  const [sort, setSort] = useState<"source_desc" | "source_asc">("source_desc");
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const uploadPanelRef = useRef<InvoiceUploadPanelHandle>(null);
  const refreshInFlight = useRef(false);
  const isManager = user.roles.includes("QUEUE_MANAGER");
  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams({ view, sort });
      if (status) query.set("status", status);
      if (supplier) query.set("supplier", supplier);
      const nextRows = await api<InvoiceListItem[]>(`/invoices?${query}`);
      setRows(nextRows);
      setError("");
      return nextRows;
    } catch (e) { setError((e as Error).message); return []; }
  }, [status, supplier, view, sort]);
  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    setRefreshing(true);
    try { await load(); } finally { refreshInFlight.current = false; setRefreshing(false); }
  }, [load]);
  const refreshAfterUpload = useCallback(async (invoiceId?: string) => {
    const nextRows = await load();
    return Boolean(invoiceId && nextRows.some((row) => row.id === invoiceId));
  }, [load]);
  const open = useCallback(async (id: string) => {
    try { setSelected(await api(`/invoices/${id}`)); setError(""); } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);
  useEffect(() => {
    if (invoiceId) void open(invoiceId);
    else setSelected(null);
  }, [invoiceId, open]);
  useEffect(() => {
    if (!invoiceId) return;
    const timer = window.setInterval(() => void open(invoiceId), 3000);
    return () => window.clearInterval(timer);
  }, [invoiceId, open]);

  if (invoiceId && selected?.id === invoiceId) return <InvoiceDetail invoice={selected} user={user} onBack={() => onNavigate("/")} onRefresh={() => void open(invoiceId)} />;
  const workflowFilters = [
    ["QUEUE_REVIEW", "Ke kontrole"], ["RETURNED", "Vrácené"], ["READY_FOR_APPROVAL", "Ke schválení"],
    ["AWAITING_APPROVAL", "Čeká na schválení"], ["APPROVED", "Schválené"], ["REJECTED", "Zamítnuté"],
  ];
  return <section>
    <div className="section-heading queue-heading">
      <div><p className="eyebrow">Správa fronty</p><h1>Přijaté faktury</h1></div>
      <div className="queue-actions" aria-label="Akce fronty">
        {isManager && <button className="button primary" onClick={() => uploadPanelRef.current?.openFilePicker()}>+ Nahrát fakturu</button>}
        <button className="button secondary" disabled={refreshing} aria-busy={refreshing} onClick={() => void refresh()}>{refreshing ? "Obnovuji…" : "Obnovit"}</button>
      </div>
    </div>
    <InvoiceUploadPanel ref={uploadPanelRef} user={user} onQueueChanged={refreshAfterUpload} />
    <div className="filters">
      <label>Pohled<select aria-label="Pohled fronty" value={view} onChange={(e) => setView(e.target.value as typeof view)}><option value="active">Aktivní</option><option value="ignored">Ignorované</option><option value="missing">Chybějící zdroj</option><option value="all">Všechny</option></select></label>
      <label>Workflow<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Všechny</option>{["NEW","VALIDATION","QUEUE_REVIEW","READY_FOR_APPROVAL","AWAITING_APPROVAL","RETURNED","REJECTED","APPROVED","READY_FOR_EXPORT","EXPORT_CREATED","IMPORTED_TO_POHODA"].map((s)=><option key={s}>{s}</option>)}</select></label>
      <label>Dodavatel<input value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="Hledat…" /></label>
      <label>Řazení<select aria-label="Řazení podle vložení" value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}><option value="source_desc">Nejnovější vložené</option><option value="source_asc">Nejstarší vložené</option></select></label>
    </div>
    <div className="quick-filters"><button className={!status?"active":""} onClick={()=>setStatus("")}>Všechny workflow</button>{workflowFilters.map(([value,label])=><button key={value} className={status===value?"active":""} onClick={()=>setStatus(value)}>{label}</button>)}</div>
    {error && <div className="alert danger">{error}</div>}
    <InvoiceTable rows={rows} onOpen={(id) => onNavigate(`/invoices/${encodeURIComponent(id)}`)} />
  </section>;
}
