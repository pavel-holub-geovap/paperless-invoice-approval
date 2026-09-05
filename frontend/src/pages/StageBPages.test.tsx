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
  classification: { document_type: "RECEIVED_INVOICE", processing_mode: "FOR_APPROVAL", extraction_source: "OCR_AI", pohoda_eligible: true, pohoda_import_method: "GENERATED_XML" },
  isdoc: { has_embedded_isdoc: false, status: "NOT_PRESENT" },
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
    expect(screen.getByRole("button", { name: "Schválit kontrolu a předat" })).toBeDisabled();
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
        invoice={{ ...first, data: { supplier_name: "Nová hodnota ze serveru" } }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );

    expect(await screen.findByText(/Na serveru je novější revize/)).toBeVisible();
    expect(screen.getByLabelText("Dodavatel")).toHaveValue("Rozepsaná lokální hodnota");
  });

  it("hydrates empty inputs when first AI extraction populates the same revision", async () => {
    mockEmptyApi();
    const processing = { ...invoice, ai_status: "AI_PROCESSING" as const, data: {} };
    const view = render(
      <InvoiceDetail invoice={processing} user={user} onBack={() => undefined} onRefresh={() => undefined} />,
    );
    expect(screen.getByLabelText("Dodavatel")).toHaveValue("");
    expect(screen.getByLabelText("IČO")).toHaveValue("");

    view.rerender(
      <InvoiceDetail
        invoice={{
          ...processing,
          ai_status: "AI_COMPLETED",
          data: { supplier_name: "GIRITON Systems s.r.o.", supplier_ico: "28652240" },
          extracted_fields: [
            { field_name: "supplier_name", value: "GIRITON Systems s.r.o.", source_text: "DODAVATEL GIRITON Systems s.r.o." },
            { field_name: "supplier_ico", value: "28652240", source_text: "IČO 28652240" },
          ],
        }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );

    await waitFor(() => expect(screen.getByRole("textbox", { name: /^Dodavatel/ })).toHaveValue("GIRITON Systems s.r.o."));
    expect(screen.getByRole("textbox", { name: /^IČO/ })).toHaveValue("28652240");
    expect(screen.getByText("AI zdroj: DODAVATEL GIRITON Systems s.r.o.")).toBeVisible();
  });

  it("shows precise manager diagnostics for a failed raw schema value", async () => {
    mockEmptyApi();
    render(
      <InvoiceDetail
        invoice={{
          ...invoice,
          ai_status: "AI_FAILED",
          ai: {
            history: [],
            latest: {
              id: "extraction-1",
              extraction_revision: 1,
              model: "qwen3:8b",
              schema_version: "invoice-extraction.v3",
              prompt_version: "invoice-extraction.cs-en.v5",
              status: "AI_FAILED",
              error_code: "SCHEMA_VALIDATION_FAILED",
              error_message: "AI vrátila hodnotu v neočekávaném formátu.",
              schema_validation_errors: [{
                stage: "canonical_schema",
                attempt: 2,
                path: "vat_lines.0.vat_rate",
                type: "decimal_parsing",
                message: "Input should be a valid decimal",
                expected: "decimal",
                actual: "21%",
                actual_type: "str",
              }],
              corrective_retry_count: 1,
              raw_response_preserved: true,
              queued_at: "2026-08-25T13:00:00Z",
              applied: false,
              requires_confirmation: false,
            },
          },
        }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );

    expect(await screen.findByText(/AI vrátila hodnotu v neočekávaném formátu:/)).toBeVisible();
    expect(screen.getByText("DPH sazba").closest("li")).toHaveTextContent('"21%"');
    expect(screen.getByText(/Raw odpověď zachována: ano/)).toHaveTextContent("opravný retry: 1");
  });

  it("shows GMtech dates in Czech format and submits ISO values", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <InvoiceDetail
        invoice={{
          ...invoice,
          data: {
            issue_date: "2026-07-08",
            taxable_supply_date: "2026-06-30",
            due_date: "2026-08-07",
          },
          extracted_fields: [{
            field_name: "taxable_supply_date",
            value: "2026-06-30",
            source_text: "Datum zd. plnění: 30.06.2026",
          }],
        }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );

    expect(screen.getByRole("textbox", { name: /^Datum vystavení/ })).toHaveValue("08.07.2026");
    expect(screen.getByRole("textbox", { name: /^DUZP/ })).toHaveValue("30.06.2026");
    expect(screen.getByRole("textbox", { name: /^Splatnost/ })).toHaveValue("07.08.2026");
    expect(screen.getByText("AI zdroj: Datum zd. plnění: 30.06.2026")).toBeVisible();

    fireEvent.change(screen.getByRole("textbox", { name: /^DUZP/ }), {
      target: { value: "01.07.2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Uložit změny" }));
    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
      expect(patchCall).toBeDefined();
      const body = JSON.parse(String(patchCall?.[1]?.body));
      expect(body.changes.taxable_supply_date).toBe("2026-07-01");
      expect(body.changes.issue_date).toBe("2026-07-08");
      expect(body.changes.due_date).toBe("2026-08-07");
    });
  });

  it("shows an inline error and does not submit an impossible Czech date", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <InvoiceDetail
        invoice={{ ...invoice, data: { taxable_supply_date: "2026-06-30" } }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: /^DUZP/ }), {
      target: { value: "31.02.2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Uložit změny" }));

    expect(await screen.findByText("Zadané datum neexistuje.")).toBeVisible();
    expect(screen.getByRole("textbox", { name: /^DUZP/ })).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
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

  it("shows VAT reconciliation and rounding as a section warning", async () => {
    mockEmptyApi();
    render(
      <InvoiceDetail
        invoice={{
          ...invoice,
          data: {
            currency: "CZK", total_without_vat: "4065.29", total_vat: "853.71", total_amount: "4919.00",
            vat_lines: [
              { vat_rate: "21", taxable_base: "4065.00", vat_amount: "853.65" },
              { vat_rate: "21", taxable_base: "0.29", vat_amount: "0.06", adjustment_type: "ROUNDING" },
            ],
          },
          validations: [{ code: "VAT_ROUNDING_ADJUSTMENT", severity: "WARNING", field_name: "vat_lines", message: "Faktura obsahuje položku zaokrouhlení 0.35.", expected: "explicit invoice adjustment", actual: "0.35", details: { difference: "0.35" } }],
        }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );
    expect(screen.getByRole("heading", { name: "DPH a zaokrouhlení" })).toBeVisible();
    expect(screen.getByText("Zaokrouhlení")).toBeVisible();
    expect(screen.getAllByText(/pravděpodobně způsoben položkou Zaokrouhlení/)[0].closest(".alert")).toHaveClass("warning");
    expect(screen.getByText(/VAT_ROUNDING_ADJUSTMENT/).closest(".alert")).toHaveClass("warning");
    await act(async () => undefined);
  });

  it("shows the Pixel VAT summary as a normal row without a rounding warning", async () => {
    mockEmptyApi();
    render(
      <InvoiceDetail
        invoice={{
          ...invoice,
          data: {
            supplier_name: "Pixel Design s.r.o.", currency: "CZK",
            total_without_vat: "4300.00", total_vat: "903.00", total_amount: "5203.00",
            vat_lines: [{
              vat_rate: "21", taxable_base: "4300.00", vat_amount: "903.00",
              gross_amount: "5203.00", adjustment_type: null,
              source_text: "Sazba DPH Základ Výše DPH Celkem",
            }],
          },
          validations: [
            { code: "VAT_ROW_OK", severity: "OK", field_name: "vat_lines", message: "DPH řádek 1 matematicky sedí." },
            { code: "TOTAL_MATH_OK", severity: "OK", field_name: "total_amount", message: "Základ a DPH odpovídají celkové částce." },
          ],
        }}
        user={user}
        onBack={() => undefined}
        onRefresh={() => undefined}
      />,
    );

    expect(screen.getByText("DPH řádek 1")).toBeVisible();
    expect(screen.queryByText("Zaokrouhlení")).not.toBeInTheDocument();
    expect(screen.queryByText(/pravděpodobně způsoben položkou Zaokrouhlení/)).not.toBeInTheDocument();
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
