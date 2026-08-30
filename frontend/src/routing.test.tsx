import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { parseRoute } from "./routing";
import type { Invoice, InvoiceListItem, User } from "./types";

const user: User = { subject: "manager", username: "queue-manager", roles: ["QUEUE_MANAGER"], csrf_token: "csrf" };
const invoice: Invoice = {
  id: "invoice-route", paperless_document_id: 42, status: "QUEUE_REVIEW",
  classification: { document_type: "RECEIVED_INVOICE", processing_mode: "FOR_APPROVAL", extraction_source: "OCR_AI", pohoda_eligible: true, pohoda_import_method: "GENERATED_XML" },
  isdoc: { has_embedded_isdoc: false, status: "NOT_PRESENT" },
  disposition: { status: "ACTIVE" }, source: { status: "AVAILABLE" },
  ai_status: "AI_COMPLETED", ai: { history: [] }, current_revision_number: 1,
  paperless: { title: "Route invoice", tag_ids: [], tags: [], ocr_text: "OCR", sync_status: "SYNCED" },
  original_review_confirmed: false, data: { invoice_number: "ROUTE-42" }, extracted_fields: [], validations: [], allocations: [],
  allocation_summary: { invoice_total: "0", allocated: "0", remaining: "0" },
};
const row: InvoiceListItem = {
  id: invoice.id, paperless_document_id: 42, status: "QUEUE_REVIEW", disposition: "ACTIVE", source_status: "AVAILABLE",
  current_revision_number: 1, title: "Route invoice", sync_status: "SYNCED", ai_status: "AI_COMPLETED",
  document_type: "RECEIVED_INVOICE", processing_mode: "FOR_APPROVAL", extraction_source: "OCR_AI", isdoc_status: "NOT_PRESENT", pohoda_import_method: "GENERATED_XML",
  invoice_number: "ROUTE-42", approvals_done: 0, approvals_required: 0, warning_count: 0, blocking_error_count: 0,
  updated_at: "2026-08-23T00:00:00Z",
};

function response(value: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => value });
}

function mockApi() {
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    const path = String(input);
    if (path === "/api/auth/me") return response(user);
    if (path === "/api/invoices/invoice-route") return response(invoice);
    if (path.startsWith("/api/invoices?")) return response([row]);
    return response([]);
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("client-side routing", () => {
  it("parses direct invoice deep links", () => {
    expect(parseRoute("/invoices/invoice%20id")).toEqual({ page: "dashboard", invoiceId: "invoice id" });
    expect(parseRoute("/approvals/history/invoice%20id")).toEqual({ page: "approvals", history: true, historyInvoiceId: "invoice id" });
  });

  it("opens a deep-linked detail and Fronta navigates to the real queue URL", async () => {
    mockApi();
    window.history.replaceState({}, "", "/invoices/invoice-route");
    render(<App/>);
    expect(await screen.findByRole("heading", { name: "ROUTE-42" })).toBeVisible();
    fireEvent.click(screen.getByRole("link", { name: "Fronta" }));
    expect(window.location.pathname).toBe("/");
    expect(await screen.findByRole("heading", { name: "Přijaté faktury" })).toBeVisible();
  });

  it("responds to browser back/popstate from detail to queue", async () => {
    mockApi();
    window.history.replaceState({}, "", "/invoices/invoice-route");
    render(<App/>);
    expect(await screen.findByRole("heading", { name: "ROUTE-42" })).toBeVisible();
    act(() => {
      window.history.replaceState({}, "", "/");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByRole("heading", { name: "Přijaté faktury" })).toBeVisible();
  });
});
