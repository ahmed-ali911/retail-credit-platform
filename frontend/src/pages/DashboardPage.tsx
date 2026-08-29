import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "../components/ui";

export function DashboardPage() {
  const navigate = useNavigate();
  const [appId, setAppId] = useState("");
  const [offerId, setOfferId] = useState("");
  const [contractId, setContractId] = useState("");

  return (
    <div className="stack">
      <h1>Dashboard</h1>

      <Card title="Start a new flow">
        <p className="muted">
          Create the records you need, then run an application through assessment,
          turn an approval into an offer, and accept it into a contract.
        </p>
        <div className="stack" style={{ marginTop: "0.75rem" }}>
          <button className="btn-primary" onClick={() => navigate("/customers/new")}>
            Create customer
          </button>{" "}
          <button className="btn-secondary" onClick={() => navigate("/products/new")}>
            Create product
          </button>{" "}
          <button className="btn-secondary" onClick={() => navigate("/applications/new")}>
            New application
          </button>
        </div>
      </Card>

      <Card title="Open an existing record" soft>
        <p className="muted">
          The backend has no list endpoints yet — open records by id.
        </p>
        <div className="inline-form">
          <label className="field">
            <span>Application #</span>
            <input
              inputMode="numeric"
              value={appId}
              onChange={(e) => setAppId(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary"
            disabled={!appId}
            onClick={() => navigate(`/applications/${appId}/offer`)}
          >
            Open → offer
          </button>
        </div>
        <div className="inline-form" style={{ marginTop: "0.75rem" }}>
          <label className="field">
            <span>Offer #</span>
            <input
              inputMode="numeric"
              value={offerId}
              onChange={(e) => setOfferId(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary"
            disabled={!offerId}
            onClick={() => navigate(`/offers/${offerId}`)}
          >
            Open offer
          </button>
        </div>
        <div className="inline-form" style={{ marginTop: "0.75rem" }}>
          <label className="field">
            <span>Contract #</span>
            <input
              inputMode="numeric"
              value={contractId}
              onChange={(e) => setContractId(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary"
            disabled={!contractId}
            onClick={() => navigate(`/contracts/${contractId}`)}
          >
            Open contract
          </button>
        </div>
      </Card>
    </div>
  );
}
