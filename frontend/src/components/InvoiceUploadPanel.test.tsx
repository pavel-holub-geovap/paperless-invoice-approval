import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UploadTracking, User } from "../types";
import { InvoiceUploadPanel } from "./InvoiceUploadPanel";

const manager: User = {
  subject: "manager",
  username: "queue-manager",
  roles: ["QUEUE_MANAGER"],
  csrf_token: "csrf",
};
const approver: User = {
  subject: "approver",
  username: "approver1",
  roles: ["APPROVER"],
  csrf_token: "csrf",
};
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
    if (path === "/api/uploads?limit=10") return response([]);
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
  it("is visible for queue manager and hidden for approver", async () => {
    mockBase();
    const view = render(<InvoiceUploadPanel user={manager} onQueueChanged={() => undefined} onOpenInvoice={() => undefined} />);
    expect(screen.getByRole("button", { name: "+ Nahrát fakturu" })).toBeVisible();
    expect(screen.getByText("Přetáhněte fakturu sem nebo")).toBeVisible();
    view.rerender(<InvoiceUploadPanel user={approver} onQueueChanged={() => undefined} onOpenInvoice={() => undefined} />);
    expect(screen.queryByRole("button", { name: "+ Nahrát fakturu" })).not.toBeInTheDocument();
  });

  it("opens the shared file selector from both buttons", () => {
    mockBase();
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => undefined} onOpenInvoice={() => undefined} />);
    const input = screen.getByLabelText("Vybrat PDF faktury") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", { name: "+ Nahrát fakturu" }));
    fireEvent.click(screen.getByRole("button", { name: "Vybrat soubor" }));
    expect(click).toHaveBeenCalledTimes(2);
  });

  it("highlights on drag enter and returns to normal on drag leave", () => {
    mockBase();
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => undefined} onOpenInvoice={() => undefined} />);
    const zone = screen.getByText("Přetáhněte fakturu sem nebo").closest(".invoice-drop-zone")!;
    fireEvent.dragEnter(zone);
    expect(screen.getByText("Pusťte fakturu sem")).toBeVisible();
    expect(zone).toHaveClass("dragging");
    fireEvent.dragLeave(zone);
    expect(screen.getByText("Přetáhněte fakturu sem nebo")).toBeVisible();
    expect(zone).not.toHaveClass("dragging");
  });

  it("drops multiple PDFs independently and rejects an invalid file inline", async () => {
    const fetchMock = mockBase();
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => undefined} onOpenInvoice={() => undefined} />);
    const zone = screen.getByText("Přetáhněte fakturu sem nebo").closest(".invoice-drop-zone")!;
    fireEvent.drop(zone, { dataTransfer: { files: [pdf("one.pdf"), pdf("two.pdf"), new File(["x"], "bad.txt", { type: "text/plain" })] } });
    expect(await screen.findByText("3 soubory")).toBeVisible();
    expect(screen.getByText("bad.txt").parentElement).toHaveTextContent("Podporovány jsou pouze PDF soubory");
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2));
  });

  it("shows an immediate pending state and then the accepted Paperless state", async () => {
    let finish!: (value: unknown) => void;
    const pending = new Promise((resolve) => { finish = resolve; });
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/uploads/config") return response(config);
      if (path === "/api/uploads?limit=10") return response([]);
      if (init?.method === "POST") return pending;
      return response([]);
    }));
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => undefined} onOpenInvoice={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Vybrat PDF faktury"), { target: { files: [pdf()] } });
    expect(await screen.findByText("Nahrávám…")).toBeVisible();
    await act(async () => finish({ ok: true, status: 202, json: async () => tracking() }));
    expect(await screen.findByText("Nahráno · čeká na Paperless")).toBeVisible();
  });

  it("shows controlled upload error and retries with the retained file", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/uploads/config") return response(config);
      if (path === "/api/uploads?limit=10") return response([]);
      if (init?.method === "POST") {
        calls += 1;
        return response(calls === 1
          ? tracking({ status: "FAILED_RETRYABLE", tracking_status: "FAILED_RETRYABLE", retryable: true, error_message: "Paperless je momentálně nedostupný." })
          : tracking({ status: "PAPERLESS_PROCESSING", tracking_status: "PAPERLESS_PROCESSING" }), true, 202);
      }
      return response([]);
    }));
    render(<InvoiceUploadPanel user={manager} onQueueChanged={() => undefined} onOpenInvoice={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Vybrat PDF faktury"), { target: { files: [pdf()] } });
    expect(await screen.findByText(/Paperless je momentálně nedostupný/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Zkusit znovu" }));
    expect(await screen.findByText("Nahráno · čeká na Paperless")).toBeVisible();
    expect(calls).toBe(2);
  });

  it("polls processing uploads to ready state and refreshes the queue without F5", async () => {
    vi.useFakeTimers();
    const changed = vi.fn();
    const accepted = tracking();
    const ready = tracking({ status: "READY_FOR_REVIEW", tracking_status: "OCR_COMPLETE", invoice_id: "invoice-731", paperless_document_id: 731, ai_status: "AI_COMPLETED", workflow_status: "QUEUE_REVIEW" });
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/uploads/config") return response(config);
      if (path === "/api/uploads?limit=10") return response([]);
      if (init?.method === "POST") return response(accepted, true, 202);
      if (path === "/api/uploads/upload-1") return response(ready);
      return response([]);
    }));
    render(<InvoiceUploadPanel user={manager} onQueueChanged={changed} onOpenInvoice={() => undefined} />);
    await act(async () => undefined);
    fireEvent.change(screen.getByLabelText("Vybrat PDF faktury"), { target: { files: [pdf()] } });
    await act(async () => undefined);
    expect(screen.getByText("Nahráno · čeká na Paperless")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.getByText("Připraveno ke kontrole")).toBeVisible();
    expect(changed).toHaveBeenCalled();
  });
});
