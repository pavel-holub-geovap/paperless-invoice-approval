import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { parseRoute } from "../routing";
import type { User } from "../types";
import helpCss from "./help.css?raw";
import { HelpPage, helpChapters } from "./HelpPage";

function response(value: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => value });
}

function loginAs(roles: User["roles"]) {
  const user: User = { subject: roles.join("-"), username: roles[0].toLowerCase(), roles, csrf_token: "csrf" };
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    if (String(input) === "/api/auth/me") return response(user);
    return response([]);
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("integrated user help", () => {
  it("shows the Help navigation and opens it for QUEUE_MANAGER", async () => {
    loginAs(["QUEUE_MANAGER"]);
    render(<App/>);

    const helpLink = await screen.findByRole("link", { name: "Nápověda" });
    expect(helpLink).toBeVisible();
    fireEvent.click(helpLink);

    expect(window.location.pathname).toBe("/help");
    expect(screen.getByRole("heading", { level: 1, name: "Nápověda ke schvalování faktur" })).toBeVisible();
  });

  it("shows the same Help without an administrator role for APPROVER", async () => {
    loginAs(["APPROVER"]);
    window.history.replaceState({}, "", "/help");
    render(<App/>);

    expect(await screen.findByRole("link", { name: "Nápověda" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "5. Schvalovatel" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "6. Správce fronty" })).toBeVisible();
    expect(screen.queryByText("403")).not.toBeInTheDocument();
  });

  it("parses both direct Help routes", () => {
    expect(parseRoute("/help")).toEqual({ page: "help" });
    expect(parseRoute("/help/")).toEqual({ page: "help" });
  });

  it("renders a complete chapter navigation with working anchors", () => {
    render(<HelpPage/>);
    const toc = screen.getByRole("navigation", { name: "Kapitoly uživatelské příručky" });
    const links = within(toc).getAllByRole("link");

    expect(links).toHaveLength(helpChapters.length);
    const approvalLink = within(toc).getByRole("link", { name: /10\.\s*Schvalování/ });
    expect(approvalLink).toHaveAttribute("href", "#schvalovani");
    expect(within(toc).getByRole("link", { name: /9\.\s*Sekce a rozdělení částek/ })).toHaveAttribute("href", "#sekce");
    expect(within(toc).getByRole("link", { name: /15\.\s*POHODA/ })).toHaveAttribute("href", "#pohoda");
    expect(document.getElementById("schvalovani")).not.toBeNull();
    expect(document.getElementById("sekce")).not.toBeNull();
    expect(document.getElementById("pohoda")).not.toBeNull();
  });

  it("contains every required offline workflow diagram and its text alternative", () => {
    render(<HelpPage/>);

    expect(screen.getByTestId("main-workflow")).toBeVisible();
    expect(screen.getByTestId("queue-manager-workflow")).toBeVisible();
    expect(screen.getByTestId("approver-upload-workflow")).toBeVisible();
    expect(screen.getByTestId("revision-workflow")).toBeVisible();
    expect(screen.getByTestId("pohoda-workflow")).toBeVisible();
    expect(screen.getAllByText("Textový popis schématu")).toHaveLength(5);
    expect(screen.getByText(/Vlastní schválení není finální schválení dokladu/)).toBeVisible();
  });

  it("documents current statuses and the manual POHODA boundary", () => {
    render(<HelpPage/>);

    expect(screen.getByRole("rowheader", { name: "Vyžaduje kontrolu" })).toBeVisible();
    expect(screen.getByRole("rowheader", { name: "Importováno do POHODY" })).toBeVisible();
    expect(screen.getByText(/Import do POHODY je vždy ruční/)).toBeVisible();
    expect(screen.getByText(/AI není autorita/)).toBeVisible();
    expect(screen.getByText(/Nejde automaticky o finální účetní středisko/)).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "20. Administrátor a nevratný PURGE" })).toBeVisible();
    expect(screen.getByText(/Role přiděluje a odebírá pouze Keycloak/)).toBeVisible();
    expect(screen.getByText(/PURGE je nevratný/)).toBeVisible();
    expect(screen.queryByText(/Oprávnění k sekcím spravuje správce fronty/)).not.toBeInTheDocument();
  });

  it("contains wide tables locally without widening the mobile page", () => {
    expect(helpCss).toMatch(/\.help-section\{[^}]*min-width:0/);
    expect(helpCss).toMatch(/\.help-table-wrap\{[^}]*min-width:0;max-width:100%;overflow:auto/);
  });
});
