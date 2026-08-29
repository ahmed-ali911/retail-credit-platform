import type { ScheduleLine } from "../api/types";
import { money } from "./ui";

/**
 * Renders an offer's schedule preview. Profit declines row-over-row
 * (declining-balance recognition) while principal stays flat — that shape
 * should be visually obvious here.
 */
export function ScheduleTable({ schedule }: { schedule: ScheduleLine[] }) {
  return (
    <table className="data" aria-label="Installment schedule">
      <thead>
        <tr>
          <th>#</th>
          <th className="num">Principal</th>
          <th className="num">Profit</th>
          <th className="num">Total</th>
        </tr>
      </thead>
      <tbody>
        {schedule.map((line) => (
          <tr key={line.sequence_number} data-testid={`schedule-row-${line.sequence_number}`}>
            <td>{line.sequence_number}</td>
            <td className="num">{money(line.principal_component)}</td>
            <td className="num" data-testid={`profit-${line.sequence_number}`}>
              {money(line.profit_component)}
            </td>
            <td className="num">{money(line.total)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <th>Total</th>
          <th className="num">
            {money(schedule.reduce((s, l) => s + l.principal_component, 0))}
          </th>
          <th className="num">
            {money(schedule.reduce((s, l) => s + l.profit_component, 0))}
          </th>
          <th className="num">{money(schedule.reduce((s, l) => s + l.total, 0))}</th>
        </tr>
      </tfoot>
    </table>
  );
}
