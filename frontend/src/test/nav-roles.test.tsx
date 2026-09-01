import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Shell } from "../components/Shell";
import { renderWithProviders } from "./helpers";

function navFor(role: string) {
  renderWithProviders(<Shell />, { user: { role } });
  return screen.getByRole("navigation");
}

describe("Role-gated navigation", () => {
  it("a sales_employee sees the core items but not staff-only screens", () => {
    const nav = navFor("sales_employee");
    expect(nav).toHaveTextContent("New Application");
    expect(nav).not.toHaveTextContent("Review Queue");
    expect(nav).not.toHaveTextContent("Configuration");
    expect(nav).not.toHaveTextContent("Approvals");
  });

  it("a finance_officer sees Reconciliation and Approvals but not Configuration", () => {
    const nav = navFor("finance_officer");
    expect(nav).toHaveTextContent("Reconciliation");
    expect(nav).toHaveTextContent("Approvals");
    expect(nav).not.toHaveTextContent("Configuration");
    expect(nav).not.toHaveTextContent("Review Queue");
  });

  it("an admin sees everything", () => {
    const nav = navFor("admin");
    expect(nav).toHaveTextContent("Review Queue");
    expect(nav).toHaveTextContent("Reconciliation");
    expect(nav).toHaveTextContent("Approvals");
    expect(nav).toHaveTextContent("Configuration");
    expect(nav).toHaveTextContent("Audit Log");
  });
});
