import { money, pragueDateTime } from "../lib/api";
import type { InvoiceListItem } from "../types";
import { StatusBadge } from "./StatusBadge";

export function InvoiceTable({ rows, onOpen }: { rows: InvoiceListItem[]; onOpen: (id: string) => void }) {
  if (!rows.length) return <div className="empty">Žádné faktury neodpovídají filtru.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Dokument</th><th>Dodavatel</th><th>Vytvořeno</th><th>Částka</th><th>Schválení</th><th>Validace</th><th>Workflow</th><th>Evidence</th></tr></thead>
        <tbody>{rows.map((row) => (
          <tr key={row.id} onClick={() => onOpen(row.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(row.id)}>
            <td><strong>{row.invoice_number || row.title || `Paperless #${row.paperless_document_id}`}</strong><small>Paperless #{row.paperless_document_id} · revize {row.current_revision_number}{row.uploaded_by ? ` · nahrál ${row.uploaded_by}` : ""}</small></td>
            <td>{row.supplier_name || row.correspondent || "—"}</td><td>{pragueDateTime(row.paperless_created_at || row.approval_created_at)}</td><td>{money(row.total_amount)}</td>
            <td>{row.approvals_done} / {row.approvals_required}</td><td>{row.blocking_error_count ? <StatusBadge value={`${row.blocking_error_count} BLOCKING_ERROR`}/>:row.warning_count?<StatusBadge value={`${row.warning_count} WARNING`}/>:<span>Bez nálezu</span>}</td><td><StatusBadge value={row.status} /></td><td><StatusBadge value={row.source_status}/>{row.disposition!=="ACTIVE"&&<> <StatusBadge value={row.disposition}/></>}{row.source_missing_at&&<small>od {pragueDateTime(row.source_missing_at)}</small>}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
