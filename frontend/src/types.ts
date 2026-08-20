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
  current_revision_number: number;
  original_checked_at?: string;
  original_checked_by?: string;
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
