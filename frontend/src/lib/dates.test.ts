import { describe, expect, it } from "vitest";
import { formatDateCs, formatDateTimeCs, parseDateCs } from "./dates";

describe("Czech date presentation", () => {
  it("formats API ISO dates and Prague timestamps without changing their values", () => {
    expect(formatDateCs("2026-07-08")).toBe("08.07.2026");
    expect(formatDateCs("2026-06-30")).toBe("30.06.2026");
    expect(formatDateTimeCs("2026-08-23T15:42:57Z")).toBe("23.08.2026 17:42:57");
  });

  it("parses real Czech dates to ISO and rejects impossible dates", () => {
    expect(parseDateCs("30.06.2026")).toEqual({ iso: "2026-06-30" });
    expect(parseDateCs("31.02.2026")).toEqual({
      iso: null,
      error: "Zadané datum neexistuje.",
    });
  });
});
