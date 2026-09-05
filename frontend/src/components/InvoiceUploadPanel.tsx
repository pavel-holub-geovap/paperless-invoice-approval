import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { UploadConfig, UploadTracking, User } from "../types";

type LocalUpload = UploadTracking & { localId: string; file?: File };

export type InvoiceUploadPanelHandle = { openFilePicker: () => void };

type Props = {
  user: User;
  onQueueChanged: (invoiceId?: string) => boolean | Promise<boolean>;
};

const statusLabels: Record<string, string> = {
  SELECTED: "Připraveno k nahrání",
  UPLOADING: "Nahrávám…",
  PAPERLESS_PROCESSING: "Dokument byl předán do Paperless. Čeká na zpracování.",
  WAITING_OCR: "Dokument byl uložen do Paperless. OCR probíhá.",
  OCR_COMPLETE: "OCR dokončeno",
  AI_PROCESSING: "AI zpracování",
  READY_FOR_REVIEW: "Faktura je připravena ke kontrole.",
  INVALID: "Nepodporovaný soubor",
  FAILED_RETRYABLE: "Fakturu se nepodařilo nahrát",
  FAILED: "Fakturu se nepodařilo nahrát",
  SUBMISSION_UNKNOWN: "Výsledek předání do Paperless není známý",
  ERROR: "Chyba zpracování",
};

const errorFallbacks: Record<string, string> = {
  PAPERLESS_UNAVAILABLE: "Paperless je momentálně nedostupný.",
  PAPERLESS_AUTH_ERROR: "Paperless odmítl přístup integračního účtu.",
  PAPERLESS_VALIDATION_ERROR: "Paperless dokument odmítl jako neplatný.",
  PAPERLESS_SUBMISSION_UNKNOWN: "Spojení se přerušilo před potvrzením výsledku. Upload se automaticky neopakuje.",
  UNSUPPORTED_FILE_TYPE: "Podporovány jsou pouze PDF soubory.",
  FILE_TOO_LARGE: "Soubor překračuje povolenou velikost.",
  INTERNAL_ERROR: "Při nahrávání nastala interní chyba.",
};

const failedStatuses = new Set(["INVALID", "FAILED_RETRYABLE", "FAILED", "SUBMISSION_UNKNOWN", "ERROR"]);
const pollingFinished = new Set(["READY_FOR_REVIEW", ...failedStatuses]);

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `upload-${Date.now()}-${Math.random()}`;
}

function localFromServer(row: UploadTracking, previous?: LocalUpload): LocalUpload {
  return { ...row, localId: previous?.localId || row.id, file: previous?.file };
}

export const InvoiceUploadPanel = forwardRef<InvoiceUploadPanelHandle, Props>(function InvoiceUploadPanel(
  { user, onQueueChanged },
  ref,
) {
  const [config, setConfig] = useState<UploadConfig>({
    max_file_size: 8 * 1024 * 1024,
    supported_mime_types: ["application/pdf"],
    supported_extensions: [".pdf"],
    multi_upload: true,
  });
  const [items, setItems] = useState<LocalUpload[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const canUpload = user.roles.some((role) => role === "QUEUE_MANAGER" || role === "APPROVER");

  useImperativeHandle(ref, () => ({ openFilePicker: () => inputRef.current?.click() }), []);

  useEffect(() => {
    if (!canUpload) return;
    void api<UploadConfig>("/uploads/config").then(setConfig).catch(() => undefined);
  }, [canUpload]);

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
  const remove = useCallback((localId: string) => {
    setItems((current) => current.filter((row) => row.localId !== localId));
  }, []);

  const applyServerState = useCallback(async (localId: string, result: UploadTracking) => {
    setItems((current) => current.map((row) => row.localId === localId ? localFromServer(result, row) : row));
    const invoiceVisible = await Promise.resolve(onQueueChanged(result.invoice_id)).catch(() => false);
    if (result.invoice_id && invoiceVisible) remove(localId);
  }, [onQueueChanged, remove]);

  const send = useCallback(async (item: LocalUpload) => {
    if (!item.file) return;
    update(item.localId, { status: "UPLOADING", error_message: undefined, retryable: false });
    const body = new FormData();
    body.append("document", item.file, item.filename);
    body.append("idempotency_key", item.idempotency_key);
    try {
      const result = await api<UploadTracking>("/uploads", { method: "POST", body });
      await applyServerState(item.localId, result);
    } catch (error) {
      const detail = error instanceof ApiError && typeof error.detail === "object" && error.detail
        ? error.detail as { code?: string; message?: string; retryable?: boolean }
        : {};
      update(item.localId, {
        status: detail.retryable ? "FAILED_RETRYABLE" : "FAILED",
        error_code: detail.code || "INTERNAL_ERROR",
        error_message: detail.message || errorFallbacks[detail.code || "INTERNAL_ERROR"] || (error as Error).message,
        retryable: Boolean(detail.retryable),
      });
    }
  }, [applyServerState, update]);

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
          ? errorFallbacks.UNSUPPORTED_FILE_TYPE
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
    if (!canUpload) return;
    const pending = items.filter((row) => row.id
      && !pollingFinished.has(row.status)
      && row.status !== "SELECTED"
      && row.status !== "UPLOADING");
    if (!pending.length) return;
    const timer = window.setInterval(() => {
      pending.forEach((row) => {
        void api<UploadTracking>(`/uploads/${encodeURIComponent(row.id)}`)
          .then((result) => applyServerState(row.localId, result))
          .catch(() => undefined);
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [applyServerState, canUpload, items]);

  if (!canUpload) return null;
  return <div className="invoice-upload-area">
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
      <span className="upload-icon" aria-hidden="true">↑</span>
      <strong>{dragging ? "Pusťte fakturu sem" : "Přetáhněte fakturu sem nebo"}</strong>
      {!dragging && <button className="button-link" onClick={() => inputRef.current?.click()}>Vybrat soubor</button>}
    </div>
    {items.length > 0 && <section className="upload-feedback" aria-live="polite" aria-label="Dočasný stav nahrávání">
      <strong>{items.length === 1 ? "Zpracování faktury" : `Nahrávání ${items.length} souborů`}</strong>
      {items.map((item) => {
        const failed = failedStatuses.has(item.status);
        const detail = item.error_message || errorFallbacks[item.error_code || ""];
        return <div className={`upload-feedback-item${failed ? " upload-feedback-error" : ""}`} key={item.localId}>
          <span className="upload-state-icon" aria-hidden="true">{item.status === "READY_FOR_REVIEW" ? "✓" : failed ? "✕" : "●"}</span>
          <span><strong>{item.filename}</strong><small>{statusLabels[item.status] || item.status}{detail ? ` · Důvod: ${detail}` : ""}</small></span>
          {item.exact_duplicate_invoice_id && <small className="duplicate-warning">Stejný PDF obsah již existuje.</small>}
          {item.retryable && item.file && <button className="button secondary compact" onClick={() => void send(item)}>Zkusit znovu</button>}
          {failed && <button className="icon-button upload-dismiss" aria-label={`Zavřít stav uploadu ${item.filename}`} onClick={() => remove(item.localId)}>×</button>}
        </div>;
      })}
    </section>}
  </div>;
});
