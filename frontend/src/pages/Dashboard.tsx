import { useCallback, useEffect, useState } from "react";
import { InvoiceTable } from "../components/InvoiceTable";
import { api } from "../lib/api";
import type { Invoice, InvoiceListItem, User } from "../types";
import { InvoiceDetail } from "./InvoiceDetail";

export function Dashboard({ user, invoiceId, onNavigate }: { user: User; invoiceId?: string; onNavigate: (path: string) => void }) {
  const [rows, setRows] = useState<InvoiceListItem[]>([]);
  const [selected, setSelected] = useState<Invoice | null>(null);
  const [status, setStatus] = useState("");
  const [view, setView] = useState<"active" | "ignored" | "missing" | "all">("active");
  const [supplier, setSupplier] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams({ view });
      if (status) query.set("status", status);
      if (supplier) query.set("supplier", supplier);
      setRows(await api(`/invoices?${query}`));
      setError("");
    } catch (e) { setError((e as Error).message); }
  }, [status, supplier, view]);
  const open = useCallback(async (id: string) => {
    try { setSelected(await api(`/invoices/${id}`)); setError(""); } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (invoiceId) void open(invoiceId);
    else setSelected(null);
  }, [invoiceId, open]);

  if (invoiceId && selected?.id === invoiceId) return <InvoiceDetail invoice={selected} user={user} onBack={() => onNavigate("/")} onRefresh={() => void open(invoiceId)} />;
  const workflowFilters = [
    ["QUEUE_REVIEW", "Ke kontrole"], ["RETURNED", "Vrácené"], ["READY_FOR_APPROVAL", "Ke schválení"],
    ["AWAITING_APPROVAL", "Čeká na schválení"], ["APPROVED", "Schválené"], ["REJECTED", "Zamítnuté"],
  ];
  return <section>
    <div className="section-heading"><div><p className="eyebrow">Správa fronty</p><h1>Přijaté faktury</h1></div><button className="button secondary" onClick={() => void load()}>Obnovit</button></div>
    <div className="filters">
      <label>Pohled<select aria-label="Pohled fronty" value={view} onChange={(e) => setView(e.target.value as typeof view)}><option value="active">Aktivní</option><option value="ignored">Ignorované</option><option value="missing">Chybějící zdroj</option><option value="all">Všechny</option></select></label>
      <label>Workflow<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Všechny</option>{["NEW","VALIDATION","QUEUE_REVIEW","READY_FOR_APPROVAL","AWAITING_APPROVAL","RETURNED","REJECTED","APPROVED","READY_FOR_EXPORT","EXPORT_CREATED","IMPORTED_TO_POHODA"].map((s)=><option key={s}>{s}</option>)}</select></label>
      <label>Dodavatel<input value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="Hledat…" /></label>
    </div>
    <div className="quick-filters"><button className={!status?"active":""} onClick={()=>setStatus("")}>Všechny workflow</button>{workflowFilters.map(([value,label])=><button key={value} className={status===value?"active":""} onClick={()=>setStatus(value)}>{label}</button>)}</div>
    {error && <div className="alert danger">{error}</div>}
    <InvoiceTable rows={rows} onOpen={(id) => onNavigate(`/invoices/${encodeURIComponent(id)}`)} />
  </section>;
}
