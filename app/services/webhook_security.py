"""HMAC signing/verification for gateway <-> Retail Credit webhooks (section 10).

Scheme (Stripe-style, deliberately simple and easy to demo/explain):

    signed_payload = f"{timestamp}.{raw_body}"
    signature      = hex(HMAC-SHA256(secret, signed_payload))
    header         = "X-Gateway-Signature: t=<timestamp>,v1=<signature>"

The mock-payment-gateway service implements the *signing* half of this exact
same scheme independently (it is a separate codebase — see
mock-payment-gateway/app/security.py) using the same shared secret
(``GATEWAY_WEBHOOK_SECRET`` on both sides, from the environment — never
committed). Folding the timestamp into the signed material is what makes
replay protection possible: an attacker who captures one valid signed request
cannot change the timestamp without invalidating the signature.
"""
from __future__ import annotations

import hashlib
import hmac
import time


class SignatureHeaderError(ValueError):
    """The X-Gateway-Signature header is missing or malformed."""


def compute_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    signed_payload = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


def parse_signature_header(header_value: str) -> tuple[str, str]:
    """``"t=1699999999,v1=abcdef..."`` -> ``("1699999999", "abcdef...")``."""
    parts: dict[str, str] = {}
    for item in header_value.split(","):
        if "=" not in item:
            continue
        k, _, v = item.partition("=")
        parts[k.strip()] = v.strip()
    if "t" not in parts or "v1" not in parts:
        raise SignatureHeaderError("X-Gateway-Signature must contain t= and v1=")
    return parts["t"], parts["v1"]


def verify_signature(
    *, secret: str, header_value: str | None, raw_body: bytes
) -> tuple[bool, str, int | None]:
    """Signature math only — deliberately separate from staleness (see
    ``is_stale`` below), so a caller can tell REJECTED_SIGNATURE (HMAC didn't
    match — the payload/header was tampered with or the secret is wrong) apart
    from REJECTED_STALE (HMAC matched fine, but the timestamp is outside the
    replay window) — two different WebhookProcessingStatus outcomes.

    Returns ``(valid, reason, timestamp)``; ``timestamp`` is the parsed
    ``t=`` value (None if the header couldn't even be parsed), so the caller
    can still run the staleness check on a *malformed* signature for a
    complete audit trail.
    """
    if not header_value:
        return False, "missing X-Gateway-Signature header", None
    try:
        timestamp, signature = parse_signature_header(header_value)
    except SignatureHeaderError as exc:
        return False, str(exc), None

    try:
        ts = int(timestamp)
    except ValueError:
        return False, "timestamp in signature header is not an integer", None

    expected = compute_signature(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, signature):
        return False, "signature does not match", ts

    return True, "ok", ts


def is_stale(timestamp: int | None, *, max_age_seconds: int) -> bool:
    if timestamp is None:
        return True
    return abs(time.time() - timestamp) > max_age_seconds


def payload_hash(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()
