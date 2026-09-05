import { act, createRef } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UploadTracking, User } from "../types";
import { InvoiceUploadPanel, type InvoiceUploadPanelHandle } from "./InvoiceUploadPanel";

const manager: User = { subject: "manager", username: "queue-manager", roles: ["QUEUE_MANAGER"], csrf_token: "csrf" };
const approver: User = { subject: "approver", username: "approver1", roles: ["APPROVER"], csrf_token: "csrf" };
const config = {
  max_file_size: 1024 * 1024,
  supported_mime_types: ["application/pdf"],
  supported_extensions: [".pdf"],
  multi_upload: true,
};

function response(value: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, statusText: ok ? "OK" : "Error", json: async () => value });
}

function tracking(overrides: Partial<UploadTracking> = {}): UploadTracking {
  return {
    id: "upload-1",
    idempotency_key: "upload-idempotency-1",
    filename: "invoice.pdf",
    file_size: 100,
    mime_type: "application/pdf",
    sha256: "abc",
    status: "PAPERLESS_PROCESSING",
    tracking_status: "PAPERLESS_PROCESSING",
    uploaded_by: "queue-manager",
    retryable: false,
    retry_count: 0,
    created_at: "2026-08-26T08:00:00Z",
    updated_at: "2026-08-26T08:00:00Z",
    ...overrides,
  };
}

function mockBase(postResult: UploadTracking = tracking()) {
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/uploads/config") return response(config);
    if (path === "/api/uploads" && init?.method === "POST") return response(postResult, true, 202);
    if (path.startsWith("/api/uploads/")) return response(postResult);
    return response([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function pdf(name = "invoice.pdf") {
  return new File(["%PDF-1.7 synthetic"], name, { type: "application/pdf" });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("invoice upload panel", () => {
  it("renders the same secure drop zone for a queue manager and an approver", () => {
    mockBase();
    const view = render(<InvoiceUploadPanel user={manager} onQueueChanged={() => false} />);
    expect(screen.getByText("Přetáhněte fakturu sem nebo")).toBeVisible();
    expect(screen.getByRole("button", { name: "Vybrat soubor" })).toBeVisible();
    view.rerender(<InvoiceUploadPanel user={approver} onQueueChanged={() => false} />);
    expect(screen.getByText("Přetáhněte fakturu sem nebo")).toBeInTheDocument();
  });

  it("does not load or render a permanent upload history grid", async () => {
    const fetchMock = mockBase();
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => false} />);
    await act(async () => undefined);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/uploads?limit="))).toBe(false);
    expect(document.querySelector(".upload-list")).not.toBeInTheDocument();
    expect(screen.queryByText("6 souborů")).not.toBeInTheDocument();
  });

  it("opens the same multiple file selector from the exposed action and drop-zone button", () => {
    mockBase();
    const ref = createRef<InvoiceUploadPanelHandle>();
    render(<InvoiceUploadPanel ref={ref} user={manager} onQueueChanged={() => false} />);
    const input = screen.getByLabelText("Vybrat PDF faktury") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    ref.current?.openFilePicker();
    fireEvent.click(screen.getByRole("button", { name: "Vybrat soubor" }));
    expect(click).toHaveBeenCalledTimes(2);
    expect(input).toHaveAttribute("multiple");
  });

  it("highlights on drag enter and returns to the compact state on drag leave", () => {
    mockBase();
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => false} />);
    const zone = screen.getByText("Přetáhněte fakturu sem nebo").closest(".invoice-drop-zone")!;
    fireEvent.dragEnter(zone);
    expect(screen.getByText("Pusťte fakturu sem")).toBeVisible();
    expect(zone).toHaveClass("dragging");
    fireEvent.dragLeave(zone);
    expect(screen.getByText("Přetáhněte fakturu sem nebo")).toBeVisible();
    expect(zone).not.toHaveClass("dragging");
  });

  it("shows only temporary per-file feedback for a multi-upload and keeps an invalid failure independent", async () => {
    const fetchMock = mockBase();
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => false} />);
    const zone = screen.getByText("Přetáhněte fakturu sem nebo").closest(".invoice-drop-zone")!;
    fireEvent.drop(zone, { dataTransfer: { files: [pdf("one.pdf"), pdf("two.pdf"), new File(["x"], "bad.txt", { type: "text/plain" })] } });
    expect(await screen.findByText("Nahrávání 3 souborů")).toBeVisible();
    expect(screen.getByLabelText("Dočasný stav nahrávání")).toBeVisible();
    expect(screen.getByText("bad.txt").parentElement).toHaveTextContent("Podporovány jsou pouze PDF soubory");
    expect(document.querySelector(".upload-list")).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2));
  });

  it("shows immediate pending and temporary Paperless-accepted feedback", async () => {
    let finish!: (value: unknown) => void;
    const pending = new Promise((resolve) => { finish = resolve; });
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === "/api/uploads/config") return response(config);
      if (init?.method === "POST") return pending;
      return response([]);
    }));
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => false} />);
    fireEvent.change(screen.getByLabelText("Vybrat PDF faktury"), { target: { files: [pdf()] } });
    expect(await screen.findByText("Nahrávám…")).toBeVisible();
    await act(async () => finish({ ok: true, status: 202, json: async () => tracking() }));
    expect(await screen.findByText(/Dokument byl předán do Paperless/)).toBeVisible();
  });

  it("keeps a controlled Paperless failure visible, retries it, and allows dismissal", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === "/api/uploads/config") return response(config);
      if (init?.method === "POST") {
        calls += 1;
        return response(calls === 1
          ? tracking({ status: "FAILED_RETRYABLE", tracking_status: "FAILED_RETRYABLE", retryable: true, error_code: "PAPERLESS_UNAVAILABLE", error_message: "Paperless je momentálně nedostupný." })
          : tracking(), true, 202);
      }
      return response([]);
    }));
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => false} />);
    fireEvent.change(screen.getByLabelText("Vybrat PDF faktury"), { target: { files: [pdf()] } });
    expect(await screen.findByText(/Důvod: Paperless je momentálně nedostupný/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Zavřít stav uploadu invoice.pdf" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Zkusit znovu" }));
    expect(await screen.findByText(/Dokument byl předán do Paperless/)).toBeVisible();
    expect(calls).toBe(2);
  });

  it("removes temporary tracking after the invoice appears in the main queue", async () => {
    vi.useFakeTimers();
    const changed = vi.fn((invoiceId?: string) => Boolean(invoiceId));
    const accepted = tracking();
    const ready = tracking({ status: "READY_FOR_REVIEW", tracking_status: "OCR_COMPLETE", invoice_id: "invoice-731", paperless_document_id: 731, ai_status: "AI_COMPLETED", workflow_status: "QUEUE_REVIEW" });
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/uploads/config") return response(config);
      if (init?.method === "POST") return response(accepted, true, 202);
      if (path === "/api/uploads/upload-1") return response(ready);
      return response([]);
    }));
    render(<InvoiceUploadPanel user={manager} onQueueChanged={changed} />);
    await act(async () => undefined);
    fireEvent.change(screen.getByLabelText("Vybrat PDF faktury"), { target: { files: [pdf()] } });
    await act(async () => undefined);
    expect(screen.getByLabelText("Dočasný stav nahrávání")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.queryByLabelText("Dočasný stav nahrávání")).not.toBeInTheDocument();
    expect(changed).toHaveBeenCalledWith("invoice-731");
  });
});
