import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { setApiUser } from "../lib/api";
import type { User } from "../types";
import { AdminPage } from "./AdminPage";

function response(value: unknown, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => value });
}

function mockIdentity(roles: string[]) {
  const identity: User = { subject: "test-sub", username: "test-user", roles, csrf_token: "csrf" };
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    if (String(input) === "/api/auth/me") return response(identity);
    return response([]);
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  setApiUser(null);
  window.history.replaceState({}, "", "/");
});

describe("ADMIN navigation and administration", () => {
  it.each([
    { roles: ["ADMIN"], admin: true, queue: false, approvals: false },
    { roles: ["QUEUE_MANAGER"], admin: false, queue: true, approvals: false },
    { roles: ["APPROVER"], admin: false, queue: false, approvals: true },
    { roles: ["ADMIN", "QUEUE_MANAGER"], admin: true, queue: true, approvals: false },
  ])("shows independent navigation for $roles", async ({ roles, admin, queue, approvals }) => {
    mockIdentity(roles);
    render(<App/>);
    await screen.findByRole("link", { name: "Nápověda" });
    expect(Boolean(screen.queryByRole("link", { name: "Administrace" }))).toBe(admin);
    expect(Boolean(screen.queryByRole("link", { name: "Fronta" }))).toBe(queue);
    expect(Boolean(screen.queryByRole("link", { name: "Moje schválení" }))).toBe(approvals);
  });

  it("does not render administration for a direct QUEUE_MANAGER deep link", async () => {
    mockIdentity(["QUEUE_MANAGER"]);
    window.history.replaceState({}, "", "/admin");
    render(<App/>);
    expect(await screen.findByRole("heading", { name: "Nemáte oprávnění k administraci" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Administrace" })).not.toBeInTheDocument();
  });

  it("does not grant an ADMIN-only user queue or export surfaces", async () => {
    mockIdentity(["ADMIN"]);
    window.history.replaceState({}, "", "/exports");
    render(<App/>);
    expect(await screen.findByRole("heading", { name: "Nemáte oprávnění k exportům" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Fronta" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Exporty" })).not.toBeInTheDocument();
  });

  it("renders sections, permissions, purge controls, audit and read-only identities", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/admin/invoices") return response([{
        id: "invoice-admin-1", paperless_document_id: 501, invoice_number: "ADM-501",
        supplier_name: "Test supplier", paperless_title: "Synthetic", status: "NEW",
        upload_origin: "QUEUE_MANAGER", uploaded_by: "queue-manager",
      }]);
      if (path === "/api/admin/purge-audits") return response([{
        id: "audit-1", invoice_id: "old-invoice", paperless_document_ids: [499],
        actor_subject: "admin-sub", actor_display_name: "admin1", reason: "Testovací doklad",
        result: "PURGED", artifact_counts: {}, created_at: "2026-09-11T10:00:00Z",
      }]);
      if (path === "/api/users") return response([{ subject: "approver-sub", username: "approver1", email: "approver@example.test", roles: ["APPROVER"] }]);
      if (path.startsWith("/api/cost-centers")) return response([{ id: "center-1", code: "IT", name: "IT", pohoda_code: "IT", active: true }]);
      if (path.startsWith("/api/section-permissions")) return response([{ id: "permission-1", approver_subject: "approver-sub", approver_username: "approver1", cost_center: { id: "center-1", code: "IT", name: "IT", active: true }, active: true, granted_by: "admin1", granted_at: "2026-09-11T10:00:00Z" }]);
      if (path === "/api/admin/invoices/invoice-admin-1/purge" && init?.method === "POST") return response({ invoice_id: "invoice-admin-1", status: "PURGED" });
      return response([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    setApiUser({ subject: "admin-sub", username: "admin1", roles: ["ADMIN"], csrf_token: "csrf" });
    render(<AdminPage/>);

    expect(await screen.findByRole("heading", { name: "Sekce a oprávnění schvalovatelů" })).toBeVisible();
    expect(screen.getByText("approver1")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Správa a odstranění dokladů" })).toBeVisible();
    expect(screen.getByText("Testovací doklad")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Známí uživatelé" })).toBeVisible();
    expect(screen.getByText(/Role se spravují v Keycloaku\./)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Nevratně odstranit" }));
    const dialog = screen.getByRole("dialog", { name: /Nevratně odstranit ADM-501/ });
    const confirmButton = within(dialog).getByRole("button", { name: "Nevratně odstranit" });
    expect(confirmButton).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Důvod nevratného odstranění"), { target: { value: "Duplicitní test" } });
    fireEvent.change(within(dialog).getByLabelText("Potvrzení nevratného odstranění"), { target: { value: "SMAZAT" } });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/invoices/invoice-admin-1/purge",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ confirmation: "SMAZAT", reason: "Duplicitní test" }) }),
    ));
  });

  it("requires explicit bulk selection, reason and bulk confirmation", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      if (String(input) === "/api/admin/invoices") return response([
        { id: "one", paperless_document_id: 1, invoice_number: "ONE", paperless_title: "One", status: "NEW", upload_origin: "QUEUE_MANAGER" },
        { id: "two", paperless_document_id: 2, invoice_number: "TWO", paperless_title: "Two", status: "NEW", upload_origin: "QUEUE_MANAGER" },
      ]);
      return response([]);
    }));
    render(<AdminPage/>);
    const bulkButton = await screen.findByRole("button", { name: "Nevratně odstranit vybrané (0)" });
    expect(bulkButton).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Vybrat doklad ONE" }));
    expect(screen.getByRole("button", { name: "Nevratně odstranit vybrané (1)" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Nevratně odstranit vybrané (1)" }));
    const dialog = screen.getByRole("dialog", { name: /Nevratně odstranit 1 vybraných dokladů/ });
    expect(within(dialog).getByText("SMAZAT VYBRANÉ")).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Nevratně odstranit" })).toBeDisabled();
  });
});
