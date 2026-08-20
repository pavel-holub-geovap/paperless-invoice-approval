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
};

export function StatusBadge({ value }: { value: string }) {
  return <span className={`badge badge-${tone[value] ?? "neutral"}`}>{value.replaceAll("_", " ")}</span>;
}

