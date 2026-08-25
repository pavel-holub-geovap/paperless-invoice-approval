import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Approvals } from "./Approvals";
import { CostCenters } from "./CostCenters";

afterEach(() => vi.unstubAllGlobals());

describe("Stage E pages", () => {
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
    expect(screen.getByRole("button", { name: "Přidat středisko" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Deaktivovat" })).toBeVisible();
  });
});
