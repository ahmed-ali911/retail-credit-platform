import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { GatewayPaymentCard } from "../components/GatewayPaymentCard";
import { PaymentOperationsPage } from "../pages/PaymentOperationsPage";
import { GatewayReconciliationPage } from "../pages/GatewayReconciliationPage";
import { mockFetch, renderWithProviders } from "./helpers";

describe("Customer Payment Screen (GatewayPaymentCard)", () => {
  it("shows payment options and opens a gateway checkout session", async () => {
    mockFetch([
      {
        method: "GET",
        url: "/contracts/5/payment-options",
        json: () => ({
          contract_id: 5,
          currency: "KWD",
          next_installment_amount: 100.0,
          next_installment_due_date: "2026-10-01",
          overdue_amount: 50.0,
          late_fees_outstanding: 0,
          total_outstanding: 600.0,
          minimum_partial_amount: 5.0,
          can_pay_current_installment: true,
          can_pay_overdue: true,
          can_pay_full_outstanding: true,
          can_pay_partial: true,
          contract_reference: "CN-000005",
        }),
      },
      { method: "GET", url: /\/payments\/intents\?contract_id=5/, json: () => [] },
      {
        method: "POST",
        url: "/payments/intents",
        json: () => ({
          id: 42,
          payment_reference: "PI-000042",
          customer_id: 1,
          contract_id: 5,
          requested_amount: 100.0,
          currency: "KWD",
          payment_purpose: "current_installment",
          status: "INITIATED",
          gateway_session_id: null,
          gateway_name: "mock-payment-gateway",
          expires_at: null,
          created_at: "2026-09-14T00:00:00Z",
          updated_at: "2026-09-14T00:00:00Z",
          transactions: [],
          contract_reference: "CN-000005",
        }),
      },
      {
        method: "POST",
        url: "/payments/intents/42/checkout",
        json: () => ({
          payment_reference: "PI-000042",
          checkout_url: "http://localhost:8100/gateway/checkout/tok123",
          expires_at: "2026-09-14T00:15:00Z",
          status: "PENDING",
        }),
      },
    ]);

    renderWithProviders(<GatewayPaymentCard contractId={5} />, {
      user: { role: "sales_employee" },
    });

    expect(await screen.findByTestId("opt-overdue")).toHaveTextContent("50.00");
    expect(screen.getByTestId("opt-total")).toHaveTextContent("600.00");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /start gateway payment/i }));

    await waitFor(() =>
      expect(screen.getByTestId("gateway-checkout-panel")).toHaveTextContent("PI-000042"),
    );
    expect(screen.getByTestId("open-checkout-link")).toHaveAttribute(
      "href",
      "http://localhost:8100/gateway/checkout/tok123",
    );
  });
});

describe("Payment Operations Dashboard", () => {
  it("lists payment intents and filters by status", async () => {
    const intents = [
      {
        id: 1,
        payment_reference: "PI-000001",
        customer_id: 1,
        contract_id: 5,
        requested_amount: 100,
        currency: "KWD",
        payment_purpose: "current_installment",
        status: "SETTLED",
        gateway_session_id: null,
        gateway_name: "mock-payment-gateway",
        expires_at: null,
        created_at: "2026-09-14T00:00:00Z",
        updated_at: "2026-09-14T00:00:00Z",
        transactions: [],
        contract_reference: "CN-000005",
      },
      {
        id: 2,
        payment_reference: "PI-000002",
        customer_id: 2,
        contract_id: 6,
        requested_amount: 50,
        currency: "KWD",
        payment_purpose: "partial",
        status: "FAILED",
        gateway_session_id: null,
        gateway_name: "mock-payment-gateway",
        expires_at: null,
        created_at: "2026-09-14T00:00:00Z",
        updated_at: "2026-09-14T00:00:00Z",
        transactions: [],
        contract_reference: "CN-000006",
      },
    ];

    mockFetch([
      {
        method: "GET",
        url: /\/payments\/intents\?status=SETTLED/,
        json: () => intents.filter((i) => i.status === "SETTLED"),
      },
      {
        method: "GET",
        url: /\/payments\/intents\?limit=100/,
        json: () => intents,
      },
    ]);

    renderWithProviders(<PaymentOperationsPage />, { user: { role: "admin" } });

    expect(await screen.findByText("PI-000001")).toBeInTheDocument();
    expect(screen.getByText("PI-000002")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText(/status/i), "SETTLED");

    await waitFor(() => expect(screen.queryByText("PI-000002")).not.toBeInTheDocument());
    expect(screen.getByText("PI-000001")).toBeInTheDocument();
  });
});

describe("Gateway Reconciliation screen", () => {
  it("pulls a settlement batch and requests a resolution for an exception", async () => {
    const items = [
      {
        id: 9,
        settlement_batch_id: 1,
        settlement_date: "2026-09-14",
        gateway_transaction_reference: "GWTXN-1",
        merchant_reference: "PI-000009",
        gross_amount: 100.0,
        gateway_fee: 1.0,
        net_amount: 99.0,
        currency: "KWD",
        gateway_reported_status: "SETTLED",
        outcome: "AMOUNT_MISMATCH",
        status: "open",
        matched_payment_id: null,
        matched_intent_id: 9,
        variance_amount: 5.0,
        resolution_reason: null,
        resolution_comments: null,
        resolved_by: null,
        resolved_at: null,
        created_at: "2026-09-14T00:00:00Z",
      },
    ];

    mockFetch([
      { method: "GET", url: "/payments/settlement-batches", json: () => [] },
      { method: "GET", url: /\/payments\/reconciliation-items/, json: () => items },
      {
        method: "POST",
        url: "/payments/settlement-batches/pull",
        json: () => ({
          batch: {
            id: 1,
            batch_reference: "GW-SETTLEMENT-2026-09-14",
            settlement_date: "2026-09-14",
            gateway_name: "mock-payment-gateway",
            currency: "KWD",
            item_count: 1,
            total_gross_amount: 100.0,
            total_gateway_fee: 1.0,
            total_net_amount: 99.0,
            imported_by: 1,
            imported_at: "2026-09-14T00:00:00Z",
          },
          items_processed: 1,
          matched: 0,
          exceptions: 1,
          missing_in_gateway: 0,
        }),
      },
      {
        method: "POST",
        url: "/payments/reconciliation-items/9/resolve",
        json: () => ({
          id: 1,
          action_type: "gateway_reconciliation.resolve",
          entity_type: "gateway_reconciliation_item",
          entity_id: "9",
          requested_by: 1,
          requested_at: "2026-09-14T00:00:00Z",
          payload: {},
          status: "pending",
          decided_by: null,
          decided_at: null,
          decision_notes: null,
        }),
      },
    ]);

    renderWithProviders(<GatewayReconciliationPage />, { user: { role: "finance_officer" } });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /pull today's batch/i }));
    await waitFor(() => expect(screen.getByTestId("pull-result")).toHaveTextContent("1 exception"));

    expect(await screen.findByTestId("recon-item-row-9")).toHaveTextContent("PI-000009");

    await user.click(screen.getByRole("button", { name: /resolve/i }));
    await user.type(
      screen.getByLabelText(/^reason$/i),
      "confirmed with gateway ops",
    );
    await user.click(screen.getByRole("button", { name: /submit resolution request/i }));

    await waitFor(() =>
      expect(screen.getByText(/resolution requested/i)).toBeInTheDocument(),
    );
  });
});
