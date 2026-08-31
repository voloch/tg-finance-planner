import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.periods import period_containing, period_for_month, previous_period


def test_simple_first_of_month():
    p = period_containing(date(2026, 8, 15), cycle_day=1)
    assert p.start == date(2026, 8, 1)
    assert p.end == date(2026, 9, 1)


def test_mid_month_cycle_day_before_anchor():
    # cycle day 5, today is the 15th -> period started the 5th of this month
    p = period_containing(date(2026, 8, 15), cycle_day=5)
    assert p.start == date(2026, 8, 5)
    assert p.end == date(2026, 9, 5)


def test_mid_month_cycle_day_after_anchor_rolls_back():
    # cycle day 20, today is the 15th -> period started the 20th of *last* month
    p = period_containing(date(2026, 8, 15), cycle_day=20)
    assert p.start == date(2026, 7, 20)
    assert p.end == date(2026, 8, 20)


def test_cycle_day_31_clamps_in_february_non_leap():
    p = period_containing(date(2026, 2, 20), cycle_day=31)
    assert p.start == date(2026, 1, 31)
    assert p.end == date(2026, 2, 28)  # 2026 is not a leap year


def test_cycle_day_31_clamps_in_february_leap_year():
    p = period_containing(date(2028, 2, 20), cycle_day=31)
    assert p.end == date(2028, 2, 29)  # 2028 is a leap year


def test_previous_period():
    p = period_containing(date(2026, 8, 15), cycle_day=1)
    prev = previous_period(p, cycle_day=1)
    assert prev.start == date(2026, 7, 1)
    assert prev.end == date(2026, 8, 1)


def test_previous_period_across_year_boundary():
    p = period_containing(date(2026, 1, 15), cycle_day=1)
    prev = previous_period(p, cycle_day=1)
    assert prev.start == date(2025, 12, 1)
    assert prev.end == date(2026, 1, 1)


def test_period_for_month():
    p = period_for_month(2026, 8, cycle_day=5)
    assert p.start == date(2026, 8, 5)
    assert p.end == date(2026, 9, 5)
