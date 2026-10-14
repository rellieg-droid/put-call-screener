"""universe_screener.py

Pre-filter a stock universe (S&P 500, Nasdaq 100) down to tickers whose price
makes a cash-secured PUT affordable under the user's own allocation limit.

This module only narrows a list. It does not rank, score or recommend anything.

Pure functions (testable offline):
    normalize_ticker, parse_index_tables, affordable_max_strike,
    price_band, filter_by_price, merge_universes
Network functions (run on the user's machine):
    fetch_index_tickers, fetch_last_prices
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from io import StringIO

CACHE_FILE = "riskshield_universe_cache.json"
CACHE_MAX_AGE_DAYS = 7

INDEX_SOURCES = {
    "S&P 500": {
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "column": "Symbol",
        "fallbacks": [("https://stockanalysis.com/list/sp-500-stocks/", "Symbol")],
    },
    "Nasdaq 100": {
        "url": "https://en.wikipedia.org/wiki/Nasdaq-100",
        "column": "Ticker",
        # Wikipedia moved the constituents table off this page; the fallback has the full list.
        "fallbacks": [("https://stockanalysis.com/list/nasdaq-100-stocks/", "Symbol")],
    },
}

_UA = {"User-Agent": "Mozilla/5.0 (RiskShield universe screener)"}


# ---------------------------------------------------------------- pure helpers
def normalize_ticker(sym) -> str:
    """'BRK.B' -> 'BRK-B' (the form yfinance expects). Empty string if unusable."""
    if sym is None:
        return ""
    s = str(sym).strip().upper().replace(".", "-")
    return s if s and s != "NAN" else ""


def parse_index_tables(tables: list, column: str) -> list[str]:
    """Among the tables that have `column` (also as the last level of a multi-level header),
    return the normalized tickers of the longest one. The longest is the constituents table;
    other tables on the page (recent changes) have the same column but few rows."""
    best: list[str] = []
    for df in tables:
        cols = []
        for c in getattr(df, "columns", []):
            last = c[-1] if isinstance(c, tuple) else c
            cols.append(str(last).strip())
        if column not in cols:
            continue
        raw = df[df.columns[cols.index(column)]].tolist()
        out, seen = [], set()
        for sym in raw:
            t = normalize_ticker(sym)
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        if len(out) > len(best):
            best = out
    return best


def affordable_max_strike(settled_cash: float, max_alloc_pct: float, contracts: int = 1) -> float:
    """Highest strike whose cash requirement fits the user's allocation limit."""
    if settled_cash <= 0 or max_alloc_pct <= 0 or contracts <= 0:
        return 0.0
    return settled_cash * (max_alloc_pct / 100.0) / (100.0 * contracts)


def price_band(max_strike: float, max_otm_pct: float = 15.0,
               min_price: float = 5.0) -> tuple[float, float]:
    """Stock-price range where some strike up to `max_otm_pct` below the price
    could still fit under `max_strike`. Upper bound = max_strike / (1 - otm)."""
    if max_strike <= 0 or not (0 <= max_otm_pct < 100):
        return (min_price, 0.0)
    return (min_price, max_strike / (1.0 - max_otm_pct / 100.0))


def filter_by_price(prices: dict, min_price: float, max_price: float) -> list[tuple[str, float]]:
    """Keep tickers with min_price <= price <= max_price. Sorted by ticker."""
    out = []
    for t, p in prices.items():
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        if p != p:  # NaN
            continue
        if min_price <= p <= max_price:
            out.append((t, round(p, 2)))
    return sorted(out)


def merge_universes(*lists: list[str]) -> list[str]:
    """Union of several ticker lists, de-duplicated, order preserved."""
    seen, out = set(), []
    for lst in lists:
        for t in lst:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


# ---------------------------------------------------------------- cache
def _load_cache(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass  # cache is a convenience, never fatal


# ---------------------------------------------------------------- network
def fetch_index_tickers(index_name: str, cache_path: str = CACHE_FILE,
                        max_age_days: int = CACHE_MAX_AGE_DAYS,
                        today: date | None = None) -> tuple[list[str], str]:
    """Return (tickers, source) where source is 'cache', 'web' or 'stale-cache'.
    Raises ValueError for an unknown index or when nothing is available."""
    if index_name not in INDEX_SOURCES:
        raise ValueError(f"אינדקס לא מוכר: {index_name}")
    today = today or date.today()
    cache = _load_cache(cache_path)
    entry = cache.get(index_name) or {}

    def _age(e):
        try:
            return (today - datetime.strptime(e["date"], "%Y-%m-%d").date()).days
        except (KeyError, ValueError, TypeError):
            return None

    age = _age(entry)
    if entry.get("tickers") and age is not None and age <= max_age_days:
        return list(entry["tickers"]), "cache"

    import pandas as pd
    import requests
    src = INDEX_SOURCES[index_name]
    attempts = [(src["url"], src["column"])] + list(src.get("fallbacks", []))
    errors = []
    for url, column in attempts:
        try:
            resp = requests.get(url, headers=_UA, timeout=20)
            resp.raise_for_status()
            tickers = parse_index_tables(pd.read_html(StringIO(resp.text)), column)
            if len(tickers) < 20:
                raise ValueError("הרשימה שחזרה קצרה מדי")
            cache[index_name] = {"date": today.strftime("%Y-%m-%d"), "tickers": tickers}
            _save_cache(cache_path, cache)
            return tickers, "web"
        except Exception as exc:
            host = url.split("/")[2]
            errors.append(f"{host}: {type(exc).__name__}: {exc}")
    if entry.get("tickers"):
        return list(entry["tickers"]), "stale-cache"
    raise ValueError(f"לא הצלחתי להביא את רשימת {index_name} (אין עותק שמור). " + " | ".join(errors))


def fetch_last_prices(tickers: list[str], chunk: int = 100) -> dict[str, float]:
    """Last close per ticker via yfinance, in chunks. Missing tickers are skipped."""
    import yfinance as yf

    prices: dict[str, float] = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            df = yf.download(part, period="5d", interval="1d", auto_adjust=True,
                             progress=False, threads=True, group_by="column")
        except Exception:
            continue
        if df is None or df.empty or "Close" not in df:
            continue
        close = df["Close"]
        if getattr(close, "ndim", 1) == 1:  # single ticker comes back as a Series
            s = close.dropna()
            if len(s):
                prices[part[0]] = float(s.iloc[-1])
            continue
        for t in close.columns:
            s = close[t].dropna()
            if len(s):
                prices[str(t)] = float(s.iloc[-1])
    return prices
