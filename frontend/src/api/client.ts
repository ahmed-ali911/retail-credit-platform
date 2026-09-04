// Thin fetch wrapper: injects the bearer token, parses JSON, and turns a 401
// into a single app-wide "you are logged out" signal.

const TOKEN_KEY = "rc.token";

// In dev, requests go to /api and Vite proxies them to the backend (see
// vite.config.ts) — this avoids CORS without changing the backend.
const BASE = "/api";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore storage errors */
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

interface ApiOptions {
  method?: string;
  body?: unknown;
  auth?: boolean;
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  const detail =
    data && typeof data === "object" && data !== null && "detail" in data
      ? (data as { detail: unknown }).detail
      : data;

  // A 401 on an authenticated call means the token is gone/expired -> sign out
  // globally. A 401 on the login call itself is just bad credentials.
  if (res.status === 401 && auth) {
    setToken(null);
    onUnauthorized();
    throw new ApiError(401, "Session expired — please sign in again.");
  }

  if (!res.ok) {
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

/**
 * Fetch a file (e.g. a CSV export) with the bearer token and trigger a browser
 * download. Kept separate from `api()` because the response is not JSON.
 */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401) {
    setToken(null);
    onUnauthorized();
    throw new ApiError(401, "Session expired — please sign in again.");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text || `Download failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * POST a single file as multipart/form-data (Step 15, Part E — bank-statement
 * .xlsx upload). Kept separate from `api()` because the request body isn't
 * JSON — the browser sets the multipart Content-Type boundary itself, so it
 * must NOT be set manually here.
 */
export async function uploadFile<T>(path: string, file: File): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const body = new FormData();
  body.append("file", file);

  const res = await fetch(`${BASE}${path}`, { method: "POST", headers, body });

  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  const detail =
    data && typeof data === "object" && data !== null && "detail" in data
      ? (data as { detail: unknown }).detail
      : data;

  if (res.status === 401) {
    setToken(null);
    onUnauthorized();
    throw new ApiError(401, "Session expired — please sign in again.");
  }
  if (!res.ok) {
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (Array.isArray(err.detail)) {
      // FastAPI validation errors
      return err.detail
        .map((e: { loc?: unknown[]; msg?: string }) =>
          `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? "invalid"}`,
        )
        .join("; ");
    }
    return err.message;
  }
  return err instanceof Error ? err.message : "Something went wrong";
}
