export type AppRoute =
  | { page: "dashboard"; invoiceId?: string }
  | { page: "approvals"; historyInvoiceId?: string; history?: boolean; uploaded?: boolean }
  | { page: "admin" }
  | { page: "exports" }
  | { page: "help" };

export function parseRoute(pathname: string): AppRoute {
  const invoice = pathname.match(/^\/invoices\/([^/]+)\/?$/);
  if (invoice) return { page: "dashboard", invoiceId: decodeURIComponent(invoice[1]) };
  const historyInvoice = pathname.match(/^\/approvals\/history\/([^/]+)\/?$/);
  if (historyInvoice) return { page: "approvals", history: true, historyInvoiceId: decodeURIComponent(historyInvoice[1]) };
  if (pathname === "/approvals/history" || pathname === "/approvals/history/") return { page: "approvals", history: true };
  if (pathname === "/approvals/uploaded" || pathname === "/approvals/uploaded/") return { page: "approvals", uploaded: true };
  if (pathname === "/approvals" || pathname === "/approvals/") return { page: "approvals" };
  if (pathname === "/admin" || pathname === "/admin/" || pathname === "/cost-centers" || pathname === "/cost-centers/") return { page: "admin" };
  if (pathname === "/exports") return { page: "exports" };
  if (pathname === "/help" || pathname === "/help/") return { page: "help" };
  return { page: "dashboard" };
}
