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
  profile: (CustomerProfileIn & { id: number; customer_id: number }) | null;
}

export interface ProductOut {
  id: number;
  name: string;
  category: string;
  cash_price: number;
  installment_eligible: boolean;
}

export interface TriggeredRule {
  rule: string;
  outcome: string;
  reason: string;
  context?: Record<string, unknown>;
}

export interface AssessmentResultOut {
  id: number;
  decision: "approved" | "rejected" | "referred";
  estimated_installment: number;
  debt_burden_ratio: number | null;
  triggered_rules: TriggeredRule[];
  config_snapshot: Record<string, unknown>;
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
}

export interface SalesOrderOut {
  id: number;
  application_id: number;
  product_id: number;
  offer_id: number;
  sale_price: number;
  down_payment_amount: number;
  created_at: string;
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
}

export interface AcceptResult {
  offer_id: number;
  sales_order_id: number;
  contract_id: number;
  contract: ContractOut;
}

export interface ReceivableOut {
  contract_id: number;
  outstanding_principal: number;
  outstanding_profit: number;
  outstanding_receivable: number;
  outstanding_late_fees: number;
  total_installments_paid: number;
  total_installments_remaining: number;
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
  };
}
