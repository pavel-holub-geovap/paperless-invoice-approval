import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, pragueDateTime } from "../lib/api";
import type { AdminInvoice, AdminPurgeAudit, AdminPurgeResponse, UserReference } from "../types";
import { CostCenters } from "./CostCenters";

type PurgeTarget = { invoiceIds: string[]; label: string; bulk: boolean };

export function AdminPage() {
  const [invoices, setInvoices] = useState<AdminInvoice[]>([]);
  const [audits, setAudits] = useState<AdminPurgeAudit[]>([]);
  const [users, setUsers] = useState<UserReference[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [target, setTarget] = useState<PurgeTarget | null>(null);
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [invoiceRows, auditRows, userRows] = await Promise.all([
        api<AdminInvoice[]>("/admin/invoices"),
        api<AdminPurgeAudit[]>("/admin/purge-audits"),
        api<UserReference[]>("/users"),
      ]);
      setInvoices(invoiceRows);
      setAudits(auditRows);
      setUsers(userRows);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const toggle = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const openSingle = (invoice: AdminInvoice) => {
    setTarget({ invoiceIds: [invoice.id], label: invoice.invoice_number || invoice.paperless_title || invoice.id, bulk: false });
    setReason(""); setConfirmation(""); setMessage(""); setError("");
  };

  const openBulk = () => {
    const invoiceIds = [...selected];
    if (!invoiceIds.length) return;
    setTarget({ invoiceIds, label: `${invoiceIds.length} vybraných dokladů`, bulk: true });
    setReason(""); setConfirmation(""); setMessage(""); setError("");
  };

  const purge = async (event: FormEvent) => {
    event.preventDefault();
    if (!target) return;
    setPending(true); setError(""); setMessage("");
    try {
      if (target.bulk) {
        const result = await api<AdminPurgeResponse>("/admin/invoices/purge", {
          method: "POST",
          body: JSON.stringify({ invoice_ids: target.invoiceIds, confirmation: "SMAZAT VYBRANÉ", reason }),
        });
        setMessage(`Úspěšně odstraněno: ${result.succeeded}. Selhalo: ${result.failed}.`);
        if (result.failed) {
          setError(result.results.filter((row) => row.status === "FAILED").map((row) => `${row.invoice_id}: ${row.error}`).join(" "));
        }
      } else {
        await api(`/admin/invoices/${target.invoiceIds[0]}/purge`, {
          method: "POST",
          body: JSON.stringify({ confirmation: "SMAZAT", reason }),
        });
        setMessage(`Doklad ${target.label} byl nevratně odstraněn.`);
      }
      setTarget(null); setSelected(new Set()); await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };

  const expected = target?.bulk ? "SMAZAT VYBRANÉ" : "SMAZAT";
  return <section className="admin-page">
    <div className="section-heading"><div><p className="eyebrow">Systémová konfigurace</p><h1>Administrace</h1><p className="muted">Role se přidělují výhradně v Keycloaku. ADMIN automaticky nezískává oprávnění správce fronty.</p></div></div>
    {message && <div className="alert success" role="status">{message}</div>}
    {error && <div className="alert danger" role="alert">{error}</div>}

    <CostCenters/>

    <section className="admin-block" aria-labelledby="admin-documents-title">
      <div className="section-heading"><div><p className="eyebrow">Destruktivní operace</p><h2 id="admin-documents-title">Správa a odstranění dokladů</h2><p className="muted">PURGE odstraní zvolený doklad z Approval, originál a schválené kopie z Paperless i navázané exportní artefakty.</p></div><button className="button danger" disabled={!selected.size} onClick={openBulk}>Nevratně odstranit vybrané ({selected.size})</button></div>
      <div className="table-wrap"><table><thead><tr><th>Vybrat</th><th>Doklad</th><th>Dodavatel</th><th>Datum</th><th>Stav</th><th>Paperless ID</th><th>Původ</th><th>Akce</th></tr></thead><tbody>{invoices.map((invoice) => <tr key={invoice.id}>
        <td><input type="checkbox" aria-label={`Vybrat doklad ${invoice.invoice_number || invoice.id}`} checked={selected.has(invoice.id)} onChange={() => toggle(invoice.id)}/></td>
        <td><strong>{invoice.invoice_number || "—"}</strong><small>{invoice.id}</small></td>
        <td>{invoice.supplier_name || "—"}</td><td>{pragueDateTime(invoice.paperless_created_at)}</td><td>{invoice.status}</td><td>{invoice.paperless_document_id}</td><td>{invoice.upload_origin}<small>{invoice.uploaded_by || "—"}</small></td>
        <td><button className="button danger" onClick={() => openSingle(invoice)}>Nevratně odstranit</button></td>
      </tr>)}</tbody></table></div>
      {!invoices.length && <p className="empty">Nejsou dostupné žádné doklady.</p>}
    </section>

    <section className="admin-block" aria-labelledby="purge-audit-title"><div className="section-heading"><div><p className="eyebrow">Minimální bezpečný audit</p><h2 id="purge-audit-title">Historie PURGE</h2></div></div>
      <div className="table-wrap"><table><thead><tr><th>Datum</th><th>Uživatel</th><th>Invoice ID</th><th>Paperless ID</th><th>Důvod</th><th>Výsledek</th></tr></thead><tbody>{audits.map((row) => <tr key={row.id}><td>{pragueDateTime(row.created_at)}</td><td>{row.actor_display_name || row.actor_subject}</td><td className="hash">{row.invoice_id}</td><td>{row.paperless_document_ids.join(", ") || "—"}</td><td>{row.reason}</td><td>{row.result}</td></tr>)}</tbody></table></div>
    </section>

    <section className="admin-block" aria-labelledby="identity-title"><div className="section-heading"><div><p className="eyebrow">Pouze pro čtení</p><h2 id="identity-title">Známí uživatelé</h2><p className="muted">Seznam vzniká automaticky po prvním přihlášení. Role se spravují v Keycloaku.</p></div></div>
      <div className="table-wrap"><table><thead><tr><th>Uživatel</th><th>Keycloak subject</th><th>Aktuálně známé role</th></tr></thead><tbody>{users.map((row) => <tr key={row.subject}><td>{row.username}<small>{row.email}</small></td><td className="hash">{row.subject}</td><td>{row.roles.join(", ")}</td></tr>)}</tbody></table></div>
    </section>

    {target && <div className="modal-backdrop"><section className="purge-modal" role="dialog" aria-modal="true" aria-labelledby="purge-title">
      <p className="eyebrow danger-text">Nevratná operace</p><h2 id="purge-title">Nevratně odstranit {target.label}?</h2>
      <p>Doklad bude nevratně odstraněn z Approval i Paperless. Odstraní se originál, jednoznačně navázané schválené kopie a exportní artefakty. Operaci nelze vrátit.</p>
      <form onSubmit={(event) => void purge(event)}><label>Důvod odstranění<textarea aria-label="Důvod nevratného odstranění" required minLength={3} maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)}/></label>
        <label>Pro potvrzení napište <strong>{expected}</strong><input aria-label="Potvrzení nevratného odstranění" autoComplete="off" value={confirmation} onChange={(event) => setConfirmation(event.target.value)}/></label>
        <div className="modal-actions"><button type="button" className="button secondary" disabled={pending} onClick={() => setTarget(null)}>Zrušit</button><button className="button danger" disabled={pending || reason.trim().length < 3 || confirmation !== expected}>{pending ? "Odstraňuji…" : "Nevratně odstranit"}</button></div>
      </form>
    </section></div>}
  </section>;
}
