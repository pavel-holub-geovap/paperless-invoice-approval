import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Invoice, User } from "../types";
import { InvoiceDetail } from "./InvoiceDetail";

const manager: User = {
  subject: "manager-subject",
  username: "queue-manager",
  roles: ["QUEUE_MANAGER"],
  csrf_token: "csrf",
};

const approved: Invoice = {
  id: "invoice-f",
  paperless_document_id: 1,
  status: "APPROVED",
  disposition: { status: "ACTIVE" },
  source: { status: "AVAILABLE" },
  ai_status: "AI_COMPLETED",
  ai: { history: [] },
  current_revision_number: 22,
  paperless: {
    title: "Synthetic Invoice",
    tag_ids: [], tags: [], ocr_text: "OCR", original_filename: "invoice.pdf", sync_status: "SYNCED",
  },
  original_review_confirmed: true,
  data: { invoice_number: "TEST-1", supplier_name: "Dodavatel", total_amount: "1210", currency: "CZK" },
  extracted_fields: [], validations: [], allocations: [],
  allocation_summary: { invoice_total: "1210", allocated: "1210", remaining: "0" },
};

afterEach(() => vi.unstubAllGlobals());

function emptyApi() {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string | URL | Request) => ({
    ok: true,
    status: 200,
    json: async () => String(input).includes("/exports/config")
      ? { pohoda_target_ico: "15049248", pohoda_target_key_configured: false, identification: "ICO_ONLY" }
      : [],
  })));
}

describe("Stage F POHODA export", () => {
  it("offers explicit XML generation for an approved invoice", async () => {
    emptyApi();
    render(<InvoiceDetail invoice={approved} user={manager} onBack={() => undefined} onRefresh={() => undefined}/>);
    expect(screen.getByRole("heading", { name: "POHODA export" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Vygenerovat XML" })).toBeVisible();
    expect(screen.getByText(/stažení samo nepotvrzuje import/i)).toBeVisible();
    expect(await screen.findByText(/IČO 15049248/)).toBeVisible();
  });

  it("shows artifact downloads and explicit import confirmation", () => {
    emptyApi();
    const exported: Invoice = {
      ...approved,
      status: "EXPORT_CREATED",
      pohoda_export: {
        id: "artifact-1", status: "XSD_VALID", generator_version: "generator.v1",
        xsd_bundle_version: "2025-10-16", encoding: "Windows-1250", xml_sha256: "a".repeat(64),
        xml_size: 2048, generated_by: "manager", generated_at: "2026-08-23T20:00:00Z", validation_errors: [],
        pohoda_target_ico: "15049248",
        pohoda_target_validation: { status: "TARGET_UNIT_VALID", actual_ico: "15049248", errors: [] },
      },
    };
    render(<InvoiceDetail invoice={exported} user={manager} onBack={() => undefined} onRefresh={() => undefined}/>);
    expect(screen.getByRole("link", { name: "Stáhnout XML" })).toHaveAttribute("href", "/api/exports/artifacts/artifact-1/xml");
    expect(screen.getByRole("button", { name: "Stáhnout ZIP" })).toBeVisible();
    expect(screen.getByRole("button", { name: "OZNAČIT JAKO IMPORTOVÁNO DO POHODY" })).toBeVisible();
    expect(screen.getByLabelText("Nahrát POHODA response XML")).toBeVisible();
  });
});
