from __future__ import annotations

import calendar
from datetime import date


def add_months(start: date, months: int) -> date:
    """Return `start` shifted by `months`, clamping the day to month length."""
    zero_based = start.month - 1 + months
    year = start.year + zero_based // 12
    month = zero_based % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
