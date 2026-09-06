import { describe, expect, it } from "vitest";
import {
  displayLabel,
  documentTypeLabel,
  extractionSourceLabel,
  pohodaImportMethodLabel,
  workflowStatusLabel,
} from "./labels";

describe("central Czech display labels", () => {
  it.each([
    ["AWAITING_APPROVAL", "Čeká na schválení"],
    ["REJECTED", "Zamítnuto"],
    ["PDF_ISDOC", "PDF s ISDOC"],
    ["OCR_AI", "OCR / AI"],
    ["RECEIVED_ADVANCE_INVOICE", "Přijatá zálohová faktura"],
  ])("maps %s without leaking the raw enum", (value, expected) => {
    expect(displayLabel(value)).toBe(expected);
    expect(displayLabel(value)).not.toContain(value);
  });

  it("provides domain-specific typed entry points", () => {
    expect(workflowStatusLabel("REJECTED")).toBe("Zamítnuto");
    expect(documentTypeLabel("RECEIVED_ADVANCE_INVOICE")).toBe("Přijatá zálohová faktura");
    expect(extractionSourceLabel("OCR_AI")).toBe("OCR / AI");
    expect(pohodaImportMethodLabel("PDF_ISDOC")).toBe("PDF s ISDOC");
  });

  it("does not expose an unknown raw backend code", () => {
    expect(displayLabel("NEW_BACKEND_ENUM")).toBe("Neznámý stav");
  });
});
