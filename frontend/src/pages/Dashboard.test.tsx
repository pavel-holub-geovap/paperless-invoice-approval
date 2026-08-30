import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { InvoiceListItem, User } from "../types";
import { Dashboard } from "./Dashboard";

const manager: User = {
  subject: "manager",
  username: "queue-manager",
  roles: ["QUEUE_MANAGER"],
  csrf_token: "csrf",
};
const invoiceRow: InvoiceListItem = {
  id: "invoice-1", paperless_document_id: 24, status: "QUEUE_REVIEW", disposition: "ACTIVE",
  source_status: "AVAILABLE", current_revision_number: 1, title: "invoice.pdf", sync_status: "SYNCED",
  ai_status: "AI_COMPLETED", approvals_done: 0, approvals_required: 0, warning_count: 0,
  document_type: "RECEIVED_INVOICE", processing_mode: "FOR_APPROVAL",
  extraction_source: "OCR_AI", isdoc_status: "NOT_PRESENT", pohoda_import_method: "GENERATED_XML",
  blocking_error_count: 0, updated_at: "2026-08-26T08:00:00Z",
};

function response(value: unknown) {
  return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: async () => value });
}

afterEach(() => vi.unstubAllGlobals());

describe("invoice queue actions", () => {
  it("renders upload and refresh buttons in the same action container above one invoice table", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/uploads/config") return response({ max_file_size: 8388608, supported_mime_types: ["application/pdf"], supported_extensions: [".pdf"], multi_upload: true });
      if (path.startsWith("/api/invoices?")) return response([invoiceRow]);
      return response([]);
    }));
    render(<Dashboard user={manager} onNavigate={() => undefined} />);
    await act(async () => undefined);

    const upload = screen.getByRole("button", { name: "+ Nahrát fakturu" });
    const refresh = screen.getByRole("button", { name: "Obnovit" });
    const actions = screen.getByLabelText("Akce fronty");
    expect(actions).toContainElement(upload);
    expect(actions).toContainElement(refresh);
    expect(actions).toHaveClass("queue-actions");
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(document.querySelector(".upload-list")).not.toBeInTheDocument();
    expect(screen.getByText("Přetáhněte fakturu sem nebo")).toBeVisible();
  });

  it("opens the shared file picker from the header upload action", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response([])));
    render(<Dashboard user={manager} onNavigate={() => undefined} />);
    const input = screen.getByLabelText("Vybrat PDF faktury") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", { name: "+ Nahrát fakturu" }));
    expect(click).toHaveBeenCalledOnce();
  });

  it("disables refresh while the request is pending and prevents a double request", async () => {
    let invoiceCalls = 0;
    let finishRefresh!: (value: unknown) => void;
    const pendingRefresh = new Promise((resolve) => { finishRefresh = resolve; });
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === "/api/uploads/config") return response({ max_file_size: 8388608, supported_mime_types: ["application/pdf"], supported_extensions: [".pdf"], multi_upload: true });
      if (path.startsWith("/api/invoices?")) {
        invoiceCalls += 1;
        if (invoiceCalls === 1) return response([]);
        return pendingRefresh;
      }
      return response([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard user={manager} onNavigate={() => undefined} />);
    await waitFor(() => expect(invoiceCalls).toBe(1));

    fireEvent.click(screen.getByRole("button", { name: "Obnovit" }));
    const pendingButton = await screen.findByRole("button", { name: "Obnovuji…" });
    expect(pendingButton).toBeDisabled();
    fireEvent.click(pendingButton);
    expect(invoiceCalls).toBe(2);

    await act(async () => finishRefresh({ ok: true, status: 200, statusText: "OK", json: async () => [] }));
    expect(await screen.findByRole("button", { name: "Obnovit" })).toBeEnabled();
  });
});
