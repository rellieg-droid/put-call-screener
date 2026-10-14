# -*- coding: utf-8 -*-
"""
data_yf.py

שליפת נתונים מ-yfinance, עם מטמון קצר ורישום שגיאות אמיתיות.

חשוב: כשהשליפה נכשלת, השגיאה האמיתית נשמרת כאן ומוצגת באפליקציה
(חלונית "בדיקת חיבור"), במקום להיבלע בשקט. כך אפשר לראות מה בדיוק נכשל בענן.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pandas as pd

from options_engine import bs_greeks, strike_distance_in_expected_moves
from rules import num

TTL_SECONDS = 300
_cache: dict = {}
_lock = threading.Lock()
_errors: list[str] = []


def _log_error(where: str, exc: Exception) -> None:
    msg = f"{where}: {type(exc).__name__}: {str(exc)[:200]}"
    with _lock:
        if msg not in _errors:
            _errors.append(msg)
        del _errors[:-30]


def recent_errors() -> list[str]:
    with _lock:
        return list(_errors)


def clear_errors() -> None:
    with _lock:
        _errors.clear()


def _cached(key, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL_SECONDS:
            return hit[1]
    val = fn()
    if val is not None and val != []:
        with _lock:
            _cache[key] = (now, val)
    return val


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# ---------------------------------------------------------------- formulas

def delta_fn(side, S, K, T, iv, r):
    return bs_greeks("call" if side == "CALL" else "put", S, K, T, iv, r).delta


def xem_fn(side, S, K, sigma, days):
    from options_engine import expected_move
    move = expected_move(S, sigma, days).move
    if side == "CALL":
        return strike_distance_in_expected_moves(K, S, move)   # (K - S) / move
    return strike_distance_in_expected_moves(S, K, move)       # (S - K) / move


# ---------------------------------------------------------------- fetchers

def fetch_spot(ticker: str):
    def _go():
        import yfinance as yf
        tk = yf.Ticker(ticker)
        try:
            p = num(tk.fast_info["last_price"])
            if p and p > 0:
                return p
        except Exception as e:
            _log_error(f"{ticker} fast_info", e)
        try:
            h = tk.history(period="5d", interval="1d", auto_adjust=True)
            s = h["Close"].dropna()
            return float(s.iloc[-1]) if len(s) else None
        except Exception as e:
            _log_error(f"{ticker} history", e)
            return None
    return _cached(("spot", ticker), _go)


def fetch_expirations(ticker: str) -> list:
    def _go():
        import yfinance as yf
        try:
            return list(yf.Ticker(ticker).options or [])
        except Exception as e:
            _log_error(f"{ticker} options", e)
            return []
    return _cached(("exps", ticker), _go)


def _fetch_both(ticker: str, expiry: str):
    def _go():
        import yfinance as yf
        try:
            oc = yf.Ticker(ticker).option_chain(expiry)
            return {"PUT": oc.puts.to_dict("records"), "CALL": oc.calls.to_dict("records")}
        except Exception as e:
            _log_error(f"{ticker} option_chain {expiry}", e)
            return None
    return _cached(("chain", ticker, expiry), _go)


def fetch_chain(ticker: str, expiry: str, side: str) -> list:
    both = _fetch_both(ticker, expiry)
    return [] if not both else both.get(side.upper(), [])


def fetch_next_earnings(ticker: str):
    """(known, date|None). known=False אם אי אפשר לדעת (נכשל/אין נתון)."""
    def _go():
        try:
            import yfinance as yf
            cal = yf.Ticker(ticker).calendar
            dates = None
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date")
            elif cal is not None and hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
                dates = list(cal.loc["Earnings Date"])
            if not dates:
                return (False, None)
            if not isinstance(dates, (list, tuple)):
                dates = [dates]
            ds = []
            for d in dates:
                if isinstance(d, datetime):
                    ds.append(d.date())
                elif isinstance(d, date):
                    ds.append(d)
                else:
                    try:
                        ds.append(pd.Timestamp(d).date())
                    except Exception:
                        pass
            future = sorted(d for d in ds if d >= date.today())
            return (True, future[0]) if future else (False, None)
        except Exception as e:
            _log_error(f"{ticker} earnings", e)
            return (False, None)
    return _cached(("earn", ticker), _go)


def prewarm(tickers: list[str], target_expiry: date, sides: list[str], want_earnings: bool,
            workers: int = 4, progress=None) -> None:
    """טוען במקביל מחיר, פקיעות ושרשראות למטמון, כדי שהסריקה עצמה תהיה מהירה."""
    from screener import nearest_expiry

    def _one(t):
        p = fetch_spot(t)
        if p is None:
            return
        exp = nearest_expiry(fetch_expirations(t), target_expiry)
        if exp:
            _fetch_both(t, exp)
        if want_earnings:
            fetch_next_earnings(t)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(_one, tickers):
            done += 1
            if progress:
                try:
                    progress(done, len(tickers))
                except Exception:
                    pass


def connection_test(symbol: str = "SPY") -> list[dict]:
    """בדיקה שלב אחר שלב: מחיר, פקיעות, שרשרת. מחזיר [{step, ok, detail}]."""
    steps = []
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        try:
            p = num(tk.fast_info["last_price"])
            steps.append({"step": "מחיר מניה", "ok": bool(p), "detail": f"{p:.2f}" if p else "לא התקבל מחיר"})
        except Exception as e:
            steps.append({"step": "מחיר מניה", "ok": False, "detail": f"{type(e).__name__}: {str(e)[:200]}"})
        exps = []
        try:
            exps = list(tk.options or [])
            steps.append({"step": "תאריכי פקיעה", "ok": bool(exps),
                          "detail": f"{len(exps)} תאריכים" if exps else "רשימה ריקה"})
        except Exception as e:
            steps.append({"step": "תאריכי פקיעה", "ok": False, "detail": f"{type(e).__name__}: {str(e)[:200]}"})
        if exps:
            try:
                oc = tk.option_chain(exps[min(2, len(exps) - 1)])
                steps.append({"step": "שרשרת אופציות", "ok": len(oc.puts) > 0,
                              "detail": f"{len(oc.puts)} PUT, {len(oc.calls)} CALL"})
            except Exception as e:
                steps.append({"step": "שרשרת אופציות", "ok": False,
                              "detail": f"{type(e).__name__}: {str(e)[:200]}"})
    except Exception as e:
        steps.append({"step": "טעינת yfinance", "ok": False, "detail": f"{type(e).__name__}: {str(e)[:200]}"})
    return steps
