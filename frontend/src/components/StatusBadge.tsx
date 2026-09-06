import { displayLabel } from "../lib/labels";

const tone: Record<string, string> = {
  BLOCKING_ERROR: "danger",
  REJECTED: "danger",
  RETURNED: "warning",
  WARNING: "warning",
  APPROVED: "success",
  READY_FOR_EXPORT: "success",
  IMPORTED_TO_POHODA: "success",
  OK: "success",
  AWAITING_APPROVAL: "info",
  SYNCED: "success",
  PENDING: "warning",
  ERROR: "danger",
};

export function StatusBadge({ value }: { value: string }) {
  const baseValue = value.replace(/^\d+\s+/, "");
  return <span className={`badge badge-${tone[baseValue] ?? "neutral"}`}>{displayLabel(value)}</span>;
}
