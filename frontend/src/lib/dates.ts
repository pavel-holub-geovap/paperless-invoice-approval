const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const CZECH_DATE = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/;

export type ParsedCzechDate = {
  iso: string | null;
  error?: string;
};

function partsInPrague(value: string, includeTime: boolean): Record<string, string> | null {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = new Intl.DateTimeFormat("cs-CZ", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false } : {}),
    timeZone: "Europe/Prague",
  }).formatToParts(parsed);
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

export function formatDateCs(value?: string | null): string {
  if (!value) return "—";
  const iso = ISO_DATE.exec(value);
  if (iso) return `${iso[3]}.${iso[2]}.${iso[1]}`;
  const parts = partsInPrague(value, false);
  return parts ? `${parts.day}.${parts.month}.${parts.year}` : "—";
}

export function formatDateTimeCs(value?: string | null): string {
  if (!value) return "—";
  const parts = partsInPrague(value, true);
  return parts
    ? `${parts.day}.${parts.month}.${parts.year} ${parts.hour}:${parts.minute}:${parts.second}`
    : "—";
}

export function parseDateCs(value: string): ParsedCzechDate {
  const trimmed = value.trim();
  if (!trimmed) return { iso: null };
  const match = CZECH_DATE.exec(trimmed);
  if (!match) return { iso: null, error: "Zadejte datum ve formátu DD.MM.YYYY." };
  const day = Number(match[1]);
  const month = Number(match[2]);
  const year = Number(match[3]);
  const candidate = new Date(Date.UTC(year, month - 1, day));
  if (
    candidate.getUTCFullYear() !== year
    || candidate.getUTCMonth() !== month - 1
    || candidate.getUTCDate() !== day
  ) {
    return { iso: null, error: "Zadané datum neexistuje." };
  }
  return {
    iso: `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
  };
}
