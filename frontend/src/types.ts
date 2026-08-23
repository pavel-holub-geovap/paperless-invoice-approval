export type User = {
  subject: string;
  username: string;
  email?: string;
  roles: string[];
  csrf_token: string;
};

export type UserReference = Omit<User, "csrf_token">;

export type InvoiceListItem = {
  id: string;
  paperless_document_id: number;
  status: string;
  current_revision_number: number;
  title: string;
  correspondent?: string;
  paperless_created_at?: string;
  sync_status: "PENDING" | "SYNCED" | "ERROR";
  ai_status: AIStatus;
  supplier_name?: string;
  invoice_number?: string;
  total_amount?: string;
  due_date?: string;
  approvals_done: number;
  approvals_required: number;
  updated_at: string;
};

export type CostCenter = {
  id: string;
  code: string;
  name: string;
  pohoda_code: string;
  active: boolean;
};

export type Validation = {
  code: string;
  severity: "OK" | "WARNING" | "BLOCKING_ERROR";
  field_name?: string;
  message: string;
  expected?: unknown;
  actual?: unknown;
};

export type AIStatus = "AI_PENDING" | "AI_PROCESSING" | "AI_COMPLETED" | "AI_FAILED";

export type AIExtraction = {
  id: string;
  extraction_revision: number;
  invoice_revision?: number;
  model: string;
  schema_version: string;
  prompt_version: string;
  status: AIStatus;
  validation_summary?: { ok: number; warning: number; blocking_error: number };
  validation_results?: Validation[];
  parsed_result?: Record<string, unknown>;
  error_code?: string;
  error_message?: string;
  queued_at: string;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  applied: boolean;
  applied_at?: string;
  applied_by?: string;
  requires_confirmation: boolean;
};

export type Assignment = {
  id: string;
  approver_subject: string;
  required: boolean;
  decision?: string;
};

export type Allocation = {
  id: string;
  amount: string;
  percentage?: string;
  cost_center: CostCenter;
  assignments: Assignment[];
};

export type Invoice = {
  id: string;
  paperless_document_id: number;
  status: string;
  ai_status: AIStatus;
  ai: {
    latest?: AIExtraction;
    history: AIExtraction[];
  };
  current_revision_number: number;
  paperless: {
    title: string;
    created_at?: string;
    correspondent_id?: number;
    correspondent?: string;
    tag_ids: number[];
    tags: string[];
    ocr_text: string;
    original_filename?: string;
    sync_status: "PENDING" | "SYNCED" | "ERROR";
    last_synced_at?: string;
    sync_error?: string;
  };
  original_review_confirmed: boolean;
  original_reviewed_at?: string;
  original_reviewed_by?: string;
  data: Record<string, unknown>;
  extracted_fields: { field_name: string; value: unknown; source_text?: string }[];
  validations: Validation[];
  allocations: Allocation[];
};

export type ApprovalTask = {
  id: string;
  invoice_id: string;
  invoice_status: string;
  revision: number;
  supplier_name?: string;
  invoice_number?: string;
  invoice_total?: string;
  currency?: string;
  cost_center: string;
  allocation_amount: string;
  decision?: string;
  comment?: string;
  current: boolean;
};

export type ExportBatch = {
  id: string;
  batch_number: string;
  status: string;
  created_by: string;
  created_at: string;
  imported_by?: string;
  imported_at?: string;
  invoice_ids: string[];
};
