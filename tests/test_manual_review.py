"""P0-2 — POST /applications/{id}/review (referred -> manual verification)."""
from tests.helpers import make_application, make_customer, make_product


def _referred_application(client, national_id):
    customer = make_customer(client, national_id=national_id, risk_score=620,
                             monthly_income=5000, existing_obligations=100)
    product = make_product(client)
    app = make_application(client, customer["id"], product["id"],
                           requested_amount=1200, requested_tenor_months=12)
    submitted = client.post(f"/applications/{app['id']}/submit").json()
    assert submitted["status"] == "referred", submitted
    return customer, product, submitted


def test_credit_officer_approves_referred_then_it_proceeds_to_offer(client, client_as):
    _, _, app = _referred_application(client, "MR-APR")

    r = client_as("credit_officer").post(
        f"/applications/{app['id']}/review",
        json={"decision": "approved", "reason": "verified payslip in branch"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["latest_assessment"]["source"] == "manual"
    assert body["latest_assessment"]["notes"] == "verified payslip in branch"
    assert body["latest_assessment"]["reviewed_by"] is not None
    # the original automated 'referred' assessment is still on record
    sources = [a["source"] for a in body["assessments"]]
    assert sources == ["automated", "manual"]

    # proceeds through offer generation exactly like an auto-approval
    offer = client.post(f"/applications/{app['id']}/offer",
                        json={"down_payment_amount": 300})
    assert offer.status_code == 201, offer.text


def test_referred_can_be_rejected(client, client_as):
    _, _, app = _referred_application(client, "MR-REJ")
    r = client_as("credit_manager").post(
        f"/applications/{app['id']}/review",
        json={"decision": "rejected", "reason": "insufficient income evidence"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_return_for_info_goes_to_draft_and_can_be_resubmitted(client, client_as):
    _, _, app = _referred_application(client, "MR-RFI")
    r = client_as("credit_officer").post(
        f"/applications/{app['id']}/review",
        json={"decision": "return_for_info", "reason": "need a stamped employer letter"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "draft"

    # resubmit through the unchanged submit flow
    resubmitted = client.post(f"/applications/{app['id']}/submit")
    assert resubmitted.status_code == 200
    assert resubmitted.json()["status"] in {"approved", "rejected", "referred"}


def test_reviewing_a_non_referred_application_is_409(client, client_as):
    customer = make_customer(client, national_id="MR-409", risk_score=720,
                             monthly_income=5000, existing_obligations=100)
    product = make_product(client)

    approved = make_application(client, customer["id"], product["id"])
    assert client.post(f"/applications/{approved['id']}/submit").json()["status"] == "approved"
    r1 = client_as("credit_officer").post(
        f"/applications/{approved['id']}/review",
        json={"decision": "approved", "reason": "x"})
    assert r1.status_code == 409

    draft = make_application(client, customer["id"], product["id"])  # never submitted
    r2 = client_as("credit_officer").post(
        f"/applications/{draft['id']}/review",
        json={"decision": "approved", "reason": "x"})
    assert r2.status_code == 409


def test_sales_employee_cannot_review(client, client_as):
    _, _, app = _referred_application(client, "MR-RBAC")
    r = client_as("sales_employee").post(
        f"/applications/{app['id']}/review",
        json={"decision": "approved", "reason": "x"})
    assert r.status_code == 403


def test_review_writes_an_audit_event(client, client_as, db):
    from app.models.audit import AuditEvent

    _, _, app = _referred_application(client, "MR-AUD")
    client_as("credit_manager").post(
        f"/applications/{app['id']}/review",
        json={"decision": "approved", "reason": "manager override"})

    evs = db.query(AuditEvent).filter_by(
        action="application.reviewed", entity_id=str(app["id"])).all()
    assert len(evs) == 1
    assert evs[0].before_value == {"status": "referred"}
    assert evs[0].after_value["decision"] == "approved"
    assert evs[0].after_value["reason"] == "manager override"
