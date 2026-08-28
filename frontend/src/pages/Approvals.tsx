import { useEffect, useState } from "react";
import { StatusBadge } from "../components/StatusBadge";
import { api, money } from "../lib/api";
import { formatDateCs, formatDateTimeCs } from "../lib/dates";
import type {
  ApprovalTask,
  ApproverHistoryAssignment,
  ApproverHistoryDetail,
  ApproverHistoryResponse,
} from "../types";

type Props = {
  history?: boolean;
  historyInvoiceId?: string;
  onNavigate?: (path: string) => void;
};

const decisionLabels: Record<string, string> = {
  APPROVE: "Schváleno",
  RETURN: "Vráceno",
  REJECT: "Odmítnuto",
};

const decisionPhrases: Record<string, string> = {
  APPROVE: "Schválil jste",
  RETURN: "Vrátil jste ke kontrole",
  REJECT: "Odmítl jste",
};

const workflowLabels: Record<string, string> = {
  NEW: "Nová",
  VALIDATION: "Validace",
  QUEUE_REVIEW: "Ke kontrole",
  READY_FOR_APPROVAL: "Připravená ke schválení",
  AWAITING_APPROVAL: "Čeká na schválení",
  RETURNED: "Vrácená",
  REJECTED: "Odmítnutá",
  APPROVED: "Schválená",
  READY_FOR_EXPORT: "Připravená k exportu",
  EXPORT_CREATED: "Export vytvořen",
  IMPORTED_TO_POHODA: "Importovaná do POHODY",
};

function navigateFallback(path: string) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function decisionLabel(row: ApproverHistoryAssignment): string {
  if (row.decision) return decisionLabels[row.decision] || row.decision;
  return row.invalidated ? "Zneplatněno" : "Bez rozhodnutí";
}

function since(period: string): string | undefined {
  if (period === "ALL") return undefined;
  const value = new Date();
  value.setDate(value.getDate() - Number(period));
  return value.toISOString().slice(0, 10);
}

function HistoryDetail({ detail, onBack }: { detail: ApproverHistoryDetail; onBack: () => void }) {
  const latest = detail.history[0];
  return <section className="history-detail">
    <button className="back" onClick={onBack}>← Zpět do mojí historie</button>
    <div className="section-heading">
      <div>
        <p className="eyebrow">Historická faktura · Paperless #{detail.paperless_document_id}</p>
        <h1>{detail.invoice_number || detail.paperless.title || "Faktura"}</h1>
        <p className="muted">{detail.supplier_name || "Neznámý dodavatel"}</p>
      </div>
      <StatusBadge value={detail.current_status}/>
    </div>
    <div className="detail-grid">
      <div className="pdf-panel">
        {detail.pdf_available
          ? <><iframe title="Originální faktura" src={`/api/invoices/${detail.invoice_id}/pdf`}/><a className="button secondary" href={`/api/invoices/${detail.invoice_id}/pdf`} target="_blank" rel="noreferrer">Otevřít PDF v novém okně</a></>
          : <div className="source-missing">Originální dokument již není v Paperless dostupný.</div>}
      </div>
      <div className="work-panel">
        {latest && <div className="card history-decision-card">
          <p className="eyebrow">Moje historické rozhodnutí</p>
          <h2>{latest.decision ? decisionPhrases[latest.decision] : decisionLabel(latest)}</h2>
          <p>{formatDateTimeCs(latest.decision_at || latest.assigned_at)}</p>
          <dl className="metadata-grid">
            <div><dt>Středisko</dt><dd>{latest.cost_center.code} – {latest.cost_center.name}</dd></div>
            <div><dt>Částka</dt><dd>{money(latest.amount, detail.currency)}</dd></div>
            <div><dt>Revize</dt><dd>{latest.revision ?? "—"}</dd></div>
            <div><dt>Rozhodnutí</dt><dd>{decisionLabel(latest)}</dd></div>
          </dl>
          {latest.comment && <div className="history-comment"><strong>Důvod / komentář</strong><p>{latest.comment}</p></div>}
          {latest.invalidated && <div className="alert warning">⚠ Toto rozhodnutí bylo později zneplatněno změnou faktury a již není platným schválením.</div>}
        </div>}
        <div className="card">
          <p className="eyebrow">Aktuální stav faktury</p>
          <h2>{workflowLabels[detail.current_status] || detail.current_status}</h2>
          <p>Aktuální revize: {detail.current_revision}</p>
          {latest?.invalidated && <p className="muted">Vaše původní rozhodnutí patří k revizi {latest.revision}.</p>}
        </div>
        <div className="card">
          <div className="card-title"><div><h2>Údaje faktury</h2><p>Režim pouze pro čtení</p></div></div>
          <dl className="metadata-grid">
            <div><dt>Dodavatel</dt><dd>{String(detail.current_data.supplier_name || detail.supplier_name || "—")}</dd></div>
            <div><dt>Číslo faktury</dt><dd>{String(detail.current_data.invoice_number || detail.invoice_number || "—")}</dd></div>
            <div><dt>Částka celkem</dt><dd>{money(String(detail.current_data.total_amount || ""), detail.currency)}</dd></div>
            <div><dt>Datum vystavení</dt><dd>{formatDateCs(String(detail.current_data.issue_date || ""))}</dd></div>
            <div><dt>Původní soubor</dt><dd>{detail.paperless.original_filename || "—"}</dd></div>
            <div><dt>Tagy</dt><dd>{detail.paperless.tags.join(", ") || "—"}</dd></div>
          </dl>
        </div>
        <div className="card">
          <div className="card-title"><div><h2>Moje schvalovací historie této faktury</h2><p>Lidsky čitelné záznamy všech vašich assignmentů a revizí.</p></div></div>
          <ol className="personal-history-list">
            {detail.history.map((row) => <li key={row.assignment_id}>
              <div><strong>{decisionLabel(row)}</strong><span>{formatDateTimeCs(row.decision_at || row.assigned_at)}</span></div>
              <p>Středisko {row.cost_center.code} – {row.cost_center.name} · {money(row.amount, detail.currency)} · revize {row.revision}</p>
              {row.comment && <p>„{row.comment}“</p>}
              {row.invalidated && <small>⚠ Později zneplatněno změnou faktury.</small>}
            </li>)}
          </ol>
        </div>
      </div>
    </div>
  </section>;
}

export function Approvals({ history = false, historyInvoiceId, onNavigate }: Props) {
  const navigate = onNavigate || navigateFallback;
  const [tasks, setTasks] = useState<ApprovalTask[]>([]);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState("");
  const [query, setQuery] = useState("");
  const [decision, setDecision] = useState("");
  const [period, setPeriod] = useState("ALL");
  const [costCenter, setCostCenter] = useState("");
  const [page, setPage] = useState(1);
  const [historyResult, setHistoryResult] = useState<ApproverHistoryResponse>({ items: [], page: 1, page_size: 25, total: 0 });
  const [detail, setDetail] = useState<ApproverHistoryDetail | null>(null);

  const loadTasks = () => api<ApprovalTask[]>("/approvals/mine").then(setTasks).catch((e) => setError(e.message));
  useEffect(() => {
    void loadTasks();
    const timer = window.setInterval(() => void loadTasks(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!historyInvoiceId) { setDetail(null); return; }
    setError("");
    void api<ApproverHistoryDetail>(`/approvals/history/${historyInvoiceId}`)
      .then(setDetail)
      .catch((e) => setError(e.message));
  }, [historyInvoiceId]);

  useEffect(() => {
    if (!history || historyInvoiceId) return;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ page: String(page), page_size: "25" });
      if (query.trim()) params.set("q", query.trim());
      if (decision) params.set("decision", decision);
      if (costCenter) params.set("cost_center", costCenter);
      const dateFrom = since(period);
      if (dateFrom) params.set("date_from", dateFrom);
      setPending("history");
      void api<ApproverHistoryResponse>(`/approvals/history?${params}`)
        .then((result) => { setHistoryResult(result); setError(""); })
        .catch((e) => setError(e.message))
        .finally(() => setPending(""));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [history, historyInvoiceId, query, decision, period, costCenter, page]);

  const current = tasks.filter((task) => task.current);
  const pages = Math.max(1, Math.ceil(historyResult.total / historyResult.page_size));
  const resetPage = (change: () => void) => { setPage(1); change(); };
  const decide = async (task: ApprovalTask, action: string) => {
    if (pending) return;
    setPending(`${task.id}-${action}`);
    setMessage("");
    try {
      await api(`/approvals/${task.id}/decision`, { method: "POST", body: JSON.stringify({ action, comment: comments[task.id] || null }) });
      setError("");
      setMessage(action === "APPROVE" ? "Úkol byl schválen." : action === "RETURN" ? "Úkol byl vrácen." : "Faktura byla zamítnuta.");
      void loadTasks();
    } catch (e) { setError((e as Error).message); } finally { setPending(""); }
  };

  if (historyInvoiceId && detail) return <HistoryDetail detail={detail} onBack={() => navigate("/approvals/history")}/>;

  return <section>
    <div className="approver-tabs" role="tablist">
      <button className={!history ? "active" : ""} role="tab" aria-selected={!history} onClick={() => navigate("/approvals")}>Ke schválení ({current.length})</button>
      <button className={history ? "active" : ""} role="tab" aria-selected={history} onClick={() => navigate("/approvals/history")}>Moje historie</button>
    </div>
    {message && <div className="alert success">{message}</div>}
    {error && <div className="alert danger">{error}</div>}
    {!history ? <>
      <div className="section-heading"><div><p className="eyebrow">Moje práce</p><h1>Ke schválení</h1><p className="muted">Každý úkol patří konkrétní revizi, rozúčtování, středisku a částce. Seznam se automaticky obnovuje.</p></div></div>
      {!current.length ? <div className="empty">Momentálně nemáte žádný aktivní úkol ke schválení.</div> : <div className="task-grid">{current.map((task) => <article className="card approval-card" key={task.id}><div className="card-title"><div><h2>{task.invoice_number || "Faktura"}</h2><p>{task.supplier_name} · revize {task.revision}</p></div><StatusBadge value={task.assignment_status}/></div><div className="amount-block"><span>Faktura celkem<strong>{money(task.invoice_total, task.currency)}</strong></span><span>Schvaluji za {task.cost_center}<strong>{money(task.allocation_amount, task.currency)}</strong>{task.allocation_percentage != null && <small>{task.allocation_percentage} %</small>}</span></div>{task.allocation_note && <p className="muted">Poznámka: {task.allocation_note}</p>}<dl className="metadata-grid"><div><dt>Datum vystavení</dt><dd>{formatDateCs(String(task.invoice_data.issue_date || ""))}</dd></div><div><dt>Splatnost</dt><dd>{formatDateCs(String(task.invoice_data.due_date || ""))}</dd></div><div><dt>Variabilní symbol</dt><dd>{String(task.invoice_data.variable_symbol || "—")}</dd></div><div><dt>Platební údaj</dt><dd>{String(task.invoice_data.iban || task.invoice_data.bank_account || "—")}</dd></div></dl><a className="button secondary" href={`/api/invoices/${task.invoice_id}/pdf`} target="_blank" rel="noreferrer">Zobrazit originální PDF</a><textarea disabled={Boolean(pending)} placeholder="Komentář je povinný pro vrácení a zamítnutí" value={comments[task.id] || ""} onChange={(e) => setComments({ ...comments, [task.id]: e.target.value })}/><div className="decision-buttons"><button className="button primary" disabled={Boolean(pending)} onClick={() => void decide(task, "APPROVE")}>{pending === `${task.id}-APPROVE` ? "Schvaluji…" : "Schválit"}</button><button className="button warning" disabled={Boolean(pending)} onClick={() => void decide(task, "RETURN")}>{pending === `${task.id}-RETURN` ? "Vracím…" : "Vrátit"}</button><button className="button danger" disabled={Boolean(pending)} onClick={() => void decide(task, "REJECT")}>{pending === `${task.id}-REJECT` ? "Zamítám…" : "Zamítnout"}</button></div></article>)}</div>}
    </> : <>
      <div className="section-heading"><div><p className="eyebrow">Moje historie</p><h1>Historie faktur</h1><p className="muted">Faktury, ke kterým jste měl v libovolné revizi schvalovací vztah.</p></div></div>
      <div className="history-search"><label>Hledat ve fakturách a jejich obsahu<input aria-label="Hledat ve fakturách a jejich obsahu" placeholder="Dodavatel, číslo faktury nebo text z OCR…" value={query} onChange={(e) => resetPage(() => setQuery(e.target.value))}/></label></div>
      <div className="filters history-filters">
        <label>Rozhodnutí<select aria-label="Rozhodnutí" value={decision} onChange={(e) => resetPage(() => setDecision(e.target.value))}><option value="">Všechna</option><option value="APPROVE">Schváleno</option><option value="RETURN">Vráceno</option><option value="REJECT">Odmítnuto</option><option value="NONE">Bez rozhodnutí</option></select></label>
        <label>Období<select aria-label="Období" value={period} onChange={(e) => resetPage(() => setPeriod(e.target.value))}><option value="ALL">Všechna</option><option value="90">Posledních 90 dní</option><option value="365">Poslední rok</option></select></label>
        <label>Středisko<select aria-label="Středisko" value={costCenter} onChange={(e) => resetPage(() => setCostCenter(e.target.value))}><option value="">Všechna</option>{historyResult.filters?.cost_centers.map((center) => <option key={center.code} value={center.code}>{center.code} – {center.name}</option>)}</select></label>
      </div>
      {pending === "history" && <p className="muted">Vyhledávám…</p>}
      {!historyResult.items.length && pending !== "history" ? <div className="empty">V historii nebyly nalezeny žádné faktury.</div> : <div className="table-wrap"><table><thead><tr><th>Dokument</th><th>Dodavatel</th><th>Středisko</th><th>Moje částka</th><th>Moje rozhodnutí</th><th>Rozhodnuto</th><th>Aktuální stav</th></tr></thead><tbody>{historyResult.items.map((row) => <tr key={row.invoice_id} onClick={() => navigate(`/approvals/history/${row.invoice_id}`)}><td><strong>{row.invoice_number || `Paperless #${row.paperless_document_id}`}</strong><small>revize {row.current_revision}{row.assignment_count > 1 ? ` · ${row.assignment_count} historické kroky` : ""}</small>{row.ocr_snippet && <small className="search-snippet">Nalezeno v textu dokumentu: „{row.ocr_snippet}“</small>}</td><td>{row.supplier_name || "—"}</td><td>{row.latest_assignment.cost_center.code}<small>{row.latest_assignment.cost_center.name}</small></td><td>{money(row.latest_assignment.amount, row.currency)}</td><td>{decisionLabel(row.latest_assignment)}{row.latest_assignment.invalidated && <small>⚠ Později zneplatněno změnou faktury</small>}</td><td>{formatDateTimeCs(row.latest_assignment.decision_at || row.latest_assignment.assigned_at)}</td><td>{workflowLabels[row.current_status] || row.current_status}{!row.pdf_available && <small>Originál není dostupný</small>}</td></tr>)}</tbody></table></div>}
      <div className="history-pagination"><button className="button secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Předchozí</button><span>Strana {page} z {pages} · {historyResult.total} faktur</span><button className="button secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Další</button></div>
    </>}
  </section>;
}
