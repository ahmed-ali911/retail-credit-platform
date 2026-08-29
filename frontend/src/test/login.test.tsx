import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { mockFetch, renderWithProviders } from "./helpers";

describe("login flow", () => {
  it("signs in with valid credentials and lands on the dashboard", async () => {
    mockFetch([
      {
        method: "POST",
        url: "/auth/login",
        json: { access_token: "tok-123", token_type: "bearer", expires_in: 1800 },
      },
      {
        method: "GET",
        url: "/auth/me",
        json: {
          id: 1,
          username: "admin",
          role: "admin",
          active: true,
          created_at: "2026-01-01T00:00:00Z",
        },
      },
    ]);

    renderWithProviders(<App />, { path: "/", token: null });
    const user = userEvent.setup();

    // unauthenticated -> redirected to the login screen
    await screen.findByRole("heading", { name: /retail credit — staff/i });

    await user.type(screen.getByLabelText(/username/i), "admin");
    await user.type(screen.getByLabelText(/password/i), "admin");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(
      await screen.findByRole("heading", { name: /dashboard/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/signed in as/i)).toHaveTextContent("admin");
    expect(localStorage.getItem("rc.token")).toBe("tok-123");
  });

  it("shows an error and stays on login when the password is wrong", async () => {
    mockFetch([
      {
        method: "POST",
        url: "/auth/login",
        status: 401,
        json: { detail: "Invalid username or password" },
      },
    ]);

    renderWithProviders(<App />, { path: "/", token: null });
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: /retail credit — staff/i });
    await user.type(screen.getByLabelText(/username/i), "admin");
    await user.type(screen.getByLabelText(/password/i), "nope");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid username or password/i);
    expect(screen.getByRole("heading", { name: /retail credit — staff/i })).toBeInTheDocument();
  });
});
