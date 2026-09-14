"""HMAC signing for outbound webhooks — this service's half of the shared
scheme retail-credit-api verifies in app/services/webhook_security.py:

    signed_payload = f"{timestamp}.{raw_body}"
    signature      = hex(HMAC-SHA256(secret, signed_payload))
    header         = "X-Gateway-Signature: t=<timestamp>,v1=<signature>"

Implemented independently here (separate codebase) using the same shared
secret (GATEWAY_WEBHOOK_SECRET / webhook_secret, from the environment on
both sides — never committed).
"""
from __future__ import annotations

import hashlib
import hmac
import time


def sign(secret: str, raw_body: bytes) -> str:
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.".encode() + raw_body
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def sign_corrupt(secret: str, raw_body: bytes) -> str:
    """Used only by the `invalid_signature` demo scenario — produces a
    well-formed header whose signature deliberately does not verify, so
    retail-credit-api's REJECTED_SIGNATURE path can be exercised on demand."""
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), b"not-the-real-payload", hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"
