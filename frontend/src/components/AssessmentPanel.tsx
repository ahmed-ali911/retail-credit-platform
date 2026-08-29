import type { AssessmentResultOut } from "../api/types";
import { StatusBadge } from "./StatusBadge";

/**
 * The screen a Credit Officer actually reads: the decision, the DBR that drove
 * it, and every rule that was triggered with its human-readable reason.
 */
export function AssessmentPanel({
  assessment,
}: {
  assessment: AssessmentResultOut;
}) {
  const { decision, debt_burden_ratio, estimated_installment, triggered_rules } =
    assessment;

  return (
    <div className="stack" data-testid="assessment-panel">
      <div>
        <h3>Decision</h3>
        <StatusBadge status={decision} />
      </div>

      <dl className="kv">
        <dt>Debt-burden ratio</dt>
        <dd data-testid="dbr">
          {debt_burden_ratio == null ? "—" : debt_burden_ratio.toFixed(4)}
        </dd>
        <dt>Estimated installment</dt>
        <dd>{estimated_installment.toFixed(2)}</dd>
      </dl>

      <div>
        <h3>Triggered rules</h3>
        {triggered_rules.length === 0 ? (
          <p className="muted" data-testid="no-triggered-rules">
            No rules triggered — the application passed every check.
          </p>
        ) : (
          <ul className="stack" data-testid="triggered-rules">
            {triggered_rules.map((r, i) => (
              <li key={`${r.rule}-${i}`} data-testid={`rule-${r.rule}`}>
                <StatusBadge status={r.outcome} />{" "}
                <strong>{r.rule.replace(/_/g, " ")}</strong>
                <div className="muted">{r.reason}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
