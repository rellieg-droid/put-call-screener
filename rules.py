# -*- coding: utf-8 -*-
"""
rules.py

הכללים של המשתמשת ובדיקת סטרייק מול כל כלל בנפרד, ל-PUT ול-CALL.

עקרונות (זהים לאפליקציה הקיימת):
- בלי ציון משולב, בלי דירוג, בלי BUY/SELL. כל כלל מחזיר pass / fail / unknown.
- "לא ידוע" (חסר נתון) אף פעם לא נחשב מעבר.
- הכללים הם ההגדרות שלה, לא חוקים פיננסיים.
- לוגיקה טהורה: בלי streamlit ובלי yfinance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict

RULES_FILE = "screener_rules.json"
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


@dataclass
class Rules:
    delta_min: float = 0.05             # |Delta| מינימום (0 = כבוי)
    delta_max: float = 0.10             # |Delta| מקסימום (0 = כבוי)
    min_premium: float = 1.0            # פרמיה מינימלית: Mid בדולרים למניה, 1.00 = $100 לחוזה (0 = כבוי)
    min_xem: float = 0.0                # מרחק מינימלי ב-xEM (0 = כבוי)
    min_annual_yield_pct: float = 0.0   # תשואה שנתית מינימלית (0 = כבוי)
    max_spread_pct: float = 20.0        # מרווח bid/ask מקסימלי באחוז מה-Mid (0 = כבוי)
    min_open_interest: int = 50         # Open Interest מינימלי (0 = כבוי)
    check_earnings: bool = True         # לפסול אם יש דוח רווחים עד הפקיעה
    check_exposure: bool = True         # PUT בלבד: לפסול אם החשיפה חורגת מהגבול


def load_rules(path: str = RULES_FILE) -> Rules:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        base = asdict(Rules())
        base.update({k: data[k] for k in base if k in data})
        return Rules(**base)
    except Exception:
        return Rules()


def save_rules(rules: Rules, path: str = RULES_FILE) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(rules), f, ensure_ascii=False)
        return True
    except Exception:
        return False


def num(v):
    """float או None (NaN ונתון לא מספרי הופכים ל-None)."""
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def live_mid(bid, ask):
    """Mid רק אם יש ציטוט חי (bid>0 ו-ask>=bid). אחרת None. אין fallback ל-Last."""
    b, a = num(bid), num(ask)
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return None
    return (a + b) / 2.0


def evaluate_strike(row: dict, rules: Rules) -> dict:
    """row: נתוני סטרייק. מחזיר {rule_name: (status, short_reason)}."""
    out = {}
    d = row.get("delta_abs")
    if rules.delta_min > 0 or rules.delta_max > 0:
        if d is None:
            out["Delta"] = (UNKNOWN, "אין Delta")
        else:
            lo_ok = rules.delta_min <= 0 or d >= rules.delta_min
            hi_ok = rules.delta_max <= 0 or d <= rules.delta_max
            out["Delta"] = (PASS, "") if (lo_ok and hi_ok) else (
                FAIL, f"Delta {d:.4f} מחוץ ל-{rules.delta_min:g}-{rules.delta_max:g}")
    if rules.min_xem > 0:
        x = row.get("xem")
        out["xEM"] = (UNKNOWN, "אין xEM") if x is None else (
            (PASS, "") if x >= rules.min_xem else (FAIL, f"xEM {x:.2f} נמוך מ-{rules.min_xem:g}"))
    mid = row.get("mid")
    if mid is None:
        out["ציטוט חי"] = (FAIL, "אין ציטוט חי (bid/ask)")
    else:
        out["ציטוט חי"] = (PASS, "")
        if rules.min_premium > 0:
            out["פרמיה"] = (PASS, "") if mid >= rules.min_premium else (
                FAIL, f"פרמיה ${mid:.2f} נמוכה מ-${rules.min_premium:g}")
        if rules.min_annual_yield_pct > 0:
            y = row.get("annual_yield_pct")
            out["תשואה"] = (UNKNOWN, "אין תשואה") if y is None else (
                (PASS, "") if y >= rules.min_annual_yield_pct else
                (FAIL, f"תשואה שנתית {y:.1f}% נמוכה מ-{rules.min_annual_yield_pct:g}%"))
        if rules.max_spread_pct > 0:
            s = row.get("spread_pct")
            out["מרווח"] = (UNKNOWN, "אין מרווח") if s is None else (
                (PASS, "") if s <= rules.max_spread_pct else
                (FAIL, f"מרווח {s:.0f}% מעל {rules.max_spread_pct:g}%"))
    if rules.min_open_interest > 0:
        oi = row.get("open_interest")
        out["Open Interest"] = (UNKNOWN, "אין OI") if oi is None else (
            (PASS, "") if oi >= rules.min_open_interest else
            (FAIL, f"OI {int(oi)} נמוך מ-{rules.min_open_interest}"))
    if rules.check_earnings:
        e = row.get("earnings_before_expiry")  # True / False / None
        out["דוחות"] = (UNKNOWN, "תאריך דוח לא ידוע") if e is None else (
            (FAIL, "דוח רווחים לפני הפקיעה") if e else (PASS, ""))
    if rules.check_exposure and row.get("side") == "PUT":
        p = row.get("cum_pct_settled")
        lim = row.get("alloc_limit")
        out["חשיפה"] = (UNKNOWN, "אין Settled Cash בהגדרות") if (p is None or lim is None) else (
            (PASS, "") if p <= lim else (FAIL, f"חשיפה מצטברת {p:.0f}% מעל {lim:g}%"))
    return out


RULE_EXPLAIN = {
    "ציטוט חי": "אין מחיר חי (Bid/Ask) לחוזה, בדרך כלל כי השוק סגור. בלי מחיר אי אפשר לחשב פרמיה, תשואה ומרווח.",
    "Delta": "ההסתברות המשוערת שהאופציה תסתיים בתוך הכסף נמצאת מחוץ לטווח שהגדרת.",
    "xEM": "הסטרייק קרוב למחיר יותר ממה שהגדרת, ביחס לתזוזה הצפויה.",
    "פרמיה": "ה-Mid של האופציה נמוך מהפרמיה המינימלית שהגדרת (בדולרים למניה, כפול 100 לחוזה).",
    "תשואה": "הפרמיה קטנה מדי ביחס להתחייבות, לפי התשואה השנתית המינימלית שהגדרת.",
    "מרווח": "ההפרש בין Bid ל-Ask גדול מדי, וקשה לקבל מחיר הוגן.",
    "Open Interest": "מעט חוזים פתוחים בסטרייק, כלומר נזילות נמוכה.",
    "דוחות": "דוח הרווחים הבא של החברה נופל לפני הפקיעה.",
    "חשיפה": "החוזה הזה, יחד עם ה-PUT הפתוחים שהזנת, חורג מהגבול שהגדרת לחשבון.",
}
