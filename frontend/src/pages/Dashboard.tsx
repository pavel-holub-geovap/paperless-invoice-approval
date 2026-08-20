import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Invoice, InvoiceListItem, User } from "../types";
import { InvoiceTable } from "../components/InvoiceTable";
import { InvoiceDetail } from "./InvoiceDetail";

export function Dashboard({ user }: { user: User }) {
  const [rows, setRows] = useState<InvoiceListItem[]>([]);
  const [selected, setSelected] = useState<Invoice | null>(null);
  const [status, setStatus] = useState("");
  const [supplier, setSupplier] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams();
      if (status) query.set("status", status);
      if (supplier) query.set("supplier", supplier);
      setRows(await api(`/invoices?${query}`));
      setError("");
    } catch (e) { setError((e as Error).message); }
  }, [status, supplier]);
  useEffect(() => { void load(); }, [load]);

  const open = async (id: string) => {
    try { setSelected(await api(`/invoices/${id}`)); } catch (e) { setError((e as Error).message); }
  };
  if (selected) return <InvoiceDetail invoice={selected} user={user} onBack={() => { setSelected(null); void load(); }} onRefresh={() => open(selected.id)} />;
  return <section>
    <div className="section-heading"><div><p className="eyebrow">Správa fronty</p><h1>Přijaté faktury</h1></div><button className="button secondary" onClick={() => void load()}>Obnovit</button></div>
    <div className="filters">
      <label>Stav<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Všechny</option>{["NEW","AI_PROCESSING","NEEDS_REVIEW","QUEUE_REVIEW","AWAITING_APPROVAL","RETURNED","REJECTED","APPROVED","READY_FOR_EXPORT","EXPORT_CREATED","IMPORTED_TO_POHODA"].map((s)=><option key={s}>{s}</option>)}</select></label>
      <label>Dodavatel<input value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="Hledat…" /></label>
    </div>
    {error && <div className="alert danger">{error}</div>}
    <InvoiceTable rows={rows} onOpen={open} />
  </section>;
}

