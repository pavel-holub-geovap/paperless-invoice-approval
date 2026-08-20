import { money, shortDate } from "../lib/api";
import type { InvoiceListItem } from "../types";
import { StatusBadge } from "./StatusBadge";

export function InvoiceTable({ rows, onOpen }: { rows: InvoiceListItem[]; onOpen: (id: string) => void }) {
  if (!rows.length) return <div className="empty">Žádné faktury neodpovídají filtru.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Faktura</th><th>Dodavatel</th><th>Částka</th><th>Splatnost</th><th>Schválení</th><th>Stav</th></tr></thead>
        <tbody>{rows.map((row) => (
          <tr key={row.id} onClick={() => onOpen(row.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(row.id)}>
            <td><strong>{row.invoice_number || `Paperless #${row.paperless_document_id}`}</strong><small>revize {row.current_revision_number}</small></td>
            <td>{row.supplier_name || "—"}</td><td>{money(row.total_amount)}</td><td>{shortDate(row.due_date)}</td>
            <td>{row.approvals_done}/{row.approvals_required}</td><td><StatusBadge value={row.status} /></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

