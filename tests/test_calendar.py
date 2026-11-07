# -*- coding: utf-8 -*-
"""בדיקות לוח החגים של שוק ארה"ב (ללא רשת)."""
import os
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import market_calendar as mc

NY = ZoneInfo("America/New_York")


def test_2026_holidays():
    h = mc.us_holidays(2026)
    assert date(2026, 7, 3) in h            # 4 ביולי בשבת, נחגג בשישי
    assert date(2026, 11, 26) in h          # Thanksgiving
    assert date(2026, 4, 3) in h            # Good Friday
    assert date(2026, 12, 25) in h
    assert date(2026, 12, 31) not in h


def test_new_year_on_saturday_has_no_friday_closure():
    assert date(2027, 12, 31) not in mc.us_holidays(2027)
    assert date(2028, 1, 1) not in mc.us_holidays(2028)


def test_christmas_2027_observed_friday():
    assert date(2027, 12, 24) in mc.us_holidays(2027)


def test_early_closes():
    assert mc.close_time(date(2026, 11, 27)) == time(13, 0)
    assert mc.close_time(date(2026, 12, 24)) == time(13, 0)
    assert mc.close_time(date(2028, 7, 3)) == time(13, 0)
    assert mc.close_time(date(2026, 11, 20)) == time(16, 0)


def test_open_closed():
    assert mc.us_market_open(datetime(2026, 11, 9, 10, 0, tzinfo=NY)) is True
    assert mc.us_market_open(datetime(2026, 11, 9, 16, 0, tzinfo=NY)) is False
    assert mc.us_market_open(datetime(2026, 11, 26, 11, 0, tzinfo=NY)) is False   # Thanksgiving
    assert mc.us_market_open(datetime(2026, 11, 27, 13, 0, tzinfo=NY)) is False   # סגירה מוקדמת
    assert mc.us_market_open(datetime(2026, 11, 28, 11, 0, tzinfo=NY)) is False   # שבת


def test_next_session_skips_holiday_and_weekend():
    o, c, today = mc.next_session(datetime(2026, 11, 25, 17, 0, tzinfo=NY))
    assert o.date() == date(2026, 11, 27) and c.hour == 13 and today is False
