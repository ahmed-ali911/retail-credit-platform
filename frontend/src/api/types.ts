// Mirrors the backend Pydantic response schemas (Steps 1–6).

export interface AuthUser {
  id: number;
  username: string;
  role: string;
  active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface CustomerProfileIn {
  monthly_income: number;
  existing_monthly_obligations: number;
  employer_name?: string | null;
  employment_type?: string | null;
  address_line?: string | null;
  city?: string | null;
  contact_phone?: string | null;
}

export interface CustomerOut {
  id: number;
  name: string;
  national_id: string;
  phone: string | null;
  email: string | null;
  status: string;
  risk_score: number | null;
  created_at: string;
  reference_code: string;
  profile: (CustomerProfileIn & { id: number; customer_id: number }) | null;
}

export interface ProductOut {
  id: number;
  name: string;
  category: string;
  cash_price: number;
  installment_eligible: boolean;
  stock_quantity: number;
  reserved_quantity: number;
  available_quantity: number;
  reference_code: string;
}

export interface CustomerListItem {
  id: number;
  name: string;
  national_id: string;
  status: string;
  risk_score: number | null;
  reference_code: string;
}

// --- reports / dashboards (Step 11) ---
export interface ContractReportRow {
  contract_id: number;
  status: string;
  customer_id: number;
  customer_name: string;
  product_id: number;
  product_name: string;
  category: string;
  tenor_months: number;
  installment_sale_price: number;
  created_at: string;
  outstanding_total: number;
  next_due_date: string | null;
}

export interface ContractReportPage {
  items: ContractReportRow[];
  total: number;
  limit: number;
  offset: number;
  totals?: Record<string, unknown>;
}

export interface ProfitabilityBucket {
  contractual_profit: number;
  recognized_profit: number;
  unearned_profit: number;
  contracts: number;
}

export interface ProfitabilityReport {
  contracts_counted: number;
  total_contractual_profit: number;
  total_recognized_profit: number;
  total_unearned_profit: number;
  by_tenor: Record<string, ProfitabilityBucket>;
  by_category: Record<string, ProfitabilityBucket>;
}

export interface ExecutiveSummary {
  total_customers: number;
  active_contracts: number;
  total_outstanding_receivable: number;
  total_profit_recognized: number;
  approval_rate: number | null;
  decisions_considered: number;
}

export interface OperationsSummary {
  payments_today_count: number;
  payments_today_amount: number;
  applications_submitted_today: number;
  overdue_installments: number;
  open_reconciliation_exceptions: number;
  as_of: string;
}

export interface PortfolioSummary {
  contracts_by_status: Record<string, number>;
  dpd_distribution: {
    current: number;
    buckets: Record<string, number>;
    as_of: string;
  };
  average_contract_size: number;
}

export interface CollectionsSummary {
  open_cases: number;
  promise_to_pay_kept: number;
  promise_to_pay_broken: number;
  late_fees_charged_count: number;
  late_fees_charged_amount: number;
  late_fees_waived_count: number;
  late_fees_waived_amount: number;
}

export interface CreditRiskSummary {
  customers_by_risk_band: Record<string, number>;
  risk_band_thresholds: { low_min: number; medium_min: number };
  top_customers_by_exposure: {
    customer_id: number;
    name: string;
    total_outstanding: number;
  }[];
  rejection_rate: number | null;
  referral_rate: number | null;
  decisions_considered: number;
}

// --- collections (Step 8/10) ---
export interface CollectionActivityOut {
  id: number;
  collection_case_id: number;
  created_by: number;
  activity_type: "call" | "sms" | "email" | "visit" | "promise_to_pay" | "other";
  notes: string | null;
  created_at: string;
  promised_amount: number | null;
  promised_date: string | null;
  promise_status: "pending" | "kept" | "broken" | null;
}

export interface CollectionCaseOut {
  id: number;
  contract_id: number;
  status: "open" | "closed";
  opened_at: string;
  opened_reason: string;
  closed_at: string | null;
  reference_code: string;
  contract_reference: string;
}

export interface CollectionCaseDetailOut extends CollectionCaseOut {
  activities: CollectionActivityOut[];
}

export interface TriggeredRule {
  rule: string;
  outcome: string;
  reason: string;
  context?: Record<string, unknown>;
}

export interface AssessmentResultOut {
  id: number;
  decision: string;
  source?: string;
  estimated_installment: number;
  debt_burden_ratio: number | null;
  triggered_rules: TriggeredRule[];
  config_snapshot: Record<string, unknown>;
  reviewed_by?: number | null;
  notes?: string | null;
  created_at: string;
}

export interface ApplicationOut {
  id: number;
  customer_id: number;
  product_id: number;
  requested_amount: number;
  requested_tenor_months: number;
  channel: "online" | "branch";
  status: "draft" | "submitted" | "under_assessment" | "approved" | "rejected" | "referred";
  created_at: string;
  created_by: string;
  latest_assessment: AssessmentResultOut | null;
  assessments: AssessmentResultOut[];
  reference_code: string;
}

export interface ApplicationListItem {
  id: number;
  customer_id: number;
  product_id: number;
  requested_amount: number;
  status: ApplicationOut["status"];
  submitted_at: string;
  reference_code: string;
}

// --- P0-4 exposure ---
export interface ContractExposure {
  contract_id: number;
  status: string;
  outstanding_principal: number;
  outstanding_profit: number;
  outstanding_late_fees: number;
  outstanding_total: number;
}

export interface CustomerExposure {
  customer_id: number;
  aggregation_level: string;
  total_outstanding: number;
  contracts: ContractExposure[];
}

// --- P0-5 bank reconciliation ---
export interface ReconciliationStatus {
  unreconciled_payments: number;
  reconciled_payments: number;
  exception_payments: number;
  open_exceptions: number;
  resolved_exceptions: number;
  unmatched_bank_lines: number;
}

export interface BankStatementLine {
  id: number;
  bank_reference: string;
  amount: number;
  value_date: string;
  imported_at: string;
  matched_payment_id: number | null;
}

export interface MatchRunResult {
  lines_processed: number;
  matched: number;
  exceptions_created: number;
}

export interface BankLineUploadResult {
  rows_processed: number;
  rows_ingested: number;
  rows_rejected: number;
  rejected: { row: number; reason: string }[];
  matched: number;
  exceptions_created: number;
}

export interface ReconciliationException {
  id: number;
  bank_line_id: number;
  reason: "no_match" | "amount_mismatch" | "duplicate_candidate";
  status: "open" | "resolved";
  created_at: string;
  resolved_at: string | null;
  resolved_by: number | null;
}

// --- maker-checker approvals ---
export interface ApprovalRequestOut {
  id: number;
  action_type: string;
  entity_type: string;
  entity_id: string;
  requested_by: number;
  requested_at: string;
  payload: Record<string, unknown>;
  status: "pending" | "approved" | "rejected";
  decided_by: number | null;
  decided_at: string | null;
  decision_notes: string | null;
}

// --- config ---
export interface ConfigParameterOut {
  key: string;
  value: string;
  value_type: string;
  description: string | null;
}

// --- audit ---
export interface AuditEventOut {
  id: number;
  user_id: number | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before_value: Record<string, unknown> | null;
  after_value: Record<string, unknown> | null;
  timestamp: string;
}

// --- closure ---
export interface SettlementQuoteOut {
  contract_id: number;
  outstanding_principal: number;
  outstanding_late_fees: number;
  unearned_profit_total: number;
  profit_rebate_pct: number;
  profit_rebate_amount: number;
  profit_still_charged: number;
  final_payoff_amount: number;
  quote_expiry: string;
  // BDR item #7 — a staff-granted rebate that deviates from the 0% default;
  // settling this quote needs a different approver first.
  is_deviation: boolean;
}

export interface SettleResult {
  contract_id: number;
  status: string; // "closed" | "pending_approval"
  quote: SettlementQuoteOut;
  closure: ContractClosureOut | null;
  pending_approval: ApprovalRequestOut | null;
}

export interface ContractClosureOut {
  id: number;
  contract_id: number;
  reason: "normal" | "early_settlement" | "cancellation" | "return";
  financial_adjustment: number | null;
  closed_at: string;
  notes: string | null;
}

export interface ScheduleLine {
  sequence_number: number;
  principal_component: number;
  profit_component: number;
  total: number;
}

export interface OfferOut {
  id: number;
  application_id: number;
  cash_price: number;
  down_payment: number;
  tenor_months: number;
  profit_rate: number;
  installment_sale_price: number;
  total_profit: number;
  amount_financed: number;
  status: string;
  valid_until: string;
  created_at: string;
  down_payment_confirmed: boolean;
  down_payment_reference: string | null;
  accepted_at: string | null;
  schedule_preview: ScheduleLine[];
  reference_code: string;
  application_reference: string;
}

export interface SalesOrderOut {
  id: number;
  application_id: number;
  product_id: number;
  offer_id: number;
  sale_price: number;
  down_payment_amount: number;
  created_at: string;
  reference_code: string;
  application_reference: string;
  product_reference: string;
}

export interface InstallmentOut {
  id: number;
  contract_id: number;
  sequence_number: number;
  due_date: string;
  principal_component: number;
  profit_component: number;
  principal_paid: number;
  profit_paid: number;
  principal_outstanding: number;
  profit_outstanding: number;
  late_fee_outstanding: number;
  total_due: number;
  status: "pending" | "partially_paid" | "overdue" | "paid";
}

export interface ContractOut {
  id: number;
  sales_order_id: number;
  tenor_months: number;
  total_profit: number;
  unearned_profit_balance: number;
  status: "created" | "active" | "closed";
  created_at: string;
  activated_at: string | null;
  sales_order: SalesOrderOut;
  installments: InstallmentOut[];
  closure?: ContractClosureOut | null;
  reference_code: string;
}

export interface AcceptResult {
  offer_id: number;
  sales_order_id: number;
  contract_id: number;
  contract: ContractOut;
  contract_reference: string;
}

export interface ReceivableOut {
  contract_id: number;
  outstanding_principal: number;
  outstanding_profit: number;
  outstanding_receivable: number;
  outstanding_late_fees: number;
  total_installments_paid: number;
  total_installments_remaining: number;
  contract_reference?: string;
}

export interface PaymentResult {
  replayed: boolean;
  payment: {
    id: number;
    amount: number;
    external_reference: string;
    status: string;
    allocated_amount: number;
    unallocated_amount: number;
    reference_code: string;
    contract_reference: string;
  };
}

// --- ECL & provision ----------------------------------------------------- //
export interface EclRunDict {
  run_id: number;
  run_ref: string | null;
  status: string;
  as_of_date: string;
  methodology: string;
  ecl_config_version: number | null;
  contracts_assessed: number;
  contracts_by_stage: Record<string, number> | null;
  total_ead: number | null;
  total_ecl: number | null;
  total_ecl_stage_1: number | null;
  total_ecl_stage_2: number | null;
  total_ecl_stage_3: number | null;
  total_provision_movement: number | null;
  accounting_event_id: number | null;
  posted_at: string | null;
  created_at: string;
}

export interface EclDashboard {
  active_methodology: string;
  ecl_config_version: number;
  total_exposure: number;
  total_ecl: number;
  total_provision: number;
  provision_movement: number;
  coverage_ratio: number | null;
  contracts_assessed: number;
  stage_exposure: Record<string, number>;
  stage_ecl: Record<string, number>;
  default_exposure: number;
  overrides_pending: number;
  overrides_active: number;
  stage_migration: Record<string, number>;
  last_run: EclRunDict | null;
}

export interface EclStageTrigger {
  id: string;
  name: string;
  category: string;
  triggered: boolean;
  detail: string;
}

export interface EclAssessmentRow {
  assessment_id: number;
  run_id: number | null;
  contract_id: number;
  customer_id: number | null;
  customer_name: string | null;
  product_id: number | null;
  contract_status: string | null;
  assessment_date: string;
  methodology: string;
  ecl_config_version: number | null;
  ead: number | null;
  dpd: number;
  dpd_bucket: string | null;
  risk_rating: string | null;
  risk_segment: string | null;
  origination_rating: string | null;
  origination_pd_12m: number | null;
  automated_stage: number | null;
  automated_pd_12m: number | null;
  automated_pd_lifetime: number | null;
  automated_lgd: number | null;
  automated_ecl: number | null;
  override_stage: number | null;
  override_pd_12m: number | null;
  override_pd_lifetime: number | null;
  override_lgd: number | null;
  override_status: string | null;
  final_stage: number | null;
  final_pd_12m: number | null;
  final_pd_lifetime: number | null;
  final_lgd: number | null;
  final_ead: number | null;
  final_ecl: number | null;
  previous_ecl: number | null;
  opening_provision: number | null;
  calculated_ecl: number | null;
  override_adjustment: number | null;
  closing_provision: number | null;
  provision_movement: number | null;
  movement_type: string | null;
  stage_reason: string | null;
  cure_note: string | null;
}

export interface EclPortfolio {
  columns: string[];
  rows: EclAssessmentRow[];
  total: number;
  limit: number;
  offset: number;
  totals: Record<string, number>;
  total_ead: number;
  total_ecl: number;
}

export interface EclOverride {
  id: number;
  override_type: string;
  status: string;
  reason_code: string;
  justification: string;
  evidence_ref: string | null;
  comments: string | null;
  automated_value: Record<string, unknown>;
  approved_value: Record<string, unknown>;
  ecl_before: number | null;
  ecl_after: number | null;
  financial_impact: number | null;
  effective_from: string;
  effective_to: string | null;
  review_date: string | null;
  approval_request_id: number | null;
  requested_by: number | null;
  approved_by: number | null;
  created_at: string;
}

export interface EclContractDetail extends EclAssessmentRow {
  stage_triggers: EclStageTrigger[];
  config_snapshot: Record<string, unknown>;
  versions: Record<string, string | number | null>;
  history: Array<{
    assessment_date: string;
    run_id: number | null;
    methodology: string;
    ead: number | null;
    dpd: number;
    automated_stage: number | null;
    final_stage: number | null;
    automated_ecl: number | null;
    final_ecl: number | null;
    opening_provision: number | null;
    closing_provision: number | null;
    provision_movement: number | null;
    movement_type: string | null;
    override_id: number | null;
  }>;
  stage_migration_history: Array<{ date: string; automated: number | null; final: number | null }>;
  overrides: EclOverride[];
  accounting_events: Array<{
    id: number;
    event_type: string;
    amount: number | null;
    status: string;
    event_date: string;
    external_gl_reference: string | null;
  }>;
  override_rules?: Record<string, unknown>;
}

export interface EclRunResult {
  run_id: number;
  run_ref: string;
  status: string;
  as_of_date: string;
  methodology: string;
  ecl_config_version: number;
  contracts_assessed: number;
  contracts_by_stage: Record<string, number>;
  total_ead: number;
  total_ecl: number | null;
  total_ecl_by_stage: Record<string, number>;
  total_provision_movement: number | null;
  accounting_event_id: number | null;
  posted: boolean;
}

export interface EclConfigResponse {
  active: Record<string, unknown> & { ecl_config_version: number; methodology: string };
  versions: Array<{
    version: number;
    is_active: boolean;
    methodology: string;
    notes: string | null;
    created_by: number | null;
    activated_at: string | null;
  }>;
}
