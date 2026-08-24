import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  disposition: { status: "ACTIVE" },
  source: { status: "AVAILABLE" },
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

  it("shows a missing-source warning and does not request the PDF surface", () => {
    mockEmptyApi();
    render(
      <InvoiceDetail
        invoice={{ ...invoice, source: { status: "MISSING", missing_at: "2026-08-23T20:00:00Z" } }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );
    expect(screen.getByText(/Zdrojový dokument v Paperless chybí/)).toBeVisible();
    expect(screen.queryByTitle("Originální faktura")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Předat ke schválení" })).toBeDisabled();
  });

  it("preserves unsaved form data when polling returns a newer revision", async () => {
    mockEmptyApi();
    const first = { ...invoice, data: { supplier_name: "Původní" } };
    const view = render(
      <InvoiceDetail invoice={first} user={user} onBack={() => undefined} onRefresh={() => undefined} />,
    );
    const supplier = screen.getByLabelText("Dodavatel");
    fireEvent.change(supplier, { target: { value: "Rozepsaná lokální hodnota" } });

    view.rerender(
      <InvoiceDetail
        invoice={{ ...first, current_revision_number: 2, data: { supplier_name: "Nová hodnota ze serveru" } }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );

    expect(await screen.findByText(/Na serveru je novější revize/)).toBeVisible();
    expect(screen.getByLabelText("Dodavatel")).toHaveValue("Rozepsaná lokální hodnota");
  });

  it("shows field and allocation errors next to their sections", async () => {
    mockEmptyApi();
    render(
      <InvoiceDetail
        invoice={{
          ...invoice,
          validations: [
            { code: "DOMESTIC_ACCOUNT_INCOMPLETE", severity: "WARNING", field_name: "bank_code", message: "Kód banky je povinný." },
            { code: "ALLOCATION_TOTAL_MISMATCH", severity: "BLOCKING_ERROR", message: "Součet rozúčtování nesedí.", expected: "1210", actual: "700" },
          ],
        }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );
    expect(screen.getAllByText("Kód banky je povinný.")[0]).toBeVisible();
    expect(screen.getByRole("textbox", { name: /^Kód banky/ })).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Rozúčtování:").parentElement).toHaveTextContent("Součet rozúčtování nesedí.");
    await act(async () => undefined);
  });

  it("prevents double save and focuses the first returned field error", async () => {
    let finishPatch!: (value: unknown) => void;
    const patchResponse = new Promise((resolve) => { finishPatch = resolve; });
    const fetchMock = vi.fn((_: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") return patchResponse;
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    Element.prototype.scrollIntoView = vi.fn();
    render(<InvoiceDetail invoice={invoice} user={user} onBack={() => undefined} onRefresh={() => undefined} />);

    const save = screen.getByRole("button", { name: "Uložit změny" });
    fireEvent.click(save);
    expect(await screen.findByRole("button", { name: "Ukládám…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Ukládám…" }));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(1);

    await act(async () => finishPatch({
      ok: true,
      status: 200,
      json: async () => ({ ...invoice, validations: [{ code: "BANK_CODE", severity: "BLOCKING_ERROR", field_name: "bank_code", message: "Doplňte kód banky." }] }),
    }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: /^Kód banky/ })).toHaveFocus());
    expect(screen.getByText("Doplňte kód banky.")).toBeVisible();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("prevents a double approval while the first decision is pending", async () => {
    let finishDecision!: (value: unknown) => void;
    const decisionResponse = new Promise((resolve) => { finishDecision = resolve; });
    const task = {
      id: "task-1", invoice_id: "invoice-1", invoice_status: "AWAITING_APPROVAL", revision: 2,
      supplier_name: "Dodavatel", invoice_number: "F-1", invoice_total: "1210", currency: "CZK",
      cost_center: "200", allocation_amount: "700", invoice_data: {}, assignment_status: "PENDING", current: true,
    };
    const fetchMock = vi.fn((_: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return decisionResponse;
      return Promise.resolve({ ok: true, status: 200, json: async () => [task] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Approvals />);
    const approve = await screen.findByRole("button", { name: "Schválit" });
    fireEvent.click(approve);
    expect(await screen.findByRole("button", { name: "Schvaluji…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Schvaluji…" }));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    await act(async () => finishDecision({ ok: true, status: 200, json: async () => ({}) }));
    expect(await screen.findByText("Úkol byl schválen.")).toBeVisible();
  });
});
