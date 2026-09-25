import { useCallback, useEffect, useRef, useState } from "react";
import { StatusBadge } from "../components/StatusBadge";
import { InvoiceUploadPanel, type InvoiceUploadPanelHandle } from "../components/InvoiceUploadPanel";
import { api, money, pragueDateTime } from "../lib/api";
import { documentTypeLabel, workflowStatusLabel } from "../lib/labels";
import type { Invoice, InvoiceListItem, User } from "../types";
import { InvoiceDetail } from "./InvoiceDetail";

type Props = {
  user: User;
  newSubmission?: boolean;
  invoiceId?: string;
  onNavigate: (path: string) => void;
};

export function Submissions({ user, newSubmission = false, invoiceId, onNavigate }: Props) {
  const [rows, setRows] = useState<InvoiceListItem[]>([]);
  const [selected, setSelected] = useState<Invoice | null>(null);
  const [error, setError] = useState("");
  const uploadPanelRef = useRef<InvoiceUploadPanelHandle>(null);

  const load = useCallback(async () => {
    try {
      const result = await api<InvoiceListItem[]>("/invoices?scope=uploaded&view=all");
      setRows(result);
      setError("");
      return result;
    } catch (caught) {
      setError((caught as Error).message);
      return [];
    }
  }, []);
  const open = useCallback(async (id: string) => {
    try {
      setSelected(await api<Invoice>(`/invoices/${id}`));
      setError("");
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (invoiceId) void open(invoiceId); else setSelected(null); }, [invoiceId, open]);
  useEffect(() => {
    const timer = window.setInterval(() => invoiceId ? void open(invoiceId) : void load(), 5000);
    return () => window.clearInterval(timer);
  }, [invoiceId, load, open]);

  if (invoiceId && selected?.id === invoiceId) {
    return <InvoiceDetail
      invoice={selected}
      user={user}
      onBack={() => onNavigate("/submissions")}
      onRefresh={(updated) => updated ? setSelected(updated) : void open(invoiceId)}
    />;
  }

  return <section>
    <div className="section-heading queue-heading">
      <div>
        <p className="eyebrow">Předkladatel faktury</p>
        <h1>{newSubmission ? "Předložit fakturu" : "Moje předložené"}</h1>
        <p className="muted">Nahrajte PDF, zkontrolujte vytěžené údaje a navrhněte rozdělení nákladu. Předání správci nevytváří schválení.</p>
      </div>
      {!newSubmission && <button className="button primary" onClick={() => onNavigate("/submissions/new")}>+ Předložit fakturu</button>}
    </div>
    {error && <div className="alert danger">{error}</div>}
    {newSubmission && <InvoiceUploadPanel
      ref={uploadPanelRef}
      user={user}
      submissionMode="INVOICE_SUBMITTER"
      onQueueChanged={async (id) => {
        await load();
        if (id) onNavigate(`/submissions/${encodeURIComponent(id)}`);
        return Boolean(id);
      }}
    />}
    {!newSubmission && (!rows.length ? <div className="empty">Zatím jste nepředložili žádnou fakturu.</div> : <div className="table-wrap">
      <table>
        <thead><tr><th>Předloženo</th><th>Dodavatel a doklad</th><th>Typ</th><th>Částka</th><th>K zaplacení</th><th>Stav</th><th>Poslední změna</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id} tabIndex={0} onClick={() => onNavigate(`/submissions/${encodeURIComponent(row.id)}`)} onKeyDown={(event) => event.key === "Enter" && onNavigate(`/submissions/${encodeURIComponent(row.id)}`)}>
          <td>{row.submitted_to_queue_at ? pragueDateTime(row.submitted_to_queue_at) : "Rozpracováno"}</td>
          <td><strong>{row.supplier_name || row.correspondent || "—"}</strong><small>{row.invoice_number || row.title}</small></td>
          <td>{documentTypeLabel(row.document_type)}</td>
          <td>{money(row.total_amount)}</td>
          <td>{row.payment_required === true ? "Ano" : row.payment_required === false ? "Ne" : "Neurčeno"}</td>
          <td><StatusBadge value={row.status}/><small>{workflowStatusLabel(row.status)}</small></td>
          <td>{pragueDateTime(row.updated_at)}</td>
        </tr>)}</tbody>
      </table>
    </div>)}
  </section>;
}
