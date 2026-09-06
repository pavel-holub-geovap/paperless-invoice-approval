const labels = {
  NEW: "Nový",
  AI_PROCESSING: "AI zpracování",
  VALIDATION: "Kontrola údajů",
  QUEUE_REVIEW: "Kontrola správcem fronty",
  NEEDS_REVIEW: "Vyžaduje kontrolu",
  READY_FOR_APPROVAL: "Připraveno ke schválení",
  AWAITING_APPROVAL: "Čeká na schválení",
  RETURNED: "Vráceno k doplnění",
  REJECTED: "Zamítnuto",
  APPROVED: "Schváleno",
  XML_READY: "XML připraveno",
  READY_FOR_EXPORT: "Připraveno k exportu",
  EXPORT_CREATED: "Export vytvořen",
  IMPORTED_TO_POHODA: "Importováno do POHODY",
  UNCLASSIFIED: "Nezařazený doklad",
  RECEIVED_INVOICE: "Přijatá faktura",
  RECEIVED_ADVANCE_INVOICE: "Přijatá zálohová faktura",
  ADVANCE_PAYMENT_TAX_DOCUMENT: "Daňový doklad k přijaté platbě",
  FINAL_SETTLEMENT: "Konečné vyúčtování",
  RECEIPT: "Účtenka",
  CARD_EXPENSE: "Platba kartou",
  EMPLOYEE_EXPENSE: "Výdaj zaměstnance",
  CENTRAL_DOCUMENT: "Centrální doklad",
  OTHER_SUPPORTING_DOCUMENT: "Ostatní podklad",
  FOR_APPROVAL: "Ke schválení",
  RECORD_ONLY: "Pouze evidovat",
  CENTRAL_MANUAL: "Centrální ruční zpracování",
  ISDOC: "ISDOC",
  OCR_AI: "OCR / AI",
  MANUAL: "Ručně",
  UNDETERMINED: "Neurčeno",
  UNCHECKED: "Nezkontrolováno",
  NOT_PRESENT: "ISDOC nenalezen",
  DETECTED: "ISDOC nalezen",
  VALID: "Platný",
  INVALID: "Neplatný",
  PDF_ISDOC: "PDF s ISDOC",
  GENERATED_XML: "Generované XML",
  NONE: "Bez importu do POHODY",
  MANUAL_REVIEW: "Ruční posouzení",
  APPROVE: "Schváleno",
  RETURN: "Vráceno",
  REJECT: "Zamítnuto",
  INVALIDATED: "Zneplatněno",
  PENDING: "Čeká",
  ACTIVE: "Aktivní",
  IGNORED_DUPLICATE: "Ignorováno jako duplicita",
  IGNORED_OTHER: "Ignorováno",
  AVAILABLE: "Dostupný",
  MISSING: "Chybí",
  SYNCED: "Synchronizováno",
  ERROR: "Chyba",
  AI_PENDING: "Čeká na AI",
  AI_COMPLETED: "AI dokončeno",
  AI_FAILED: "AI selhala",
  CREATED: "Vytvořeno",
  IMPORTED: "Importováno",
  PARSED: "Načteno",
  RUNNING: "Probíhá",
  XSD_VALID: "XSD platné",
  XSD_INVALID: "XSD neplatné",
  TARGET_UNIT_VALID: "Cílová jednotka ověřena",
  TARGET_UNIT_INVALID: "Cílová jednotka neodpovídá",
  NOT_RECORDED: "Neověřeno",
  STORED: "Uloženo",
  FAILED: "Selhalo",
  HISTORICAL: "Historické",
  BLOCKING_ERROR: "Blokující chyba",
  WARNING: "Upozornění",
  OK: "OK",
  DONE: "Hotovo",
  CURRENT: "Aktuální krok",
  WAITING: "Čeká",
  BLOCKED: "Blokováno",
  SUBMITTING: "Odesílání",
  PAPERLESS_PROCESSING: "Zpracování v Paperless",
  WAITING_OCR: "Čeká na OCR",
  OCR_COMPLETE: "OCR dokončeno",
  FAILED_RETRYABLE: "Dočasně selhalo",
  SUBMISSION_UNKNOWN: "Výsledek odeslání není známý",
  READY_FOR_REVIEW: "Připraveno ke kontrole",
  PAPERLESS_SYNC: "Synchronizace z Paperless",
  QUEUE_MANAGER: "Správce fronty",
  APPROVER: "Schvalovatel",
} as const;

export type DisplayCode = keyof typeof labels;

export function displayLabel(value: string | null | undefined): string {
  if (!value) return "Neurčeno";
  const counted = value.match(/^(\d+)\s+(BLOCKING_ERROR|WARNING)$/);
  if (counted) {
    return counted[2] === "BLOCKING_ERROR"
      ? `${counted[1]} blokující chyby`
      : `${counted[1]} upozornění`;
  }
  return labels[value as DisplayCode] ?? "Neznámý stav";
}

export const workflowStatusLabel = displayLabel;
export const documentTypeLabel = displayLabel;
export const processingModeLabel = displayLabel;
export const extractionSourceLabel = displayLabel;
export const isdocStatusLabel = displayLabel;
export const pohodaImportMethodLabel = displayLabel;
export const decisionLabel = displayLabel;
export const validationSeverityLabel = displayLabel;

export const documentTypeOptions = [
  "UNCLASSIFIED",
  "RECEIVED_INVOICE",
  "RECEIVED_ADVANCE_INVOICE",
  "ADVANCE_PAYMENT_TAX_DOCUMENT",
  "FINAL_SETTLEMENT",
  "RECEIPT",
  "CARD_EXPENSE",
  "EMPLOYEE_EXPENSE",
  "CENTRAL_DOCUMENT",
  "OTHER_SUPPORTING_DOCUMENT",
] as const;

export const processingModeOptions = [
  "FOR_APPROVAL",
  "RECORD_ONLY",
  "CENTRAL_MANUAL",
] as const;
