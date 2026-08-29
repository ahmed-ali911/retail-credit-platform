import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { AcceptResult, ApplicationOut, OfferOut } from "../api/types";
import { ScheduleTable } from "../components/ScheduleTable";
import { StatusBadge } from "../components/StatusBadge";
import { Card, ErrorNote, Field, money } from "../components/ui";

export function OfferPage() {
  const { applicationId, offerId } = useParams();
  const navigate = useNavigate();

  const [application, setApplication] = useState<ApplicationOut | null>(null);
  const [offer, setOffer] = useState<OfferOut | null>(null);
  const [downPayment, setDownPayment] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      if (offerId) {
        setOffer(await api<OfferOut>(`/offers/${offerId}`));
      } else if (applicationId) {
        setApplication(await api<ApplicationOut>(`/applications/${applicationId}`));
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [applicationId, offerId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api<OfferOut>(`/applications/${applicationId}/offer`, {
        method: "POST",
        body: { down_payment_amount: Number(downPayment) },
      });
      setOffer(res);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function accept(e: FormEvent) {
    e.preventDefault();
    if (!offer) return;
    setError(null);
    setBusy(true);
    try {
      const res = await api<AcceptResult>(`/offers/${offer.id}/accept`, {
        method: "POST",
        body: {
          down_payment_confirmed: true,
          down_payment_reference: reference || null,
        },
      });
      navigate(`/contracts/${res.contract_id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Offer</h1>
      <ErrorNote message={error} />

      {!offer && application && (
        <Card title={`Application #${application.id}`}>
          <p>
            <StatusBadge status={application.status} />
          </p>
          {application.status !== "approved" ? (
            <p className="alert alert--info">
              An offer can only be generated for an <strong>approved</strong>{" "}
              application.
            </p>
          ) : (
            <form className="inline-form" onSubmit={generate}>
              <Field
                label="Down payment amount"
                inputMode="decimal"
                value={downPayment}
                onChange={(e) => setDownPayment(e.target.value)}
                required
              />
              <button className="btn-primary" type="submit" disabled={busy}>
                {busy ? "Pricing…" : "Generate offer"}
              </button>
            </form>
          )}
        </Card>
      )}

      {offer && (
        <>
          <Card title={`Offer #${offer.id}`}>
            <p>
              <StatusBadge status={offer.status} />
            </p>
            <dl className="kv">
              <dt>Cash price</dt>
              <dd>{money(offer.cash_price)}</dd>
              <dt>Down payment</dt>
              <dd>{money(offer.down_payment)}</dd>
              <dt>Installment sale price</dt>
              <dd>{money(offer.installment_sale_price)}</dd>
              <dt>Total profit</dt>
              <dd>{money(offer.total_profit)}</dd>
              <dt>Amount financed</dt>
              <dd>{money(offer.amount_financed)}</dd>
              <dt>Tenor</dt>
              <dd>{offer.tenor_months} months</dd>
              <dt>Profit rate</dt>
              <dd>{(offer.profit_rate * 100).toFixed(2)}%</dd>
            </dl>
          </Card>

          <Card title="Schedule preview">
            <p className="muted">
              Principal is repaid evenly; profit is front-loaded
              (declining-balance recognition).
            </p>
            <ScheduleTable schedule={offer.schedule_preview} />
          </Card>

          <Card title="Accept & confirm down payment">
            {offer.status === "accepted" ? (
              <p className="muted">This offer has already been accepted.</p>
            ) : (
              <form className="inline-form" onSubmit={accept}>
                <Field
                  label="Down-payment reference"
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                  placeholder="e.g. DP-2026-0042"
                  required
                />
                <button className="btn-primary" type="submit" disabled={busy}>
                  {busy ? "Creating contract…" : "Accept & confirm down payment"}
                </button>
              </form>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
