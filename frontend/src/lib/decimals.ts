/** Canonical decimal strings only; no floating-point arithmetic or rounding. */
export function decimalString(input: string): string | null {
  const text = input.trim().replace(/[\s\u00a0\u202f]/g, "");
  if (!text) return null;
  let value = text;
  if (text.includes(",") && text.includes(".")) {
    if (/^[+-]?\d{1,3}(\.\d{3})+,\d+$/.test(text)) value = text.replace(/\./g, "").replace(",", ".");
    else if (/^[+-]?\d{1,3}(,\d{3})+\.\d+$/.test(text)) value = text.replace(/,/g, "");
    else throw new Error("Nejednoznačná desetinná částka.");
  } else value = text.replace(",", ".");
  if (!/^[+-]?\d+(\.\d+)?$/.test(value)) throw new Error("Zadejte částku číslem, například 1 497,38.");
  return value;
}

export const financialFields = new Set(["total_without_vat", "total_vat", "total_amount", "rounding_amount"]);
