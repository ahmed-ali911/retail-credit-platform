// Chart of Accounts & GL — Checkpoint 3. Five tabs: Chart of Accounts, Event
// Mappings, General Ledger, Trial Balance, Unmapped/Failed Events. Every
// mapping/account change goes through the EXISTING maker-checker Approvals
// screen (a "propose" call here creates an ApprovalRequest; decide happens
// on /approvals, by a different user — this page never re-hosts that step).
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { api, errorMessage } from "../api/client";
import type {
  AccountTypeValue,
  ChartOfAccountOut,
  GLJournalDetailOut,
  NormalBalanceValue,
  PostingSideValue,
} from "../api/types";
import { AMOUNT_SOURCES } from "../api/types";
import { Card, EmptyState, ErrorNote, Field, SelectField } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { SkeletonText } from "../components/Skeleton";
import { StatusBadge } from "../components/StatusBadge";
import { Tabs } from "../components/Tabs";
import { useToast } from "../components/Toast";
import { GenericTableReport } from "./ReportsPage";

const ACCOUNT_TYPES: AccountTypeValue[] = ["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"];
const NORMAL_BALANCES: NormalBalanceValue[] = ["DEBIT", "CREDIT"];
const POSTING_SIDES: PostingSideValue[] = ["DEBIT", "CREDIT"];

// Matches app/models/accounting.py::AccountingEventType exactly — 23
// values, verified against the running enum, not guessed.
const EVENT_TYPES = [
  "contract_activated", "down_payment_received", "payment_received", "profit_recognized",
  "late_fee_charged", "late_fee_waived", "early_settlement", "cancellation", "return",
  "contract_closed", "ecl_provision_movement", "ecl_provision_created", "ecl_provision_increased",
  "ecl_provision_released", "ecl_provision_override_adjustment", "payment_reversed",
  "refund_completed", "gateway_fee_recognized", "settlement_difference", "write_off_executed",
  "partial_write_off_executed", "recovery_received", "recovery_adjustment",
];

const PROPOSE_ROLES = ["finance_officer", "admin"];

function humanize(s: string): string {
  return s.replace(/_/g, " ");
}

// --------------------------------------------------------------------------- //
// Tab 1 — Chart of Accounts
// --------------------------------------------------------------------------- //
function AccountsTab({ canPropose }: { canPropose: boolean }) {
  const toast = useToast();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountTypeValue>("ASSET");
  const [normal, setNormal] = useState<NormalBalanceValue>("DEBIT");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  async function propose(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ id: number }>("/gl/accounts/propose-create", {
        method: "POST",
        body: {
          account_code: code, account_name: name, account_type: type,
          normal_balance: normal, description: description || undefined,
        },
      });
      toast.success(`Account creation proposed — approval #${r.id} pending a second approver.`);
      setCode("");
      setName("");
      setDescription("");
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" data-testid="coa-tab-accounts">
      <Card title="Chart of Accounts">
        <GenericTableReport
          key={refreshKey}
          endpoint="/gl/reports/chart-of-accounts"
          base="chart-of-accounts"
        />
      </Card>

      {canPropose && (
        <Card title="Propose a new account (maker-checker)">
          <p className="muted">
            Every account is a <strong>DEMO / ILLUSTRATIVE PLACEHOLDER</strong> — not this
            company&apos;s real chart of accounts.
          </p>
          <ErrorNote message={error} />
          <form className="stack" onSubmit={propose} data-testid="propose-account-form">
            <Field label="Account code" required value={code} onChange={(e) => setCode(e.target.value)} />
            <Field label="Account name" required value={name} onChange={(e) => setName(e.target.value)} />
            <SelectField
              label="Account type"
              required
              value={type}
              onChange={(e) => setType(e.target.value as AccountTypeValue)}
            >
              {ACCOUNT_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </SelectField>
            <SelectField
              label="Normal balance"
              required
              value={normal}
              onChange={(e) => setNormal(e.target.value as NormalBalanceValue)}
            >
              {NORMAL_BALANCES.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </SelectField>
            <Field
              label="Description (optional)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
            <button className="btn-primary" type="submit" disabled={busy || !code.trim() || !name.trim()}>
              {busy ? "Submitting…" : "Propose account"}
            </button>
          </form>
        </Card>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Tab 2 — Event Mappings
// --------------------------------------------------------------------------- //
interface DraftLine {
  posting_side: PostingSideValue;
  account_id: string;
  amount_source: string;
  reverse_on_negative: boolean;
}

function MappingsTab({ canPropose, accounts }: { canPropose: boolean; accounts: ChartOfAccountOut[] }) {
  const toast = useToast();
  const [eventType, setEventType] = useState(EVENT_TYPES[0]);
  const [classification, setClassification] = useState<"POSTABLE" | "SUMMARY_ONLY" | "RESERVED">("POSTABLE");
  const [changeReason, setChangeReason] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([
    { posting_side: "DEBIT", account_id: "", amount_source: "event_amount", reverse_on_negative: false },
    { posting_side: "CREDIT", account_id: "", amount_source: "event_amount", reverse_on_negative: false },
  ]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  function updateLine(i: number, patch: Partial<DraftLine>) {
    setLines((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  }

  function addLine() {
    setLines((prev) => [
      ...prev,
      { posting_side: "DEBIT", account_id: "", amount_source: "event_amount", reverse_on_negative: false },
    ]);
  }

  function removeLine(i: number) {
    setLines((prev) => prev.filter((_, idx) => idx !== i));
  }

  async function propose(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        account_event_type: eventType,
        classification,
        change_reason: changeReason || undefined,
      };
      if (classification === "POSTABLE") {
        body.lines = lines
          .filter((l) => l.account_id)
          .map((l) => ({
            posting_side: l.posting_side,
            account_id: Number(l.account_id),
            amount_source: l.amount_source,
            reverse_on_negative: l.reverse_on_negative,
          }));
      } else {
        body.lines = [];
      }
      const r = await api<{ id: number }>("/gl/mappings/propose-change", { method: "POST", body });
      toast.success(`Mapping change proposed — approval #${r.id} pending a second approver.`);
      setChangeReason("");
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" data-testid="coa-tab-mappings">
      <Card title="Event Mapping Register">
        <GenericTableReport key={refreshKey} endpoint="/gl/reports/mappings" base="event-mapping-register" />
      </Card>

      {canPropose && (
        <Card title="Propose a mapping change (maker-checker)">
          <p className="muted">
            Creates the NEXT version for the selected event type — the current active version stays
            unchanged until this is approved, and is never edited in place.
            <strong> Every mapping is a DEMO / ILLUSTRATIVE PLACEHOLDER — FINANCE DECISION REQUIRED.</strong>
          </p>
          <ErrorNote message={error} />
          <form className="stack" onSubmit={propose} data-testid="propose-mapping-form">
            <SelectField
              label="Event type"
              required
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
            >
              {EVENT_TYPES.map((t) => (
                <option key={t} value={t}>{humanize(t)}</option>
              ))}
            </SelectField>
            <SelectField
              label="Classification"
              required
              value={classification}
              onChange={(e) => setClassification(e.target.value as typeof classification)}
            >
              <option value="POSTABLE">POSTABLE</option>
              <option value="SUMMARY_ONLY">SUMMARY_ONLY</option>
              <option value="RESERVED">RESERVED</option>
            </SelectField>

            {classification === "POSTABLE" && (
              <div className="stack" data-testid="mapping-lines">
                <span className="muted">Debit/credit lines (at least one of each)</span>
                {lines.map((line, i) => (
                  <div key={i} className="inline-form" data-testid={`mapping-line-${i}`}>
                    <SelectField
                      label="Side"
                      value={line.posting_side}
                      onChange={(e) => updateLine(i, { posting_side: e.target.value as PostingSideValue })}
                    >
                      {POSTING_SIDES.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </SelectField>
                    <SelectField
                      label="Account"
                      value={line.account_id}
                      onChange={(e) => updateLine(i, { account_id: e.target.value })}
                    >
                      <option value="">Select…</option>
                      {accounts.map((a) => (
                        <option key={a.id} value={a.id}>{a.account_code} — {a.account_name}</option>
                      ))}
                    </SelectField>
                    <SelectField
                      label="Amount source"
                      value={line.amount_source}
                      onChange={(e) => updateLine(i, { amount_source: e.target.value })}
                    >
                      {AMOUNT_SOURCES.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </SelectField>
                    <label className="field">
                      <span>Reverse on negative</span>
                      <input
                        type="checkbox"
                        checked={line.reverse_on_negative}
                        onChange={(e) => updateLine(i, { reverse_on_negative: e.target.checked })}
                      />
                    </label>
                    {lines.length > 2 && (
                      <button type="button" className="btn-secondary" onClick={() => removeLine(i)}>
                        Remove
                      </button>
                    )}
                  </div>
                ))}
                <button type="button" className="btn-secondary" onClick={addLine}>
                  + Add line
                </button>
              </div>
            )}

            <Field
              label="Change reason (optional)"
              value={changeReason}
              onChange={(e) => setChangeReason(e.target.value)}
            />
            <button className="btn-primary" type="submit" disabled={busy}>
              {busy ? "Submitting…" : "Propose mapping change"}
            </button>
          </form>
        </Card>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Tab 3 — General Ledger
// --------------------------------------------------------------------------- //
function GeneralLedgerTab({ accounts }: { accounts: ChartOfAccountOut[] }) {
  const [accountId, setAccountId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [journalId, setJournalId] = useState<number | null>(null);
  const [journal, setJournal] = useState<GLJournalDetailOut | null>(null);
  const [journalError, setJournalError] = useState<string | null>(null);

  const openJournal = useCallback(async (reference: string) => {
    setJournalError(null);
    setJournal(null);
    const eventId = Number(reference.replace(/^GLJ-/, ""));
    try {
      const j = await api<GLJournalDetailOut>(`/gl/journals/by-event/${eventId}`);
      setJournal(j);
      setJournalId(j.id);
    } catch (err) {
      setJournalError(errorMessage(err));
    }
  }, []);

  const params = new URLSearchParams();
  if (accountId) params.set("account_id", accountId);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const endpoint = accountId ? `/gl/reports/ledger?${params.toString()}` : null;

  return (
    <div className="stack" data-testid="coa-tab-ledger">
      <Card title="General Ledger by Account">
        <div className="inline-form">
          <SelectField label="Account" required value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Select an account…</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.account_code} — {a.account_name}</option>
            ))}
          </SelectField>
          <Field label="From" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          <Field label="To" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>

        {!endpoint && <EmptyState message="Choose an account to view its ledger." />}
        {endpoint && (
          <div data-testid="ledger-drill-down-hint">
            <p className="muted">
              Click a journal reference below to open it (drill-down: Ledger → Journal →
              Accounting Event → source).
            </p>
            <GenericTableReport
              key={endpoint}
              endpoint={endpoint}
              base={`general-ledger-${accountId}`}
              summaryKeys={["account_code", "account_name", "closing_balance"]}
            />
            <table className="data" style={{ marginTop: "0.5rem" }}>
              <tbody>
                <tr>
                  <td colSpan={2}>
                    <label className="field">
                      <span>Open a journal by reference (e.g. GLJ-42)</span>
                      <input
                        data-testid="journal-reference-lookup"
                        placeholder="GLJ-42"
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void openJournal((e.target as HTMLInputElement).value);
                        }}
                      />
                    </label>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {journalError && <ErrorNote message={journalError} />}
      {journal && (
        <Card title={`Journal ${journal.journal_reference}`}>
          <div className="kv" data-testid={`journal-detail-${journalId}`}>
            <dt>Status</dt><dd><StatusBadge status={journal.journal_status} /></dd>
            <dt>Total debit / credit</dt><dd>{journal.total_debit.toFixed(2)} / {journal.total_credit.toFixed(2)}</dd>
            <dt>Balanced</dt><dd>{journal.is_balanced ? "Yes" : "No"}</dd>
            <dt>Mapping version</dt><dd>{journal.mapping_version_id ?? "—"}</dd>
            <dt>External GL reference</dt><dd>{journal.external_gl_reference ?? "—"}</dd>
          </div>
          <table className="data" aria-label="Journal lines">
            <thead>
              <tr><th>Side</th><th>Account</th><th className="num">Amount</th><th>Source</th></tr>
            </thead>
            <tbody>
              {journal.lines.map((l) => (
                <tr key={l.id}>
                  <td>{l.posting_side}</td>
                  <td>{l.account_code_snapshot} — {l.account_name_snapshot}</td>
                  <td className="num">{l.amount.toFixed(2)}</td>
                  <td>{l.amount_source}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ marginTop: "0.5rem" }}>
            Source event: <code>{journal.event.event_reference}</code>{" "}
            ({journal.event.event_type}){" "}
            {journal.event.contract_id != null && (
              <>
                — contract{" "}
                <Link to={`/contracts/${journal.event.contract_id}`}>#{journal.event.contract_id}</Link>
              </>
            )}
          </p>
        </Card>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Tab 4 — Trial Balance
// --------------------------------------------------------------------------- //
function TrialBalanceTab() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const endpoint = `/gl/reports/trial-balance${params.toString() ? `?${params.toString()}` : ""}`;

  return (
    <div className="stack" data-testid="coa-tab-trial-balance">
      <Card title="Trial Balance">
        <div className="inline-form">
          <Field label="From" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          <Field label="To" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <GenericTableReport
          key={endpoint}
          endpoint={endpoint}
          base="trial-balance"
          summaryKeys={[
            "total_debits", "total_credits", "difference",
            "unmapped_event_count", "unmapped_event_total",
            "failed_journal_count", "summary_only_event_count",
          ]}
        />
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Tab 5 — Unmapped / Failed Events
// --------------------------------------------------------------------------- //
function UnmappedFailedTab() {
  return (
    <div className="stack" data-testid="coa-tab-unmapped-failed">
      <Card title="Unmapped & Failed Events">
        <p className="muted">
          A postable event this platform could not turn into a balanced journal — an unmapped event
          type, or a genuine resolution/balance failure. Never silently dropped, never silently
          posted either.
        </p>
        <GenericTableReport endpoint="/gl/reports/unmapped-failed" base="unmapped-failed-events" />
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //
export function ChartOfAccountsPage() {
  const { user } = useAuth();
  const canPropose = user != null && PROPOSE_ROLES.includes(user.role);
  const [tab, setTab] = useState("accounts");
  const [accounts, setAccounts] = useState<ChartOfAccountOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadAccounts = useCallback(async () => {
    try {
      setAccounts(await api<ChartOfAccountOut[]>("/gl/accounts"));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  return (
    <div className="stack page-wide">
      <PageHeader
        title="Chart of Accounts & GL"
        description={
          <>
            Converts every recorded accounting event into an explainable, balanced double-entry
            journal. <strong>Every account and posting rule is a DEMO / ILLUSTRATIVE PLACEHOLDER —
            not this company&apos;s approved accounting policy.</strong> See{" "}
            <Link to="/approvals">Approvals</Link> to review a pending account or mapping change.
          </>
        }
      />
      <ErrorNote message={error} />

      <Tabs
        ariaLabel="Chart of Accounts sections"
        value={tab}
        onChange={setTab}
        items={[
          { value: "accounts", label: "Chart of Accounts" },
          { value: "mappings", label: "Event Mappings" },
          { value: "ledger", label: "General Ledger" },
          { value: "trial-balance", label: "Trial Balance" },
          { value: "unmapped-failed", label: "Unmapped / Failed" },
        ]}
      />

      {!accounts && !error && <SkeletonText lines={4} />}
      {accounts && (
        <>
          {tab === "accounts" && <AccountsTab canPropose={canPropose} />}
          {tab === "mappings" && <MappingsTab canPropose={canPropose} accounts={accounts} />}
          {tab === "ledger" && <GeneralLedgerTab accounts={accounts} />}
          {tab === "trial-balance" && <TrialBalanceTab />}
          {tab === "unmapped-failed" && <UnmappedFailedTab />}
        </>
      )}
    </div>
  );
}
