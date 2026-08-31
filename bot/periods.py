"""Budget-cycle math. A period runs from `cycle_day` of one month up to (but
not including) `cycle_day` of the next, clamped to each month's real length
so cycle_day=31 behaves sanely in February."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta


def _clamp_day(year: int, month: int, day: int) -> int:
    last = calendar.monthrange(year, month)[1]
    return min(max(day, 1), last)


def _add_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _sub_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # exclusive
    label: str


def period_start_containing(anchor: date, cycle_day: int) -> date:
    """The start date of the period that contains `anchor`."""
    day_this_month = _clamp_day(anchor.year, anchor.month, cycle_day)
    if anchor.day >= day_this_month:
        return date(anchor.year, anchor.month, day_this_month)
    py, pm = _sub_month(anchor.year, anchor.month)
    day_prev_month = _clamp_day(py, pm, cycle_day)
    return date(py, pm, day_prev_month)


def _label(start: date, end: date) -> str:
    last_day = end - timedelta(days=1)
    return f"{start.strftime('%d/%m')}–{last_day.strftime('%d/%m/%Y')}"


def period_containing(anchor: date, cycle_day: int) -> Period:
    start = period_start_containing(anchor, cycle_day)
    ny, nm = _add_month(start.year, start.month)
    end = date(ny, nm, _clamp_day(ny, nm, cycle_day))
    return Period(start=start, end=end, label=_label(start, end))


def previous_period(period: Period, cycle_day: int) -> Period:
    day_before = period.start - timedelta(days=1)
    return period_containing(day_before, cycle_day)


def period_for_month(year: int, month: int, cycle_day: int) -> Period:
    """The period that starts within calendar month `year`-`month`."""
    start = date(year, month, _clamp_day(year, month, cycle_day))
    ny, nm = _add_month(year, month)
    end = date(ny, nm, _clamp_day(ny, nm, cycle_day))
    return Period(start=start, end=end, label=_label(start, end))
