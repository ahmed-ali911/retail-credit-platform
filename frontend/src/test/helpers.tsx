import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { vi } from "vitest";
import { AuthContext, AuthProvider } from "../auth/AuthContext";
import { setToken } from "../api/client";
import type { AuthUser } from "../api/types";

export interface Handler {
  method?: string;
  url: string | RegExp;
  status?: number;
  /** Static body, or a function called per request (for evolving state). */
  json?: unknown | ((req: { url: string; body: unknown }) => unknown);
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
    let parsedBody: unknown = undefined;
    if (init?.body != null) {
      try {
        parsedBody = JSON.parse(init.body as string);
      } catch {
        parsedBody = init.body;
      }
    }
    const resolved =
      typeof match.json === "function"
        ? (match.json as (req: { url: string; body: unknown }) => unknown)({
            url,
            body: parsedBody,
          })
        : match.json;
    const body = resolved === undefined ? "" : JSON.stringify(resolved);
    return new Response(body, {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const FAKE_USER: AuthUser = {
  id: 1,
  username: "tester",
  role: "admin",
  active: true,
  created_at: "2026-01-01T00:00:00Z",
};

export function renderWithProviders(
  ui: ReactElement,
  {
    path = "/",
    withAuth = true,
    token = "test-token",
    user,
    routePath,
  }: {
    path?: string;
    withAuth?: boolean;
    token?: string | null;
    /** Provide a fake signed-in user without hitting /auth/me. */
    user?: Partial<AuthUser> | null;
    /** Route pattern when `path` contains params, e.g. "/review/:applicationId". */
    routePath?: string;
  } = {},
) {
  if (token) setToken(token);

  const routed = routePath ? (
    <Routes>
      <Route path={routePath} element={ui} />
    </Routes>
  ) : (
    ui
  );

  const tree = (
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      {routed}
    </MemoryRouter>
  );

  if (user !== undefined) {
    const value = {
      user: user === null ? null : { ...FAKE_USER, ...user },
      loading: false,
      login: async () => {},
      logout: () => {},
    };
    return render(
      <AuthContext.Provider value={value}>{tree}</AuthContext.Provider>,
    );
  }

  return render(withAuth ? <AuthProvider>{tree}</AuthProvider> : tree);
}
