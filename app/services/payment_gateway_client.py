"""Retail Credit's outbound client to the separate mock-payment-gateway service.

Server-to-server calls only. This module never touches the gateway's own
database — the gateway is a genuinely separate service (see
mock-payment-gateway/) reached over HTTP, exactly like a real external
payment provider would be.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.core.config import get_settings


class GatewayClientError(Exception):
    """The gateway call failed (network error, non-2xx, or a malformed
    response) — the caller decides how to surface this (e.g. leave the
    PaymentIntent at INITIATED rather than PENDING)."""


@dataclass
class CheckoutSessionResult:
    token: str
    checkout_url: str
    expires_at: str


def create_checkout_session(
    *,
    payment_reference: str,
    amount: Decimal,
    currency: str,
    customer_name: str,
    contract_reference: str,
    description: str,
    return_url: str,
) -> CheckoutSessionResult:
    """Ask the gateway to open a hosted checkout session for this payment.

    ``customer_name`` is sent in full — the *gateway's own checkout page* is
    responsible for masking it for display (section 5: "Customer name in
    masked format"); this app never displays or stores a masked copy itself.
    """
    settings = get_settings()
    body = {
        "merchant_reference": payment_reference,
        "amount": str(amount),
        "currency": currency,
        "customer_name": customer_name,
        "contract_reference": contract_reference,
        "description": description,
        "return_url": return_url,
        "webhook_url": settings.gateway_webhook_receive_url,
    }
    try:
        resp = httpx.post(
            f"{settings.gateway_base_url}/gateway/checkout-sessions",
            json=body,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return CheckoutSessionResult(
            token=data["token"],
            checkout_url=data["checkout_url"],
            expires_at=data["expires_at"],
        )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise GatewayClientError(f"mock-payment-gateway checkout-session call failed: {exc}") from exc


def fetch_transaction(gateway_transaction_reference: str) -> dict | None:
    """Ground-truth lookup used by webhook-retry/dead-letter reprocessing —
    re-query the gateway rather than trust a stored payload blob (this
    application never persists the raw webhook body, only its hash)."""
    settings = get_settings()
    try:
        resp = httpx.get(
            f"{settings.gateway_base_url}/gateway/transactions/{gateway_transaction_reference}",
            timeout=10.0,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise GatewayClientError(f"mock-payment-gateway transaction lookup failed: {exc}") from exc
