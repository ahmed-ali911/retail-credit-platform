import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { setToken } from "../api/client";

export interface Handler {
  method?: string;
  url: string | RegExp;
  status?: number;
  json?: unknown;
}

/** Install a fetch mock that resolves requests against `handlers` in order,
 *  falling back to the last matching handler (so repeated GETs keep working). */
export function mockFetch(handlers: Handler[]) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const match = handlers.find(
      (h) =>
        (h.method ?? "GET").toUpperCase() === method &&
        (typeof h.url === "string" ? url.endsWith(h.url) : h.url.test(url)),
    );
    if (!match) {
      throw new Error(`Unhandled request: ${method} ${url}`);
    }
    const status = match.status ?? 200;
    const body = match.json === undefined ? "" : JSON.stringify(match.json);
    return new Response(body, {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export function renderWithProviders(
  ui: ReactElement,
  { path = "/", withAuth = true, token = "test-token" }: {
    path?: string;
    withAuth?: boolean;
    token?: string | null;
  } = {},
) {
  if (token) setToken(token);
  const tree = (
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      {ui}
    </MemoryRouter>
  );
  return render(withAuth ? <AuthProvider>{tree}</AuthProvider> : tree);
}
