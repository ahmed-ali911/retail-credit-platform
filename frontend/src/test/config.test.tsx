import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ConfigPage } from "../pages/ConfigPage";
import { mockFetch, renderWithProviders } from "./helpers";

describe("Config management", () => {
  it("shows a pending-approval message, not an immediate save", async () => {
    mockFetch([
      {
        method: "GET",
        url: "/config/parameters",
        json: [
          {
            key: "late_fee_rate",
            value: "0.02",
            value_type: "float",
            description: "Late fee rate",
          },
        ],
      },
      {
        method: "PUT",
        url: "/config/parameters/late_fee_rate",
        status: 202,
        json: { id: 7, action_type: "config.update", status: "pending" },
      },
    ]);

    renderWithProviders(<ConfigPage />, { user: { role: "admin" } });

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("config-edit-late_fee_rate"));
    await user.clear(screen.getByLabelText(/new value for late_fee_rate/i));
    await user.type(screen.getByLabelText(/new value for late_fee_rate/i), "0.03");
    await user.click(screen.getByRole("button", { name: /request change/i }));

    await waitFor(() =>
      expect(screen.getByTestId("config-pending")).toHaveTextContent(
        /awaiting a different approver/i,
      ),
    );
    // it must NOT claim the value was saved
    expect(screen.queryByText(/saved/i)).not.toBeInTheDocument();
  });
});
