export type AppRoute =
  | { page: "dashboard"; invoiceId?: string }
  | { page: "approvals" }
  | { page: "centres" }
  | { page: "exports" };

export function parseRoute(pathname: string): AppRoute {
  const invoice = pathname.match(/^\/invoices\/([^/]+)\/?$/);
  if (invoice) return { page: "dashboard", invoiceId: decodeURIComponent(invoice[1]) };
  if (pathname === "/approvals" || pathname.startsWith("/approvals/")) return { page: "approvals" };
  if (pathname === "/cost-centers") return { page: "centres" };
  if (pathname === "/exports") return { page: "exports" };
  return { page: "dashboard" };
}
