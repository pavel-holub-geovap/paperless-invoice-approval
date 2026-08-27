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
  disposition: "ACTIVE" | "IGNORED_DUPLICATE" | "IGNORED_OTHER";
  source_status: "AVAILABLE" | "MISSING";
  source_missing_at?: string;
  current_revision_number: number;
  title: string;
  correspondent?: string;
  paperless_created_at?: string;
  approval_created_at?: string;
  uploaded_by?: string;
  source_pdf_sha256?: string;
  sync_status: "PENDING" | "SYNCED" | "ERROR";
  ai_status: AIStatus;
  supplier_name?: string;
  invoice_number?: string;
  total_amount?: string;
  due_date?: string;
  approvals_done: number;
  approvals_required: number;
  warning_count: number;
  blocking_error_count: number;
  updated_at: string;
};

export type CostCenter = {
  id: string;
  code: string;
  name: string;
  pohoda_code: string;
  active: boolean;
  created_at?: string;
  updated_at?: string;
};

export type Validation = {
  code: string;
  severity: "OK" | "WARNING" | "BLOCKING_ERROR";
  field_name?: string;
  message: string;
  expected?: unknown;
  actual?: unknown;
  details?: Record<string, unknown>;
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
  candidate_data?: Record<string, unknown>;
  error_code?: string;
  error_message?: string;
  schema_validation_errors?: Array<{
    stage: string;
    attempt: number;
    path: string;
    type: string;
    message: string;
    expected: string;
    actual: unknown;
    actual_type: string;
  }>;
  normalization_result?: {
    raw_schema_version?: string;
    canonical_schema_version?: string;
    changes?: Array<{ path: string; raw: unknown; normalized: unknown; code?: string; reason?: string }>;
    rejections?: Array<{
      path: string;
      code: string;
      raw: unknown;
      normalized: unknown;
      source_text?: string;
      reason: string;
    }>;
  };
  corrective_retry_count?: number;
  raw_response_preserved?: boolean;
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
  status: string;
  assigned_by: string;
  assigned_at: string;
  decided_at?: string;
  comment?: string;
  decision?: string;
};

export type Allocation = {
  id: string;
  amount: string;
  percentage?: string;
  note?: string;
  vat_breakdown: { rate: string; base: string; vat: string }[];
  created_by: string;
  cost_center: CostCenter;
  assignments: Assignment[];
};

export type Invoice = {
  id: string;
  paperless_document_id: number;
  status: string;
  disposition: {
    status: "ACTIVE" | "IGNORED_DUPLICATE" | "IGNORED_OTHER";
    reason?: string;
    comment?: string;
    actor?: string;
    changed_at?: string;
    duplicate_of_invoice_id?: string;
  };
  source: { status: "AVAILABLE" | "MISSING"; missing_at?: string };
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
    source_pdf_sha256?: string;
    uploaded_by?: string;
  };
  original_review_confirmed: boolean;
  original_reviewed_at?: string;
  original_reviewed_by?: string;
  data: Record<string, unknown>;
  extracted_fields: { field_name: string; value: unknown; source_text?: string }[];
  validations: Validation[];
  allocations: Allocation[];
  allocation_summary: {
    invoice_total: string;
    allocated: string;
    remaining: string;
  };
  pohoda_export?: PohodaExport;
  created_at?: string;
  updated_at?: string;
};

export type PohodaExport = {
  id: string;
  status: "XSD_VALID" | "XSD_INVALID";
  source_export_id?: string;
  generator_version: string;
  xsd_bundle_version: string;
  encoding: string;
  xml_sha256: string;
  xml_size: number;
  generated_by: string;
  generated_at: string;
  validation_errors: { line?: number; column?: number; message: string; path?: string }[];
  imported_by?: string;
  imported_at?: string;
  pohoda_target_ico?: string;
  pohoda_target_key_configured?: boolean;
  pohoda_target_validation?: {
    status: "TARGET_UNIT_VALID" | "TARGET_UNIT_INVALID" | "NOT_RECORDED";
    actual_ico?: string;
    errors: string[];
  };
};

export type PohodaConfig = {
  pohoda_target_ico?: string;
  pohoda_target_key_configured: boolean;
  identification: "ICO_ONLY" | "ICO_AND_KEY" | "NOT_CONFIGURED";
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
  allocation_percentage?: string;
  allocation_note?: string;
  invoice_data: Record<string, unknown>;
  assignment_status: string;
  decision?: string;
  comment?: string;
  current: boolean;
};

export type AuditEvent = {
  id: string;
  timestamp: string;
  actor: string;
  revision?: number;
  event_type: string;
  old_state?: string;
  new_state?: string;
  old_value?: unknown;
  new_value?: unknown;
  comment?: string;
  metadata: Record<string, unknown>;
};

export type UploadConfig = {
  max_file_size: number;
  supported_mime_types: string[];
  supported_extensions: string[];
  multi_upload: boolean;
};

export type UploadTracking = {
  id: string;
  idempotency_key: string;
  filename: string;
  file_size: number;
  mime_type: string;
  sha256: string;
  status: string;
  tracking_status: string;
  paperless_task_id?: string;
  paperless_document_id?: number;
  invoice_id?: string;
  ai_status?: AIStatus;
  workflow_status?: string;
  uploaded_by: string;
  source_created_at?: string;
  approval_created_at?: string;
  error_code?: string;
  error_message?: string;
  retryable: boolean;
  retry_count: number;
  exact_duplicate_invoice_id?: string;
  created_at: string;
  updated_at: string;
};

export type ExportBatch = {
  id: string;
  batch_number: string;
  status: string;
  created_by: string;
  created_at: string;
  imported_by?: string;
  imported_at?: string;
  archive_sha256?: string;
  invoice_ids: string[];
  items: {
    invoice_id: string;
    revision_id: string;
    export_artifact_id: string;
    pdf_filename: string;
    xml_filename: string;
    imported_at?: string;
  }[];
};
