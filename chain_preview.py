# -*- coding: utf-8 -*-
"""
chain_preview.py

תצוגה מקדימה של שרשרת האופציות של מניה אחת, בלי סינון כללים.
על המסך: 10 עמודות עיקריות. בקובץ Excel/CSV: כל 18 עמודות הגיליון שלך,
באותו סדר כותרות, כטבלת Excel עם כפתורי סינון.
אין ציון ואין דירוג. הרשימה ממוינת לפי סטרייק בלבד.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from screener import occ_symbol, export_excel, SHEET_COLUMNS

# מה שמוצג על המסך (נוח לטלפון). הקובץ המיוצא מכיל את כל SHEET_COLUMNS.
DISPLAY_COLUMNS = ["Ticker", "Type", "Strike", "UnderlyingPrice", "Bid", "Ask",
                   "PriceUsed", "Delta", "DTE", "Expiration"]


def _num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return None if n is None else int(n)


def _live_mid(bid, ask):
    b, a = _num(bid), _num(ask)
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return None
    return round((a + b) / 2.0, 2)


def _nearest_expiry(expirations, target: date):
    if not expirations:
        return None
    return min(expirations, key=lambda e: abs((date.fromisoformat(e) - target).days))


def _us_market_open_now(now=None):
    """True/False לפי שעון ניו יורק (ב׳-ו׳ 9:30-16:00), בלי לוח חגים. None אם אין נתוני אזור זמן."""
    try:
        from zoneinfo import ZoneInfo
        n = now or datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return None
    if n.weekday() >= 5:
        return False
    return (9, 30) <= (n.hour, n.minute) < (16, 0)


def build_preview(ticker: str, side: str, price: float, expiry: str, chain: list, *,
                  delta_fn, r: float = 0.045, span: float = 0.5, off_hours=None,
                  today: date | None = None) -> pd.DataFrame:
    """
    שורה לכל סטרייק OTM בטווח, בכל 18 עמודות הגיליון (SHEET_COLUMNS).
    ערכים חסרים נשארים ריקים, לא מומצאים. PriceUsed = Mid רק מציטוט חי.
    """
    side = side.upper()
    today = today or date.today()
    dte = max(1, (date.fromisoformat(expiry) - today).days)
    T = dte / 365.0
    lo, hi = ((1 - span) * price, price) if side == "PUT" else (price, (1 + span) * price)
    basis_is_strike = side == "PUT"
    rows = []
    for c in chain:
        k = _num(c.get("strike"))
        if k is None or not (lo <= k <= hi):
            continue
        iv = _num(c.get("impliedVolatility"))
        delta = None
        if iv and iv > 0:
            try:
                d = delta_fn(side, price, k, T, iv, r)
                delta = None if d is None else round(float(d), 4)
            except Exception:
                delta = None
        mid = _live_mid(c.get("bid"), c.get("ask"))
        basis = k if basis_is_strike else price
        ann = None if mid is None else round(mid / basis * (365.0 / dte) * 100.0, 1)
        rows.append({
            "Ticker": ticker,
            "Type": side,
            "ContractSymbol": occ_symbol(ticker, expiry, k, side),
            "Strike": k,
            "UnderlyingPrice": round(price, 2),
            "Bid": _num(c.get("bid")),
            "Ask": _num(c.get("ask")),
            "LastPrice": _num(c.get("lastPrice")),
            "PriceUsed": mid,
            "Delta": delta,
            "IV_Percent": None if not iv else round(iv * 100.0, 2),
            "DTE": dte,
            "Expiration": expiry,
            "AnnualizedReturn_Percent": ann,
            "RiskFreeRate_Percent": round(r * 100.0, 2),
            "OpenInterest": _int(c.get("openInterest")),
            "Volume": _int(c.get("volume")),
            "IsOffHours": off_hours,
        })
    df = pd.DataFrame(rows, columns=SHEET_COLUMNS)
    if not df.empty:
        df = df.sort_values("Strike", ascending=False).reset_index(drop=True)
    return df


def render_chain_preview(st, tickers: list, target_expiry: date) -> None:
    """ממשק: בחירת מניה וסוג חוזה, כפתור טעינה, טבלה, וכפתורי Excel/CSV עם כל 18 העמודות."""
    import data_yf as dy
    from styles import html_table

    ss = st.session_state
    st.caption("שלב 1 נשאר Ticker + Price. כאן את בוחרת מניה אחת ורואה את החוזים שלה, בלי סינון כללים. "
               "PriceUsed = Mid מציטוט חי. כשהשוק סגור Bid ו-Ask לרוב ריקים או ישנים. "
               "על המסך מוצגות 10 עמודות, והקובץ להורדה כולל את כל 18 עמודות הגיליון.")
    c1, c2 = st.columns(2)
    t_sel = c1.selectbox("מניה", tickers, key="prev_ticker")
    side = c2.radio("סוג חוזה", ["PUT", "CALL"], horizontal=True, key="prev_side")
    if st.button("טעני שרשרת", key="prev_btn"):
        try:
            price = dy.fetch_spot(t_sel)
            exp = _nearest_expiry(dy.fetch_expirations(t_sel), target_expiry)
            if price is None or exp is None:
                ss["chain_prev"] = {"error": f"אין מחיר או תאריכי פקיעה עבור {t_sel}."}
            else:
                chain = dy.fetch_chain(t_sel, exp, side)
                mkt = _us_market_open_now()
                df = build_preview(t_sel, side, float(price), exp, chain, delta_fn=dy.delta_fn,
                                   off_hours=None if mkt is None else (not mkt))
                ss["chain_prev"] = {"df": df, "ticker": t_sel, "side": side, "expiry": exp}
        except Exception as e:
            ss["chain_prev"] = {"error": f"{type(e).__name__}: {e}"}
    prev = ss.get("chain_prev")
    if not prev:
        return
    if prev.get("error"):
        st.warning(prev["error"])
        return
    df = prev["df"]
    st.caption(f"{prev['ticker']} {prev['side']}, פקיעה {prev['expiry']}")
    if df.empty:
        st.info("לא חזרו חוזים בטווח.")
        return
    st.html(html_table(df[DISPLAY_COLUMNS]))
    base = f"chain_{prev['ticker']}_{prev['side']}_{prev['expiry']}"
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ שרשרת ל-Excel (כל העמודות)", data=export_excel(df, sheet_name="Chain"),
                       file_name=f"{base}.xlsx", key="prev_xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d2.download_button("⬇️ שרשרת CSV", data=df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{base}.csv", mime="text/csv", key="prev_csv")
