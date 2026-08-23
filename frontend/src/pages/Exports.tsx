import { useEffect, useState } from "react";
import { api, shortDate } from "../lib/api";
import type { ExportBatch, InvoiceListItem } from "../types";
import { StatusBadge } from "../components/StatusBadge";

export function Exports() {
  const [batches, setBatches] = useState<ExportBatch[]>([]);
  const [ready, setReady] = useState<InvoiceListItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const load = () => Promise.all([
    api<ExportBatch[]>("/exports"),
    api<InvoiceListItem[]>("/invoices?status=READY_FOR_EXPORT"),
  ]).then(([batchRows, invoiceRows]) => {
    setBatches(batchRows);
    setReady(invoiceRows);
  }).catch((reason: Error) => setError(reason.message));
  useEffect(() => { void load(); }, []);

  const create = async () => {
    try {
      await api("/exports", { method: "POST", body: JSON.stringify({ invoice_ids: selected }) });
      setSelected([]);
      await load();
    } catch (reason) { setError((reason as Error).message); }
  };
  const imported = async (batch: ExportBatch) => {
    const list = batch.items.map((item) => item.invoice_id).join("\n");
    if (!window.confirm(`Potvrzujete ruční import všech faktur této dávky do POHODY?\n\n${list}`)) return;
    try {
      await api(`/exports/${batch.id}/mark-imported`, { method: "POST", body: JSON.stringify({ confirmed: true }) });
      await load();
    } catch (reason) { setError((reason as Error).message); }
  };

  return <section>
    <div className="section-heading"><div><p className="eyebrow">POHODA</p><h1>Exportní dávky</h1><p className="muted">Do dávky lze zařadit pouze XSD-validní aktuální revize ve stavu READY_FOR_EXPORT.</p></div></div>
    {error && <div className="alert danger">{error}</div>}
    <div className="card"><h2>Faktury připravené k exportu</h2>
      {ready.map((invoice) => <label className="check-row" key={invoice.id}><input type="checkbox" checked={selected.includes(invoice.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, invoice.id] : selected.filter((id) => id !== invoice.id))}/><span>{invoice.invoice_number} — {invoice.supplier_name}</span></label>)}
      {!ready.length && <p className="muted">Aktuálně není připravena žádná faktura.</p>}
      <button className="button primary" disabled={!selected.length} onClick={() => void create()}>Vytvořit ZIP dávku</button>
    </div>
    <div className="batch-list">{batches.map((batch) => <article className="card" key={batch.id}><div className="card-title"><div><h2>{batch.batch_number}</h2><p>{shortDate(batch.created_at)} · {batch.invoice_ids.length} faktur</p></div><StatusBadge value={batch.status}/></div>
      <ul>{batch.items.map((item) => <li key={item.invoice_id}>{item.invoice_id} · revize {item.revision_id}</li>)}</ul>
      {batch.archive_sha256 && <p className="hash">ZIP SHA-256: {batch.archive_sha256}</p>}
      <div className="decision-buttons"><a className="button secondary" href={`/api/exports/${batch.id}/download`}>Stáhnout ZIP</a>{batch.status !== "IMPORTED" && <button className="button primary" onClick={() => void imported(batch)}>Označit celý batch jako importovaný</button>}</div>
    </article>)}</div>
  </section>;
}
