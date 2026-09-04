import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CustomerDirectoryPage } from "../pages/CustomerDirectoryPage";
import { mockFetch, renderWithProviders } from "./helpers";

// Step 14 bug fix — regression guard: given a mocked /customers response that
// includes reference_code (what the backend now actually sends), the
// directory must render the real code, not the "—" placeholder <RefCode>
// falls back to when reference_code is missing.
describe("Customer Directory — reference codes", () => {
  it("renders the reference code from the API response, not a dash placeholder", async () => {
    mockFetch([
      {
        method: "GET",
        url: /\/customers(\?|$)/,
        json: [
          {
            id: 9,
            reference_code: "CU-000009",
            name: "Dana Q",
            national_id: "ID-9",
            status: "active",
            risk_score: 700,
          },
        ],
      },
    ]);

    renderWithProviders(<CustomerDirectoryPage />, {
      user: { role: "credit_officer" },
      path: "/customers",
    });

    const cell = await screen.findByText("CU-000009");
    expect(cell).toBeInTheDocument();
    expect(screen.queryByText("—", { selector: ".ref-code" })).not.toBeInTheDocument();
  });
});
