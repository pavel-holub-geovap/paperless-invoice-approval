import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { UploadConfig, UploadTracking, User } from "../types";

type LocalUpload = UploadTracking & {
  localId: string;
  file?: File;
};

const statusLabels: Record<string, string> = {
  SELECTED: "Připraveno k nahrání",
  UPLOADING: "Nahrávám…",
  PAPERLESS_PROCESSING: "Nahráno · čeká na Paperless",
  WAITING_OCR: "Čeká na OCR",
  OCR_COMPLETE: "OCR dokončeno",
  AI_PROCESSING: "AI zpracování",
  READY_FOR_REVIEW: "Připraveno ke kontrole",
  INVALID: "Nepodporovaný soubor",
  FAILED_RETRYABLE: "Nahrání se nezdařilo",
  FAILED: "Nahrání se nezdařilo",
  SUBMISSION_UNKNOWN: "Výsledek předání není známý",
  ERROR: "Chyba zpracování",
};

const terminal = new Set([
  "READY_FOR_REVIEW",
  "FAILED_RETRYABLE",
  "FAILED",
  "SUBMISSION_UNKNOWN",
  "ERROR",
]);

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `upload-${Date.now()}-${Math.random()}`;
}

function localFromServer(row: UploadTracking, previous?: LocalUpload): LocalUpload {
  return { ...row, localId: previous?.localId || row.id, file: previous?.file };
}

export function InvoiceUploadPanel({
  user,
  onQueueChanged,
  onOpenInvoice,
}: {
  user: User;
  onQueueChanged: () => void;
  onOpenInvoice: (invoiceId: string) => void;
}) {
  const [config, setConfig] = useState<UploadConfig>({
    max_file_size: 8 * 1024 * 1024,
    supported_mime_types: ["application/pdf"],
    supported_extensions: [".pdf"],
    multi_upload: true,
  });
  const [items, setItems] = useState<LocalUpload[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const isManager = user.roles.includes("QUEUE_MANAGER");

  useEffect(() => {
    if (!isManager) return;
    void Promise.all([api<UploadConfig>("/uploads/config"), api<UploadTracking[]>("/uploads?limit=10")])
      .then(([nextConfig, recent]) => {
        setConfig(nextConfig);
        setItems((current) => {
          const local = current.filter((row) => row.file);
          const localIds = new Set(local.map((row) => row.id));
          return [
            ...local,
            ...recent.filter((row) => !localIds.has(row.id)).map((row) => localFromServer(row)),
          ];
        });
      })
      .catch(() => undefined);
  }, [isManager]);

  useEffect(() => {
    const preventDocumentOpen = (event: DragEvent) => event.preventDefault();
    window.addEventListener("dragover", preventDocumentOpen);
    window.addEventListener("drop", preventDocumentOpen);
    return () => {
      window.removeEventListener("dragover", preventDocumentOpen);
      window.removeEventListener("drop", preventDocumentOpen);
    };
  }, []);

  const update = useCallback((localId: string, changes: Partial<LocalUpload>) => {
    setItems((current) => current.map((row) => row.localId === localId ? { ...row, ...changes } : row));
  }, []);

  const send = useCallback(async (item: LocalUpload) => {
    if (!item.file) return;
    update(item.localId, { status: "UPLOADING", error_message: undefined, retryable: false });
    const body = new FormData();
    body.append("document", item.file, item.filename);
    body.append("idempotency_key", item.idempotency_key);
    try {
      const result = await api<UploadTracking>("/uploads", { method: "POST", body });
      setItems((current) => current.map((row) => row.localId === item.localId
        ? localFromServer(result, row)
        : row));
      onQueueChanged();
    } catch (error) {
      const detail = error instanceof ApiError && typeof error.detail === "object" && error.detail
        ? error.detail as { code?: string; message?: string; retryable?: boolean }
        : {};
      update(item.localId, {
        status: detail.retryable ? "FAILED_RETRYABLE" : "FAILED",
        error_code: detail.code,
        error_message: detail.message || (error as Error).message,
        retryable: Boolean(detail.retryable),
      });
    }
  }, [onQueueChanged, update]);

  const acceptFiles = useCallback((files: File[]) => {
    const additions = files.map<LocalUpload>((file) => {
      const pdf = file.name.toLowerCase().endsWith(".pdf")
        && (!file.type || ["application/pdf", "application/x-pdf"].includes(file.type));
      const tooLarge = file.size > config.max_file_size;
      const idempotencyKey = newKey();
      return {
        id: idempotencyKey,
        localId: idempotencyKey,
        idempotency_key: idempotencyKey,
        filename: file.name,
        file_size: file.size,
        mime_type: file.type || "application/pdf",
        sha256: "",
        status: pdf && !tooLarge ? "SELECTED" : "INVALID",
        tracking_status: pdf && !tooLarge ? "SUBMITTING" : "FAILED",
        retryable: false,
        retry_count: 0,
        error_code: !pdf ? "UNSUPPORTED_FILE_TYPE" : tooLarge ? "FILE_TOO_LARGE" : undefined,
        error_message: !pdf
          ? "Podporovány jsou pouze PDF soubory."
          : tooLarge
            ? `Soubor překračuje limit ${Math.floor(config.max_file_size / 1024 / 1024)} MiB.`
            : undefined,
        uploaded_by: user.username,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        file,
      };
    });
    setItems((current) => [...additions, ...current]);
    additions.filter((row) => row.status === "SELECTED").forEach((row) => void send(row));
  }, [config.max_file_size, send, user.username]);

  useEffect(() => {
    if (!isManager) return;
    const pending = items.filter((row) => row.id && !terminal.has(row.status) && row.status !== "INVALID" && row.status !== "SELECTED" && row.status !== "UPLOADING");
    if (!pending.length) return;
    const timer = window.setInterval(() => {
      pending.forEach((row) => {
        void api<UploadTracking>(`/uploads/${encodeURIComponent(row.id)}`).then((result) => {
          setItems((current) => current.map((candidate) => candidate.localId === row.localId
            ? localFromServer(result, candidate)
            : candidate));
          onQueueChanged();
        }).catch(() => undefined);
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [isManager, items, onQueueChanged]);

  if (!isManager) return null;
  return <div className="invoice-upload-area">
    <div className="upload-toolbar">
      <button className="button primary" onClick={() => inputRef.current?.click()}>+ Nahrát fakturu</button>
    </div>
    <input
      ref={inputRef}
      className="visually-hidden"
      aria-label="Vybrat PDF faktury"
      type="file"
      accept="application/pdf,.pdf"
      multiple
      onChange={(event) => {
        acceptFiles(Array.from(event.target.files || []));
        event.target.value = "";
      }}
    />
    <div
      className={`invoice-drop-zone${dragging ? " dragging" : ""}`}
      onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
      onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
      onDragLeave={(event) => { event.preventDefault(); setDragging(false); }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        acceptFiles(Array.from(event.dataTransfer.files));
      }}
    >
      <span className="upload-icon">↑</span>
      <strong>{dragging ? "Pusťte fakturu sem" : "Přetáhněte fakturu sem nebo"}</strong>
      {!dragging && <button className="button-link" onClick={() => inputRef.current?.click()}>Vybrat soubor</button>}
    </div>
    {items.length > 0 && <div className="upload-list" aria-live="polite">
      <strong>{items.length} {items.length === 1 ? "soubor" : "soubory"}</strong>
      {items.map((item) => <div className={`upload-row status-${item.status.toLowerCase()}`} key={item.localId}>
        <span className="upload-state-icon" aria-hidden="true">{item.status === "READY_FOR_REVIEW" ? "✓" : terminal.has(item.status) ? "✕" : "●"}</span>
        <span><strong>{item.filename}</strong><small>{statusLabels[item.status] || item.status}{item.error_message ? ` · ${item.error_message}` : ""}</small></span>
        {item.exact_duplicate_invoice_id && <small className="duplicate-warning">Stejný PDF obsah již existuje.</small>}
        {item.retryable && item.file && <button className="button secondary compact" onClick={() => void send(item)}>Zkusit znovu</button>}
        {item.invoice_id && <button className="button-link" onClick={() => onOpenInvoice(item.invoice_id!)}>Otevřít</button>}
      </div>)}
    </div>}
  </div>;
}
