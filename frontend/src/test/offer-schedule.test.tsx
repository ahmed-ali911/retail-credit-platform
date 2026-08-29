import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScheduleTable } from "../components/ScheduleTable";
import type { ScheduleLine } from "../api/types";

// A real declining-balance schedule slice (cash 1200, dp 300, 12mo @ 9%):
// principal is flat at 75.00, profit falls every row.
const schedule: ScheduleLine[] = [
  { sequence_number: 1, principal_component: 75, profit_component: 12.46, total: 87.46 },
  { sequence_number: 2, principal_component: 75, profit_component: 11.42, total: 86.42 },
  { sequence_number: 3, principal_component: 75, profit_component: 10.39, total: 85.39 },
  { sequence_number: 4, principal_component: 75, profit_component: 9.35, total: 84.35 },
];

function profitValues(): number[] {
  return schedule.map((l) =>
    Number(screen.getByTestId(`profit-${l.sequence_number}`).textContent!.replace(/,/g, "")),
  );
}

describe("ScheduleTable", () => {
  it("renders each installment's principal, profit and total", () => {
    render(<ScheduleTable schedule={schedule} />);
    const row1 = screen.getByTestId("schedule-row-1");
    expect(row1).toHaveTextContent("75.00");
    expect(row1).toHaveTextContent("12.46");
    expect(row1).toHaveTextContent("87.46");
    expect(screen.getAllByRole("row")).toHaveLength(1 + schedule.length + 1); // head + rows + footer
  });

  it("shows profit declining row-over-row (declining-balance shape)", () => {
    render(<ScheduleTable schedule={schedule} />);
    const profits = profitValues();
    expect(profits).toEqual([12.46, 11.42, 10.39, 9.35]);
    for (let i = 1; i < profits.length; i++) {
      expect(profits[i]).toBeLessThan(profits[i - 1]);
    }
  });

  it("totals the columns in the footer", () => {
    render(<ScheduleTable schedule={schedule} />);
    // profit total = 43.62, principal total = 300.00
    expect(screen.getByText("300.00")).toBeInTheDocument();
    expect(screen.getByText("43.62")).toBeInTheDocument();
  });
});
