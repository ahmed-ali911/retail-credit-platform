"""Mock ERP / General-Ledger adapter — the outbound posting boundary.

Same pattern as every other external system in this platform (payment gateway,
bank feed): a narrow interface with a mock implementation that always succeeds,
structured so a real ERP client is a drop-in replacement later.

Explicitly **not** built here (deferred until a real ERP is connected, per the
assessment's "don't over-engineer for mocks" principle): retry/backoff,
circuit-breaker, dead-letter queue, signature verification. The posting *job*
(`POST /jobs/post-accounting-events`) records `failed` + `retry_count` and is
safe to re-run; that is the whole recovery story for now.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.models.accounting import AccountingEvent


@dataclass
class PostResult:
    ok: bool
    external_gl_reference: str | None = None
    error_message: str | None = None


class GlProvider:
    """Interface a real ERP client must implement."""

    def post_event(self, event: AccountingEvent) -> PostResult:  # pragma: no cover
        raise NotImplementedError


class MockGlProvider(GlProvider):
    """Always accepts the event and returns a fake GL reference."""

    def post_event(self, event: AccountingEvent) -> PostResult:
        return PostResult(
            ok=True,
            external_gl_reference=f"MOCK-GL-{uuid.uuid4()}",
        )


# The single provider instance the posting job uses. Swapping in a real client
# later is a one-line change here (or dependency-injection if it needs config).
_provider: GlProvider = MockGlProvider()


def post_event(event: AccountingEvent) -> PostResult:
    return _provider.post_event(event)
