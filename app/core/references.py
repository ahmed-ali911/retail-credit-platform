"""Human-readable reference codes — computed, never stored.

A reference code is an entity's existing integer primary key rendered with a
two-letter type prefix and zero-padding, e.g. contract 12 -> ``CN-000012``.

There is **no** separate sequence, column, migration or backfill: the primary
key already IS the sequential number, and a code is just that number formatted.
Codes are added to API responses at serialisation time (see the ``*Out``
schemas); the numeric ``id`` field is kept alongside because internal code and
tests still use it.

Any new entity that should carry a code must be added to
``REFERENCE_PREFIXES`` here — keep the prefixes short, uppercase and unique.
"""
from __future__ import annotations

# entity class name -> code prefix
REFERENCE_PREFIXES: dict[str, str] = {
    "Customer": "CU",
    "Product": "PR",
    "CreditApplication": "AP",
    "InstallmentOffer": "OF",
    "SalesOrder": "SO",
    "InstallmentContract": "CN",
    "Payment": "PY",
    "CollectionCase": "CC",
}

_PAD = 6


def format_reference(entity_type: str, id: int) -> str:
    """``format_reference("InstallmentContract", 12) -> "CN-000012"``."""
    try:
        prefix = REFERENCE_PREFIXES[entity_type]
    except KeyError as exc:
        raise ValueError(
            f"no reference-code prefix registered for entity type {entity_type!r}"
        ) from exc
    return f"{prefix}-{int(id):0{_PAD}d}"


def parse_reference(code: str) -> tuple[str, int] | None:
    """Reverse of ``format_reference``: ``"CN-000012" -> ("InstallmentContract", 12)``.

    Returns ``None`` if the string isn't a recognised reference code.
    """
    if not isinstance(code, str) or "-" not in code:
        return None
    prefix, _, num = code.strip().upper().partition("-")
    for entity_type, p in REFERENCE_PREFIXES.items():
        if p == prefix and num.isdigit():
            return entity_type, int(num)
    return None
