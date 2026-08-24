import type { User } from "../types";

let currentUser: User | null = null;

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    const message = typeof detail === "string"
      ? detail
      : typeof detail === "object" && detail && "message" in detail
        ? String((detail as { message: unknown }).message)
        : JSON.stringify(detail);
    super(message);
    this.name = "ApiError";
  }
}

export function setApiUser(user: User | null) {
  currentUser = user;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (init.method && !["GET", "HEAD"].includes(init.method) && currentUser?.csrf_token) {
    headers.set("X-CSRF-Token", currentUser.csrf_token);
  }
  const response = await fetch(`/api${path}`, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function money(value?: string | number, currency = "CZK") {
  if (value == null || value === "") return "—";
  return new Intl.NumberFormat("cs-CZ", { style: "currency", currency }).format(Number(value));
}

export function shortDate(value?: string) {
  return value ? new Intl.DateTimeFormat("cs-CZ").format(new Date(value)) : "—";
}

export function pragueDateTime(value?: string) {
  return value
    ? new Intl.DateTimeFormat("cs-CZ", {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: "Europe/Prague",
      }).format(new Date(value))
    : "—";
}
