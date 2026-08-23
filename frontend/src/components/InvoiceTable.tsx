import { money, shortDate } from "../lib/api";
import type { InvoiceListItem } from "../types";
import { StatusBadge } from "./StatusBadge";

export function InvoiceTable({ rows, onOpen }: { rows: InvoiceListItem[]; onOpen: (id: string) => void }) {
  if (!rows.length) return <div className="empty">Žádné faktury neodpovídají filtru.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Dokument</th><th>Korespondent</th><th>Vytvořeno</th><th>Částka</th><th>Synchronizace</th><th>Workflow</th></tr></thead>
        <tbody>{rows.map((row) => (
          <tr key={row.id} onClick={() => onOpen(row.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(row.id)}>
            <td><strong>{row.invoice_number || row.title || `Paperless #${row.paperless_document_id}`}</strong><small>Paperless #{row.paperless_document_id} · revize {row.current_revision_number}</small></td>
            <td>{row.supplier_name || row.correspondent || "—"}</td><td>{shortDate(row.paperless_created_at)}</td><td>{money(row.total_amount)}</td>
            <td><StatusBadge value={row.sync_status} /></td><td><StatusBadge value={row.status} /></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
