// Write-off & Recovery — the Contract page's write-off action, plus (once
// executed) the recovery entry screen. Deliberately hidden entirely on a
// contract with no write-off history once it's no longer active (settled/
// cancelled/returned contracts never show this card) — but a contract that
// WAS written off keeps showing its history and recovery section forever,
// closed or not, since that's exactly the record a written-off account
// needs to stay explainable.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type {
  EligibilityResultOut,
  RecoveryOut,
  WriteOffExecutionDetailOut,
  WriteOffExecutionOut,
  WriteOffRequestOut,
} from "../api/types";
import { Card, ErrorNote, Field, SelectField, money } from "./ui";
import { SkeletonText } from "./Skeleton";
import { StatusBadge } from "./StatusBadge";
import { useToast } from "./Toast";

const WRITEOFF_ROLES = ["collections_officer", "finance_officer", "credit_manager", "admin"];
const RECOVERY_ROLES = ["finance_officer", "admin"];

const REASON_CODES = [
  "COLLECTIONS_EXHAUSTED",
  "CUSTOMER_FINANCIAL_DIFFICULTY",
  "UNLIKELY_TO_RECOVER",
  "LEGAL_OUTCOME",
  "DECEASED_CUSTOMER",
  "INSOLVENCY",
  "MANAGEMENT_DECISION",
  "OTHER",
];

// --------------------------------------------------------------------------- //
// Eligibility panel
// --------------------------------------------------------------------------- //
function EligibilityPanel({ e }: { e: EligibilityResultOut }) {
  return (
    <div data-testid="writeoff-eligibility">
      <p>
        Eligibility: <StatusBadge status={e.status} />
        {e.dpd != null && <span className="muted"> · DPD {e.dpd}</span>}
        {e.ecl_stage != null && <span className="muted"> · ECL Stage {e.ecl_stage}</span>}
      </p>
      <table className="data" aria-label="Write-off eligibility indicators">
        <thead>
          <tr>
            <th>Indicator</th>
            <th>Result</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {e.indicators.map((i) => (
            <tr key={i.id} data-testid={`elig-indicator-${i.id}`}>
              <td>{i.name}</td>
              <td>
                <StatusBadge status={i.result} />
              </td>
              <td className="muted">{i.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Request form
// --------------------------------------------------------------------------- //
function RequestForm({
  contractId,
  eligibility,
  onDone,
}: {
  contractId: number;
  eligibility: EligibilityResultOut | null;
  onDone: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState({
    write_off_type: "FULL" as "FULL" | "PARTIAL",
    reason_code: "COLLECTIONS_EXHAUSTED",
    justification: "",
    evidence_ref: "",
    comments: "",
    exception_justification: "",
    requested_principal: "",
    requested_profit: "",
    requested_late_fee: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const needsException = eligibility != null && eligibility.status !== "ELIGIBLE";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        write_off_type: form.write_off_type,
        reason_code: form.reason_code,
        justification: form.justification,
        evidence_ref: form.evidence_ref || undefined,
        comments: form.comments || undefined,
      };
      if (needsException) body.exception_justification = form.exception_justification;
      if (form.write_off_type === "PARTIAL") {
        if (form.requested_principal) body.requested_principal = Number(form.requested_principal);
        if (form.requested_profit) body.requested_profit = Number(form.requested_profit);
        if (form.requested_late_fee) body.requested_late_fee = Number(form.requested_late_fee);
      }
      const req = await api<{ id: number }>(`/write-offs/contracts/${contractId}/requests`, {
        method: "POST",
        body,
      });
      toast.success(`Write-off requested — approval #${req.id} pending a second approver.`);
      setForm((f) => ({ ...f, justification: "", evidence_ref: "", comments: "", exception_justification: "" }));
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" data-testid="writeoff-request-form" onSubmit={submit}>
      <ErrorNote message={error} />
      {needsException && (
        <div className="alert alert--warn" data-testid="writeoff-exception-note">
          This contract's eligibility is <strong>{eligibility?.status}</strong> — a normal
          request is blocked. Submitting requires a controlled exception with mandatory
          justification below, and still goes through the same two-person approval.
        </div>
      )}
      <SelectField
        label="Write-off type"
        value={form.write_off_type}
        onChange={(e) => setForm({ ...form, write_off_type: e.target.value as "FULL" | "PARTIAL" })}
      >
        <option value="FULL">Full</option>
        <option value="PARTIAL">Partial</option>
      </SelectField>
      {form.write_off_type === "PARTIAL" && (
        <div className="field-row">
          <Field
            label="Principal to write off"
            inputMode="decimal"
            value={form.requested_principal}
            onChange={(e) => setForm({ ...form, requested_principal: e.target.value })}
          />
          <Field
            label="Profit to write off"
            inputMode="decimal"
            value={form.requested_profit}
            onChange={(e) => setForm({ ...form, requested_profit: e.target.value })}
          />
          <Field
            label="Late fees to write off"
            inputMode="decimal"
            value={form.requested_late_fee}
            onChange={(e) => setForm({ ...form, requested_late_fee: e.target.value })}
          />
        </div>
      )}
      <SelectField
        label="Reason code"
        value={form.reason_code}
        onChange={(e) => setForm({ ...form, reason_code: e.target.value })}
      >
        {REASON_CODES.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </SelectField>
      <Field
        label="Justification"
        value={form.justification}
        onChange={(e) => setForm({ ...form, justification: e.target.value })}
        required
        minLength={10}
      />
      <Field
        label="Evidence reference (optional)"
        value={form.evidence_ref}
        onChange={(e) => setForm({ ...form, evidence_ref: e.target.value })}
      />
      {needsException && (
        <Field
          label="Exception justification (mandatory)"
          value={form.exception_justification}
          onChange={(e) => setForm({ ...form, exception_justification: e.target.value })}
          required
          data-testid="exception-justification-field"
        />
      )}
      <button className="btn-primary" type="submit" disabled={busy}>
        Request write-off
      </button>
    </form>
  );
}

// --------------------------------------------------------------------------- //
// Request history + review/execute actions
// --------------------------------------------------------------------------- //
function RequestHistory({
  requests,
  canDecide,
  onChanged,
}: {
  requests: WriteOffRequestOut[];
  canDecide: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busyId, setBusyId] = useState<number | null>(null);

  async function execute(id: number) {
    setBusyId(id);
    try {
      const res = await api<{ replayed: boolean }>(`/write-offs/requests/${id}/execute`, {
        method: "POST",
      });
      toast.success(res.replayed ? "Already executed." : "Write-off executed.");
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  async function cancel(id: number) {
    setBusyId(id);
    try {
      await api(`/write-offs/requests/${id}/cancel`, { method: "POST", body: {} });
      toast.success("Request cancelled.");
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <table className="data" aria-label="Write-off requests">
      <thead>
        <tr>
          <th>#</th>
          <th>Type</th>
          <th>Status</th>
          <th>Eligibility</th>
          <th className="num">Requested total</th>
          <th>Reason</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {requests.map((r) => {
          const total = r.requested_principal + r.requested_profit + r.requested_late_fee + r.requested_other_charges;
          return (
            <tr key={r.id} data-testid={`writeoff-request-row-${r.id}`}>
              <td>{r.id}</td>
              <td>{r.write_off_type}</td>
              <td>
                <StatusBadge status={r.status} />
              </td>
              <td>
                <StatusBadge status={r.eligibility_status} />
                {r.is_exception && <span className="muted"> · exception</span>}
              </td>
              <td className="num">{money(total)}</td>
              <td>{r.reason_code}</td>
              <td>
                {canDecide && r.status === "APPROVED" && (
                  <button
                    className="btn-primary"
                    disabled={busyId === r.id}
                    onClick={() => void execute(r.id)}
                    data-testid={`execute-writeoff-${r.id}`}
                  >
                    Execute
                  </button>
                )}
                {canDecide && r.status === "PENDING" && (
                  <button
                    className="btn-link"
                    disabled={busyId === r.id}
                    onClick={() => void cancel(r.id)}
                  >
                    Cancel
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// --------------------------------------------------------------------------- //
// Executed write-off + recovery
// --------------------------------------------------------------------------- //
function RecoverySection({
  execution,
  canRecordRecovery,
  onChanged,
}: {
  execution: WriteOffExecutionDetailOut;
  canRecordRecovery: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState({ amount: "", external_reference: "", channel: "", recovery_date: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const totalWrittenOff =
    execution.executed_principal + execution.executed_profit + execution.executed_late_fee + execution.executed_other_charges;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api(`/write-offs/executions/${execution.id}/recoveries`, {
        method: "POST",
        body: {
          amount: Number(form.amount),
          external_reference: form.external_reference,
          channel: form.channel || undefined,
          recovery_date: form.recovery_date || undefined,
        },
      });
      toast.success(`Recovery of ${money(Number(form.amount))} recorded.`);
      setForm({ amount: "", external_reference: "", channel: "", recovery_date: "" });
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Write-off & Recovery" soft>
      <dl className="kv" data-testid="writeoff-execution-summary">
        <dt>Write-off type</dt>
        <dd>{execution.write_off_type}</dd>
        <dt>Original write-off</dt>
        <dd>{money(totalWrittenOff)}</dd>
        <dt>Total recovered</dt>
        <dd data-testid="total-recovered">{money(execution.total_recovered)}</dd>
        <dt>Remaining written-off balance</dt>
        <dd data-testid="remaining-recoverable">
          <strong>{money(execution.remaining_recoverable)}</strong>
        </dd>
        <dt>Executed</dt>
        <dd>{new Date(execution.executed_at).toLocaleString()}</dd>
      </dl>

      {execution.recoveries.length > 0 && (
        <table className="data" aria-label="Recoveries">
          <thead>
            <tr>
              <th>Date</th>
              <th>Reference</th>
              <th className="num">Amount</th>
              <th>Channel</th>
              <th>Recorded by</th>
            </tr>
          </thead>
          <tbody>
            {execution.recoveries.map((rec: RecoveryOut) => (
              <tr key={rec.id} data-testid={`recovery-row-${rec.id}`}>
                <td>{rec.recovery_date}</td>
                <td>{rec.external_reference}</td>
                <td className="num">{money(rec.amount)}</td>
                <td>{rec.channel ?? "—"}</td>
                <td>{rec.recorded_by != null ? `user #${rec.recorded_by}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canRecordRecovery && execution.remaining_recoverable > 0 && (
        <form className="inline-form" onSubmit={submit} data-testid="record-recovery-form">
          <ErrorNote message={error} />
          <Field
            label="Amount"
            inputMode="decimal"
            value={form.amount}
            onChange={(e) => setForm({ ...form, amount: e.target.value })}
            required
          />
          <Field
            label="External reference"
            value={form.external_reference}
            onChange={(e) => setForm({ ...form, external_reference: e.target.value })}
            placeholder="bank ref / cash receipt #"
            required
          />
          <Field
            label="Channel (optional)"
            value={form.channel}
            onChange={(e) => setForm({ ...form, channel: e.target.value })}
          />
          <Field
            label="Date (optional)"
            type="date"
            value={form.recovery_date}
            onChange={(e) => setForm({ ...form, recovery_date: e.target.value })}
          />
          <button className="btn-primary" type="submit" disabled={busy}>
            Record recovery
          </button>
        </form>
      )}
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Page-level card
// --------------------------------------------------------------------------- //
export function WriteOffCard({
  contractId,
  contractStatus,
  onChanged,
}: {
  contractId: number;
  contractStatus: string;
  onChanged: () => void;
}) {
  const { user } = useAuth();
  const [eligibility, setEligibility] = useState<EligibilityResultOut | null>(null);
  const [requests, setRequests] = useState<WriteOffRequestOut[] | null>(null);
  const [executionDetail, setExecutionDetail] = useState<WriteOffExecutionDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canRequest = user != null && WRITEOFF_ROLES.includes(user.role);
  const canRecordRecovery = user != null && RECOVERY_ROLES.includes(user.role);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRequests(await api<WriteOffRequestOut[]>(`/write-offs/requests?contract_id=${contractId}`));
    } catch {
      setRequests([]); // role may not permit write-off visibility — degrade quietly
    }
    if (contractStatus === "active" && canRequest) {
      try {
        setEligibility(await api<EligibilityResultOut>(`/write-offs/eligibility/${contractId}`));
      } catch {
        setEligibility(null);
      }
    }
    try {
      const execs = await api<WriteOffExecutionOut[]>(`/write-offs/executions?contract_id=${contractId}`);
      if (execs.length > 0) {
        setExecutionDetail(await api<WriteOffExecutionDetailOut>(`/write-offs/executions/${execs[0].id}`));
      } else {
        setExecutionDetail(null);
      }
    } catch {
      setExecutionDetail(null);
    }
  }, [contractId, contractStatus, canRequest]);

  useEffect(() => {
    void load();
  }, [load]);

  function refresh() {
    void load();
    onChanged();
  }

  if (requests == null) return null; // avoid a flash of nothing while loading
  if (contractStatus !== "active" && requests.length === 0 && executionDetail == null) {
    return null; // an ordinary, never-written-off, non-active contract — nothing to show
  }

  return (
    <>
      {contractStatus === "active" && canRequest && (
        <Card title="Write-off">
          <ErrorNote message={error} />
          {!eligibility ? (
            <SkeletonText lines={3} />
          ) : (
            <EligibilityPanel e={eligibility} />
          )}
          <div style={{ marginTop: "1rem" }}>
            <RequestForm contractId={contractId} eligibility={eligibility} onDone={refresh} />
          </div>
        </Card>
      )}

      {requests.length > 0 && (
        <Card title="Write-off requests" soft>
          <RequestHistory requests={requests} canDecide={canRequest} onChanged={refresh} />
        </Card>
      )}

      {executionDetail && (
        <RecoverySection execution={executionDetail} canRecordRecovery={canRecordRecovery} onChanged={refresh} />
      )}
    </>
  );
}
