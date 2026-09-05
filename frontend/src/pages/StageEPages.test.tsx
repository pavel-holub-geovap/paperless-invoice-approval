import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Approvals } from "./Approvals";
import { CostCenters } from "./CostCenters";

afterEach(() => vi.unstubAllGlobals());

describe("Stage E pages", () => {
  it("shows uploader self-approval as pre-review without reject actions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [{
        id: "self-assignment", invoice_id: "own-invoice", invoice_status: "NEEDS_REVIEW",
        revision: 1, invoice_number: "OWN-1", invoice_total: "100.00", currency: "CZK",
        cost_center: "SEC-A", allocation_amount: "100.00", invoice_data: {},
        assignment_status: "PENDING", current: true, pre_review: true,
      }],
    }));
    render(<Approvals />);
    expect(await screen.findByRole("button", { name: "Schválit vlastní sekci" })).toBeVisible();
    expect(screen.getByText(/Finální schválení čeká na kontrolu queue-managera/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Zamítnout" })).not.toBeInTheDocument();
  });

  it("shows an approver task bound to allocation and revision", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [{
        id: "assignment-1", invoice_id: "invoice-1", invoice_status: "AWAITING_APPROVAL",
        revision: 4, supplier_name: "Dodavatel", invoice_number: "E-1",
        invoice_total: "1210.00", currency: "CZK", cost_center: "200",
        allocation_amount: "700.00", allocation_percentage: "57.851240",
        allocation_note: "Vývoj", invoice_data: { due_date: "2026-09-03", variable_symbol: "20260001" },
        assignment_status: "PENDING", current: true,
      }],
    }));
    render(<Approvals />);
    expect(await screen.findByText("Dodavatel · revize 4")).toBeVisible();
    expect(screen.getByText(/Schvaluji za 200/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Schválit" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Vrátit" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Zamítnout" })).toBeVisible();
    expect(screen.getByText("03.09.2026")).toBeVisible();
  });

  it("renders configurable cost centers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [{ id: "c-200", code: "200", name: "Vývoj", pohoda_code: "200", active: true, created_at: "2026-08-23T00:00:00Z", updated_at: "2026-08-23T00:00:00Z" }],
    }));
    render(<CostCenters />);
    expect(await screen.findByText("Vývoj")).toBeVisible();
    expect(screen.getByRole("button", { name: "Přidat sekci" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Deaktivovat" })).toBeVisible();
  });

  it("shows a paginated Czech approver history and combines search with filters", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/approvals/mine") return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          items: [{
            invoice_id: "invoice-history", paperless_document_id: 11, invoice_number: "25081151",
            supplier_name: "GIRITON Systems s.r.o.", currency: "CZK", current_status: "AWAITING_APPROVAL",
            current_revision: 3, source_status: "AVAILABLE", pdf_available: true,
            latest_event_at: "2026-08-27T07:12:00Z", assignment_count: 2,
            ocr_snippet: "…unikátní technická podpora systému…",
            latest_assignment: {
              assignment_id: "a-1", revision_id: "r-2", revision: 2,
              cost_center: { id: "c-200", code: "200", name: "Vývoj" }, amount: "71255.69",
              assigned_at: "2026-08-27T07:00:00Z", decision: "APPROVE", decision_at: "2026-08-27T07:12:00Z",
              assignment_status: "INVALIDATED", decision_valid: false, invalidated: true,
              event_at: "2026-08-27T07:12:00Z",
            },
          }],
          page: 1, page_size: 25, total: 1,
          filters: { cost_centers: [{ code: "200", name: "Vývoj" }] },
        }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Approvals history/>);
    expect(screen.getByRole("tab", { name: "Ke schválení (0)" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Moje historie" })).toBeVisible();
    expect(await screen.findByText("GIRITON Systems s.r.o.")).toBeVisible();
    expect(screen.getAllByText("Schváleno").some((node) => node.tagName === "TD")).toBe(true);
    expect(screen.getByText(/Později zneplatněno změnou faktury/)).toBeVisible();
    expect(screen.getByText("27.08.2026 09:12:00")).toBeVisible();
    expect(screen.getByText(/Nalezeno v textu dokumentu/)).toBeVisible();

    fireEvent.change(screen.getByLabelText("Hledat ve fakturách a jejich obsahu"), { target: { value: "technická podpora" } });
    fireEvent.change(screen.getByLabelText("Rozhodnutí"), { target: { value: "APPROVE" } });
    fireEvent.change(screen.getByLabelText("Období"), { target: { value: "365" } });
    fireEvent.change(screen.getByLabelText("Středisko"), { target: { value: "200" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/q=technick%C3%A1\+podpora.*decision=APPROVE.*cost_center=200.*date_from=/), expect.anything()));
  });

  it("opens an authorized read-only history detail with PDF and human decision history", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/approvals/mine") return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      return Promise.resolve({ ok: true, status: 200, json: async () => ({
        invoice_id: "invoice-history", paperless_document_id: 11, invoice_number: "25081151",
        supplier_name: "GIRITON Systems s.r.o.", currency: "CZK", current_status: "AWAITING_APPROVAL", current_revision: 3,
        current_data: { supplier_name: "GIRITON Systems s.r.o.", invoice_number: "25081151", total_amount: "4919.00", issue_date: "2026-08-26" },
        source_status: "AVAILABLE", pdf_available: true,
        paperless: { title: "GIRITON", tags: ["Přijatá faktura"], original_filename: "giriton.pdf" },
        history: [{ assignment_id: "a-1", revision_id: "r-2", revision: 2, cost_center: { id: "c-200", code: "200", name: "Vývoj" }, amount: "510.00", assigned_at: "2026-08-26T12:00:00Z", decision: "RETURN", decision_at: "2026-08-26T12:31:00Z", comment: "Ověřit období podpory.", assignment_status: "INVALIDATED", decision_valid: false, invalidated: true, event_at: "2026-08-26T12:31:00Z" }],
      }) });
    }));
    render(<Approvals history historyInvoiceId="invoice-history"/>);
    expect(await screen.findByRole("heading", { name: "Vrátil jste ke kontrole" })).toBeVisible();
    expect(screen.getAllByText("26.08.2026 14:31:00")).toHaveLength(2);
    expect(screen.getByText("Ověřit období podpory.")).toBeVisible();
    expect(screen.getByTitle("Originální faktura")).toHaveAttribute("src", "/api/invoices/invoice-history/pdf");
    expect(screen.getByText("Režim pouze pro čtení")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Uložit|Přidat|Předat|Schválit/ })).not.toBeInTheDocument();
  });

  it("explains a missing Paperless original without removing history", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      if (String(input) === "/api/approvals/mine") return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      return Promise.resolve({ ok: true, status: 200, json: async () => ({
        invoice_id: "missing", paperless_document_id: 99, invoice_number: "MISSING-1", supplier_name: "Dodavatel",
        currency: "CZK", current_status: "REJECTED", current_revision: 1, current_data: {}, source_status: "MISSING", pdf_available: false,
        paperless: { title: "Missing", tags: [] }, history: [{ assignment_id: "a", revision_id: "r", revision: 1, cost_center: { id: "c", code: "300", name: "Obchod" }, amount: "100.00", assigned_at: "2026-08-20T10:00:00Z", assignment_status: "INVALIDATED", invalidated: true, event_at: "2026-08-20T10:00:00Z" }],
      }) });
    }));
    render(<Approvals history historyInvoiceId="missing"/>);
    expect(await screen.findByText("Originální dokument již není v Paperless dostupný.")).toBeVisible();
    expect(screen.queryByTitle("Originální faktura")).not.toBeInTheDocument();
    expect(screen.getAllByText("Zneplatněno").length).toBeGreaterThanOrEqual(1);
  });
});
