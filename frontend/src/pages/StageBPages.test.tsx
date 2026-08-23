import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Approvals } from "./Approvals";
import { InvoiceDetail } from "./InvoiceDetail";
import type { Invoice, User } from "../types";

const user: User = {
  subject: "queue-manager-subject",
  username: "queue-manager",
  roles: ["QUEUE_MANAGER"],
  csrf_token: "test-csrf",
};

const invoice: Invoice = {
  id: "invoice-1",
  paperless_document_id: 1,
  status: "QUEUE_REVIEW",
  ai_status: "AI_COMPLETED",
  ai: { history: [] },
  current_revision_number: 1,
  paperless: {
    title: "Synthetic Invoice CS-EN",
    created_at: "2026-08-23T15:42:57Z",
    tag_ids: [1],
    tags: ["Přijatá faktura"],
    ocr_text: "Synthetic OCR text",
    original_filename: "synthetic-invoice-cs-en.pdf",
    sync_status: "SYNCED",
    last_synced_at: "2026-08-23T16:36:36Z",
  },
  data: {},
  extracted_fields: [],
  validations: [],
  allocations: [],
  allocation_summary: { invoice_total: "0.00", allocated: "0.00", remaining: "0.00" },
  original_review_confirmed: false,
};

function mockEmptyApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Stage B pages", () => {
  it("renders Paperless metadata, OCR, and both original PDF surfaces", () => {
    mockEmptyApi();

    render(
      <InvoiceDetail
        invoice={invoice}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );

    const expectedPdfUrl = "/api/invoices/invoice-1/pdf";
    expect(screen.getByTitle("Originální faktura")).toHaveAttribute("src", expectedPdfUrl);
    expect(screen.getByRole("link", { name: "Otevřít PDF v novém okně" })).toHaveAttribute(
      "href",
      expectedPdfUrl,
    );
    expect(screen.getByText("Synthetic OCR text")).toBeVisible();
    expect(screen.getByText("synthetic-invoice-cs-en.pdf")).toBeVisible();
  });

  it("renders the empty approver task section", async () => {
    mockEmptyApi();

    render(<Approvals />);

    expect(await screen.findByText("Momentálně nemáte žádný aktivní úkol ke schválení.")).toBeVisible();
  });
});
