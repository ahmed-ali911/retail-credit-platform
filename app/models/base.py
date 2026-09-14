from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def created_at_column():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def by_value(enum_cls: type[enum.Enum]) -> list[str]:
    """Pass as ``values_callable`` to every ``sqlalchemy.Enum(SomeEnum, ...)``
    column whose Python enum has SCREAMING_CASE *values* (an external/API
    contract, e.g. ``"SETTLED"``) but lowercase Pythonic *names* (``settled``).

    SQLAlchemy's ``Enum`` type persists/reads by member **name** unless told
    otherwise — harmless for every enum in this codebase where name == value,
    but silently wrong for one where they differ: a value written via
    ``server_default`` or a raw-SQL migration (which naturally use the enum's
    *value*) round-trips back through the ORM as a ``LookupError``. Confirmed
    live in the ECL module (status columns) — a fresh, always-empty test DB
    never hits this, since every row it creates goes through the ORM's own
    (self-consistent) write path; it only surfaces once real data exists that
    predates the column, e.g. after migrating a populated database. See
    ``app/models/ecl.py``'s ``_by_value`` (kept there for that module's own
    history) — this is the same helper, promoted here so every *new* enum
    with a name/value split uses it from the start instead of rediscovering
    the bug.
    """
    return [e.value for e in enum_cls]
