# -*- coding: utf-8 -*-
"""
market_calendar.py

לוח חגים ושעות של הבורסה בארה"ב (NYSE / Nasdaq), מחושב בכללים, בלי רשת ובלי קובץ נתונים.
- חגים: ראש השנה, MLK, יום הנשיאים, שישי הטוב, Memorial, Juneteenth, 4 ביולי, Labor, Thanksgiving, חג המולד.
- חג שנופל בשבת נחגג ביום שישי, בראשון נחגג ביום שני (חוץ מראש השנה בשבת: אין סגירה ב-31 בדצמבר).
- סגירה מוקדמת 13:00: יום אחרי Thanksgiving, 24 בדצמבר, 3 ביולי (כשהם ימי חול ובורסה פתוחה).
אין כאן חגי ישראל: הסורק בודק רק את שוק ארה"ב.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

NY = "America/New_York"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _observed(d: date) -> date | None:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_holidays(year: int) -> dict[date, str]:
    """{תאריך: שם החג} של ימים שבהם הבורסה סגורה."""
    out: dict[date, str] = {}

    def add(d, name):
        if d is not None and d.year == year:
            out[d] = name

    ny = date(year, 1, 1)
    if ny.weekday() != 5:              # ראש שנה בשבת: אין סגירה ביום שישי
        add(_observed(ny), "New Year's Day")
    add(_nth_weekday(year, 1, 0, 3), "Martin Luther King Jr. Day")
    add(_nth_weekday(year, 2, 0, 3), "Presidents' Day")
    add(_easter(year) - timedelta(days=2), "Good Friday")
    add(_last_weekday(year, 5, 0), "Memorial Day")
    add(_observed(date(year, 6, 19)), "Juneteenth")
    add(_observed(date(year, 7, 4)), "Independence Day")
    add(_nth_weekday(year, 9, 0, 1), "Labor Day")
    add(_nth_weekday(year, 11, 3, 4), "Thanksgiving")
    add(_observed(date(year, 12, 25)), "Christmas")
    return out


def holiday_name(d: date) -> str | None:
    return us_holidays(d.year).get(d)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and holiday_name(d) is None


def close_time(d: date) -> time:
    """13:00 בימי סגירה מוקדמת, אחרת 16:00 (שעון ניו יורק)."""
    if not is_trading_day(d):
        return time(16, 0)
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return time(13, 0)
    if d.month == 12 and d.day == 24:
        return time(13, 0)
    if d.month == 7 and d.day == 3 and date(d.year, 7, 4).weekday() < 5:
        return time(13, 0)
    return time(16, 0)


def _ny_now(now: datetime | None) -> datetime:
    from zoneinfo import ZoneInfo
    ny = ZoneInfo(NY)
    if now is None:
        return datetime.now(ny)
    if now.tzinfo is None:             # זמן בלי אזור נחשב כשעון ניו יורק
        return now.replace(tzinfo=ny)
    return now.astimezone(ny)


def us_market_open(now: datetime | None = None) -> bool | None:
    """True אם הבורסה בארה"ב פתוחה כרגע. None אם אין נתוני אזור זמן."""
    try:
        n = _ny_now(now)
    except Exception:
        return None
    d = n.date()
    if not is_trading_day(d):
        return False
    return time(9, 30) <= n.time().replace(second=0, microsecond=0) < close_time(d)


def next_session(now: datetime | None = None):
    """(תאריך, פתיחה, סגירה) בשעון ניו יורק של הסשן הנוכחי או הבא, ו-True אם הוא היום."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo(NY)
    n = _ny_now(now)
    d = n.date()
    while not is_trading_day(d) or (d == n.date() and n >= datetime.combine(d, close_time(d), tzinfo=ny)):
        d += timedelta(days=1)
    return (datetime.combine(d, time(9, 30), tzinfo=ny),
            datetime.combine(d, close_time(d), tzinfo=ny),
            d == n.date())
