# -*- coding: utf-8 -*-
"""
otm_tab.py

טאב "הסתברות OTM" לסורק PUT / CALL: בחירת מניה, טבלת השוואה לפי סטרייק (PUT),
גרף היסטוריה (נרות, ממוצעים נעים 20/50/200) וטבלת סיכום מול SPY.
הגרף והסטטיסטיקות מועתקים מ-riskshield_tab.py של Stock_tracking, וההסתברויות מ-options_engine.
אין ציון, אין דירוג ואין המלצה. הרשימה ממוינת לפי סטרייק.
"""
from __future__ import annotations

from datetime import date as _date, datetime
from typing import Optional

import streamlit as st

# ---------------------------------------------------------------------------
# גרף והיסטוריה (הועתק מ-riskshield_tab.py)
# ---------------------------------------------------------------------------
# גודל נר בטווחים קצרים: (interval, period) של yfinance. תוך-יום זמין רק לתקופה מוגבלת בעבר.
_HIST_INTRA = {"דקה": ("1m", "7d"), "5 דק'": ("5m", "60d"), "15 דק'": ("15m", "60d"),
               "30 דק'": ("30m", "60d"), "שעה": ("1h", "730d")}
_HIST_RANGES = ["יום", "שבוע", "חודש", "שנה", "שנתיים", "5 שנים", "10 שנים", "מקסימום"]
_HIST_SPAN_DAYS = {"חודש": 31, "שנה": 365, "שנתיים": 730, "5 שנים": 1825, "10 שנים": 3650, "מקסימום": None}
_HIST_INTRA_DAYS = {"יום": 1, "שבוע": 5, "חודש": 21}
_HIST_BARS = {"יום": ["דקה", "5 דק'", "15 דק'"], "שבוע": ["5 דק'", "15 דק'", "30 דק'", "שעה"],
              "חודש": ["30 דק'", "שעה", "יומי"]}
_HIST_BAR_DEFAULT = {"יום": "5 דק'", "שבוע": "30 דק'", "חודש": "שעה", "שנה": "יומי", "שנתיים": "יומי",
                     "5 שנים": "שבועי", "10 שנים": "שבועי", "מקסימום": "חודשי"}


@st.cache_data(ttl=3600, show_spinner=False)
def _try_fetch_price_history(ticker: str):
    """היסטוריה יומית מלאה (Open/High/Low/Close, מתואמת לדיבידנדים) כ-DataFrame. None אם אין נתון."""
    if not ticker:
        return None
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="max", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 30:
            return None
        df = hist[["Open", "High", "Low", "Close"]].dropna(subset=["Close"]).copy()
        try:
            df.index = df.index.tz_localize(None)
        except Exception:
            pass
        return df
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _try_fetch_intraday(ticker: str, interval: str, period: str):
    """נרות תוך-יום (שעון ניו יורק, שעות מסחר רגילות). None אם אין נתון."""
    if not ticker:
        return None
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True, prepost=False)
        if hist is None or hist.empty or len(hist) < 10:
            return None
        df = hist[["Open", "High", "Low", "Close"]].dropna(subset=["Close"]).copy()
        try:
            df.index = df.index.tz_localize(None)
        except Exception:
            pass
        return df
    except Exception:
        return None


def _resample_ohlc(df, how: str):
    """יומי / שבועי / חודשי. מקבץ נרות כדי שגרף ארוך יישאר קריא."""
    if how == "יומי":
        return df
    rule = "W" if how == "שבועי" else "ME"
    try:
        g = df.resample(rule)
    except ValueError:  # pandas ישן שלא מכיר ME
        g = df.resample("W" if how == "שבועי" else "M")
    return g.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()


def _history_frames(daily, intra, rng: str, bar: str, mas=(20, 50, 200)) -> dict:
    """בוחרת מה לצייר לפי טווח וגודל נר. פונקציה טהורה (בלי רשת).
    daily: היסטוריה יומית. intra: נרות תוך-יום (או None). מחזירה:
    win (חלון הטווח), bars (הנרות לציור), ma (קווי הממוצע לחלון), is_intra, tbl (סגירות יומיות לטבלת העובדות)."""
    import pandas as pd
    is_intra = rng in ("יום", "שבוע") or (rng == "חודש" and bar != "יומי")
    if is_intra:
        base = intra
        days = sorted(set(base.index.normalize()))
        n = _HIST_INTRA_DAYS[rng]
        cut = days[-n] if len(days) >= n else days[0]
        win = base[base.index >= cut]
        bars = win
    else:
        base = daily
        span = _HIST_SPAN_DAYS[rng]
        win = base if span is None else base[base.index >= base.index[-1] - pd.Timedelta(days=span)]
        if len(win) < 5:
            win = base
        bars = _resample_ohlc(win, bar)
    ma = {}
    for n in mas:
        m = base["Close"].rolling(n).mean()
        ma[n] = m[m.index >= win.index[0]].dropna()
    short = rng in ("יום", "שבוע", "חודש")
    tbl = daily["Close"][daily.index >= daily.index[-1] - pd.Timedelta(days=365)] if short else win["Close"]
    return {"win": win, "bars": bars, "ma": ma, "is_intra": is_intra, "tbl": tbl, "short": short}


def _history_stats(closes) -> dict:
    """מדדי עבר פשוטים מסדרת סגירות יומית. פונקציה טהורה (בלי רשת)."""
    import numpy as np
    c = closes.astype(float)
    r = c.pct_change().dropna()
    lr = np.log(c / c.shift(1)).dropna()
    yr = c.iloc[-252:]
    dd = c / c.cummax() - 1.0
    w5 = c.pct_change(5).dropna()
    last = float(c.iloc[-1])

    def _ma(n):
        return float(c.iloc[-n:].mean()) if len(c) >= n else None

    return {
        "last": last,
        "hi52": float(yr.max()),
        "lo52": float(yr.min()),
        "from_hi52": last / float(yr.max()) - 1.0,
        "max_dd": float(dd.min()),
        "max_dd_date": dd.idxmin(),
        "worst_1d": float(r.min()),
        "worst_1d_date": r.idxmin(),
        "worst_5d": float(w5.min()) if len(w5) else None,
        "days_down10": int((r <= -0.10).sum()),
        "days_down5": int((r <= -0.05).sum()),
        "rv30": float(lr.iloc[-30:].std() * (252 ** 0.5)) if len(lr) >= 20 else None,
        "rv1y": float(lr.iloc[-252:].std() * (252 ** 0.5)) if len(lr) >= 60 else None,
        "total_return": last / float(c.iloc[0]) - 1.0,
        "ma20": _ma(20),
        "ma50": _ma(50),
        "ma200": _ma(200),
        "n_days": int(len(c)),
        "start": c.index[0],
        "end": c.index[-1],
    }


def _history_table(ticker: str, st_t: dict, st_b: Optional[dict]):
    """טבלת השוואה: המניה מול SPY. מחזיר DataFrame. סימן הדולר נכתב כ-$ (HTML)."""
    import pandas as pd

    def pct(v):
        return "-" if v is None else f"{v * 100:+.1f}%"

    def usd(v):
        return "-" if v is None else f"${v:,.2f}"

    def day(d):
        try:
            return d.strftime("%Y-%m-%d")
        except Exception:
            return str(d)

    def vs(last, m):
        return "-" if not m else pct(last / m - 1.0)

    def cross(s):
        if s["ma50"] is None or s["ma200"] is None:
            return "-"
        return "ממוצע 50 מעל ממוצע 200" if s["ma50"] > s["ma200"] else "ממוצע 50 מתחת לממוצע 200"

    def col(s):
        if s is None:
            return ["-"] * 14
        return [
            usd(s["last"]),
            f"{usd(s['lo52'])} עד {usd(s['hi52'])}",
            pct(s["from_hi52"]),
            f"{usd(s['ma20'])} / {usd(s['ma50'])} / {usd(s['ma200'])}",
            f"{vs(s['last'], s['ma20'])} / {vs(s['last'], s['ma50'])} / {vs(s['last'], s['ma200'])}",
            cross(s),
            f"{pct(s['max_dd'])} ({day(s['max_dd_date'])})",
            f"{pct(s['worst_1d'])} ({day(s['worst_1d_date'])})",
            pct(s["worst_5d"]),
            f"{s['days_down10']} / {s['days_down5']}",
            "-" if s["rv30"] is None else f"{s['rv30'] * 100:.0f}%",
            "-" if s["rv1y"] is None else f"{s['rv1y'] * 100:.0f}%",
            pct(s["total_return"]),
            f"{day(s['start'])} עד {day(s['end'])} ({s['n_days']} ימי מסחר)",
        ]

    labels = ["מחיר אחרון", "טווח 52 שבועות", "מרחק מהשיא של 52 שבועות",
              "ממוצע נע 20 / 50 / 200 יום", "מחיר מול הממוצעים 20 / 50 / 200",
              "ממוצע 50 מול ממוצע 200",
              "ירידה מקסימלית מהשיא (בטווח שנבחר)", "היום הגרוע ביותר",
              "5 ימי המסחר הגרועים ביותר", "ימים בירידה של 10%+ / 5%+",
              "תנודתיות ממומשת, 30 יום (שנתי)", "תנודתיות ממומשת, שנה (שנתי)",
              "תשואה בטווח שנבחר", "הטווח שנבדק"]
    data = {"מדד": labels, ticker: col(st_t)}
    if st_b is not None:
        data["SPY (להשוואה)"] = col(st_b)
    return pd.DataFrame(data)


def _render_stock_history(ticker: str, key_prefix: str) -> None:
    """נרות, ממוצעים נעים 20/50/200, גרף ירידה מהשיא וטבלת עובדות עבר. נפתח לפי בקשה."""
    if not ticker:
        return
    import pandas as pd
    from styles import html_table as _hist_html
    with st.expander(f"📈 גרף והיסטוריה של {ticker}: איך המניה התנהגה בעבר", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        _per = c1.selectbox("טווח", _HIST_RANGES, index=_HIST_RANGES.index("שנה"), key=f"{key_prefix}_hist_period")
        _kind = c2.selectbox("סוג גרף", ["נרות + ממוצעים", "קו מול SPY"], key=f"{key_prefix}_hist_kind",
                             help="'קו מול SPY' מנרמל את המניה ואת SPY ל-100 בתחילת הטווח, כדי להשוות ביצועים.")
        _bar_opts = _HIST_BARS.get(_per, ["יומי", "שבועי", "חודשי"])
        _bar = c3.selectbox("גודל נר", _bar_opts, index=_bar_opts.index(_HIST_BAR_DEFAULT[_per]),
                            key=f"{key_prefix}_hist_bar_{_per}",
                            help="בטווח קצר הנרות הם תוך-יום (דקות או שעה). בטווח ארוך נרות יומיים צפופים מדי, "
                                 "ולכן ברירת המחדל שבועי או חודשי.")
        _log = c4.checkbox("סקאלה לוגריתמית", value=False, key=f"{key_prefix}_hist_log",
                           help="מתאימה למניות שזזו פי כמה. בה כל אחוז נראה אותו גובה.")
        st.markdown(
            '<div style="font-size:0.875rem;color:inherit;margin-top:.4rem;">ממוצעים נעים '
            '<span style="opacity:.75;">(קווי תמיכה והתנגדות דינמיים)</span></div>',
            unsafe_allow_html=True,
        )
        # התווית נכתבת כ-HTML משלה (לא כתווית של ה-checkbox), כדי שתהיה גדולה, לבנה ובלי רקע
        _mc = st.columns([1, 5, 1, 5, 1, 5])
        _mas = []
        for _i, (_n, _dot) in enumerate(((20, "#E69F00"), (50, "#0072B2"), (200, "#CC3311"))):
            if _mc[_i * 2].checkbox(f"ממוצע {_n} יום", value=True, key=f"{key_prefix}_hist_ma_{_n}",
                                    label_visibility="collapsed"):
                _mas.append(_n)
            _mc[_i * 2 + 1].markdown(
                f'<div style="font-size:0.875rem;color:inherit;line-height:2.2;">'
                f'<span style="color:{_dot};">&#9679;</span> ממוצע {_n} יום</div>',
                unsafe_allow_html=True,
            )
        st.caption("ממוצע של מחירי הסגירה ב-N הנרות האחרונים (בנרות תוך-יום: N נרות של אותו גודל). משמש הרבה סוחרים "
                   "כקו תמיכה או התנגדות. זה תיאור של העבר, לא הבטחה שהמחיר יגיב אליהם.")
        full = _try_fetch_price_history(ticker)
        if full is None:
            st.info(f"לא נמצאה היסטוריית מחירים עבור \"{ticker}\" (בדקי טיקר או חיבור רשת).")
            return
        _intra_mode = _per in ("יום", "שבוע") or (_per == "חודש" and _bar != "יומי")
        intra = None
        if _intra_mode:
            _iv, _pr = _HIST_INTRA[_bar]
            intra = _try_fetch_intraday(ticker, _iv, _pr)
            if intra is None:
                st.info("אין כרגע נתוני תוך-יום לטיקר הזה בגודל הנר שנבחר. אפשר לנסות גודל נר אחר או טווח ארוך יותר.")
                return
        fr = _history_frames(full, intra, _per, _bar, mas=tuple(_mas) if _mas else ())
        win, bars, _ma_lines = fr["win"], fr["bars"], fr["ma"]
        s = win["Close"]
        # SPY: לגרף ההשוואה באותו גודל נר, ולטבלה על ימי מסחר
        b = None
        bt = None
        if ticker != "SPY":
            _bd = _try_fetch_price_history("SPY")
            if _bd is not None:
                bt = _bd["Close"][_bd.index >= fr["tbl"].index[0]]
                if _intra_mode:
                    _bi = _try_fetch_intraday("SPY", _iv, _pr)
                    b = None if _bi is None else _bi["Close"][_bi.index >= win.index[0]]
                else:
                    b = _bd["Close"][_bd.index >= win.index[0]]
                if b is not None and len(b) < 5:
                    b = None
                if bt is not None and len(bt) < 30:
                    bt = None
        _ma_color = {20: "#E69F00", 50: "#0072B2", 200: "#CC3311"}
        # הסתרת סופי שבוע ושעות סגורות, כדי שלא יהיו רווחים ריקים בין הנרות
        _breaks = []
        if _intra_mode:
            _breaks = [dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour")]
        elif _bar == "יומי":
            _breaks = [dict(bounds=["sat", "mon"])]

        _dd = s / s.cummax() - 1.0
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            if _kind == "קו מול SPY":
                fig.add_trace(go.Scatter(x=s.index, y=s / float(s.iloc[0]) * 100.0, name=ticker, mode="lines"))
                if b is not None:
                    fig.add_trace(go.Scatter(x=b.index, y=b / float(b.iloc[0]) * 100.0, name="SPY", mode="lines"))
                _ytitle = "מנורמל ל-100 בתחילת הטווח"
            else:
                fig.add_trace(go.Candlestick(
                    x=bars.index, open=bars["Open"], high=bars["High"], low=bars["Low"], close=bars["Close"],
                    name=ticker,
                    increasing=dict(line=dict(color="#26A69A", width=1), fillcolor="#26A69A"),
                    decreasing=dict(line=dict(color="#EF5350", width=1), fillcolor="#EF5350"),
                ))
                for n, m in _ma_lines.items():
                    fig.add_trace(go.Scatter(x=m.index, y=m, name=f"ממוצע {n}", mode="lines",
                                             line=dict(width=1.6, color=_ma_color[n]),
                                             hovertemplate="%{y:.2f}<extra>ממוצע " + str(n) + "</extra>"))
                _ytitle = "מחיר"
            fig.update_layout(height=440, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=_ytitle,
                              yaxis_type="log" if _log else "linear", legend=dict(orientation="h"),
                              xaxis_rangeslider_visible=False, dragmode="pan", hovermode="closest",
                              hoverlabel=dict(font_size=11, font_color="#FFFFFF",
                                              bgcolor="rgba(20,20,20,0.70)", bordercolor="rgba(255,255,255,0.25)"))
            fig.update_xaxes(gridcolor="rgba(128,128,128,0.15)", rangebreaks=_breaks)
            fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", fixedrange=False)
            _cfg = {"scrollZoom": True, "displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}
            st.plotly_chart(fig, use_container_width=True, config=_cfg, key=f"{key_prefix}_hist_price_fig")
            st.caption("גלגלת העכבר מקרבת ומרחיקה, גרירה מזיזה את הגרף, לחיצה כפולה מאפסת את התצוגה. "
                       "בזמן שהעכבר על הגרף הגלגלת מקרבת את הגרף ולא גוללת את העמוד.")
            st.markdown(f"**ירידה מהשיא של {ticker}** (כמה מתחת לשיא הקודם המניה הייתה בכל נקודה בטווח)")
            fig2 = go.Figure(go.Scatter(x=_dd.index, y=_dd * 100.0, fill="tozeroy", mode="lines", name="ירידה מהשיא %"))
            fig2.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="%",
                               dragmode="pan", hovermode="x",
                               hoverlabel=dict(font_size=11, font_color="#FFFFFF",
                                               bgcolor="rgba(20,20,20,0.70)", bordercolor="rgba(255,255,255,0.25)"))
            fig2.update_xaxes(rangebreaks=_breaks)
            st.plotly_chart(fig2, use_container_width=True, config=_cfg, key=f"{key_prefix}_hist_dd_fig")
        except ImportError:
            _fallback = pd.DataFrame({"סגירה": s})
            for n, m in _ma_lines.items():
                _fallback[f"ממוצע {n}"] = m
            st.line_chart(_fallback)
            st.line_chart(_dd * 100.0)

        if fr["short"]:
            st.caption("הטבלה למטה מחושבת על השנה האחרונה (ימי מסחר), כי טווח קצר מדי לסטטיסטיקה של סיכון.")
        st.markdown(f"##### סיכום טבלה: {ticker}")
        st.html(_hist_html(_history_table(ticker, _history_stats(fr["tbl"]),
                                          _history_stats(bt) if bt is not None else None), rtl=True))
        st.caption("אלה עובדות על העבר, לא תחזית ולא המלצה. מחיר מעל ממוצע נע נחשב לחוזק ומתחת לחולשה, אבל המחיר לא חייב להגיב אליו. \"ירידה מקסימלית מהשיא\" מראה כמה המניה איבדה בנקודה הגרועה בטווח, ושווה להשוות אותה לסטרייק שבחרת.")


# ---------------------------------------------------------------------------
# טבלת השוואה לפי סטרייק (PUT). אין ציון ואין דירוג: שורה לכל סטרייק, ממוינת לפי סטרייק.
# ההסתברויות מגיעות מ-options_engine (אותם מודלים כמו בטאב RiskShield).
# ---------------------------------------------------------------------------
OTM_COLUMNS = ["Ticker", "Type", "Strike", "Picked", "Bid", "Ask", "PriceUsed", "Delta", "IV_Percent",
               "OTM_Model_Percent", "OTM_FatTail_Percent", "OTM_Historical_Percent",
               "BreakEven", "Distance_Percent", "xEM", "OpenInterest", "Volume",
               "Spread_Percent", "DTE", "Expiration"]

# מה שמוצג על המסך (נוח לטלפון). הקובץ המיוצא מכיל את כל OTM_COLUMNS.
OTM_DISPLAY = ["Ticker", "Strike", "Picked", "Bid", "Ask", "PriceUsed", "Delta", "OTM_Model_Percent", "OTM_FatTail_Percent",
               "OTM_Historical_Percent", "BreakEven", "Distance_Percent", "xEM", "OpenInterest",
               "Spread_Percent"]

# טווחי דלתא לבחירה (ערך מוחלט). None = כל הסטרייקים בטווח.
DELTA_BANDS = {"0.05 עד 0.10 (הכללים שלי)": (0.05, 0.10), "0.05 עד 0.20": (0.05, 0.20),
               "0.10 עד 0.35": (0.10, 0.35), "הכול (עד 50% מתחת למחיר)": None}


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


def _r1(x, nd=1):
    return None if x is None else round(float(x), nd)


def _nearest_expiry(expirations, target):
    if not expirations:
        return None
    return min(expirations, key=lambda e: abs((_date.fromisoformat(e) - target).days))


def _us_market_open_now(now=None):
    try:
        from zoneinfo import ZoneInfo
        n = now or datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return None
    if n.weekday() >= 5:
        return False
    return (9, 30) <= (n.hour, n.minute) < (16, 0)


def build_strike_compare(ticker, price, expiry, chain, *, closes, delta_fn, engine, r=0.045,
                         span=0.5, today=None, picked=None):
    """
    שורה לכל סטרייק PUT מתחת למחיר (עד span מתחתיו). ערך שאי אפשר לחשב נשאר ריק, לא מומצא.
    PriceUsed = Mid מציטוט חי בלבד (Bid ו-Ask חיים). בלי Last.
    engine = מודול options_engine. closes = רשימת סגירות יומית (או None).
    picked = קבוצת סטרייקים שנבחרו בסורק (מסומנים ב-✓ בעמודה Picked).
    """
    import math
    import pandas as pd
    today = today or _date.today()
    dte = max(1, (_date.fromisoformat(expiry) - today).days)
    T = dte / 365.0
    log_rets = None
    if closes and len(closes) > 30:
        log_rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a and a > 0 and b and b > 0]
    lo = (1 - span) * price
    rows = []
    for c in chain:
        k = _num(c.get("strike"))
        if k is None or not (lo <= k <= price):
            continue
        iv = _num(c.get("impliedVolatility"))
        iv_ok = bool(iv and iv > 0)
        delta = model = fat = hist = xem = None
        if iv_ok:
            try:
                d = delta_fn("PUT", price, k, T, iv, r)
                delta = None if d is None else abs(float(d))
            except Exception:
                delta = None
            try:
                model = engine.bs_greeks(kind="put", S=price, K=k, T=T, sigma=iv, r=r).prob_otm
            except Exception:
                model = None
            try:
                em = engine.expected_move(S=price, sigma=iv, T_days=dte)
                xem = engine.strike_distance_in_expected_moves(S=price, K=k, move=em.move)
            except Exception:
                xem = None
        if log_rets:
            try:
                fat = engine.fat_tail_otm_probability(log_rets, S=price, K=k, dte_days=dte).otm_probability
            except Exception:
                fat = None
        if closes:
            try:
                hist = engine.historical_put_otm_probability(closes, strike=k, dte_days=dte).weighted_otm
            except Exception:
                hist = None
        bid, ask = _num(c.get("bid")), _num(c.get("ask"))
        mid = _live_mid(bid, ask)
        be = None
        if mid is not None:
            try:
                be = engine.short_put_breakeven(strike=k, premium=mid)
            except Exception:
                be = k - mid
        spread = None if mid is None else (ask - bid) / mid * 100.0
        rows.append({
            "Ticker": ticker, "Type": "PUT", "Strike": k,
            "Picked": "✓" if (picked and k in picked) else "", "Bid": bid, "Ask": ask, "PriceUsed": mid,
            "Delta": _r1(delta, 3), "IV_Percent": _r1(None if not iv_ok else iv * 100.0, 2),
            "OTM_Model_Percent": _r1(None if model is None else model * 100.0),
            "OTM_FatTail_Percent": _r1(None if fat is None else fat * 100.0),
            "OTM_Historical_Percent": _r1(None if hist is None else hist * 100.0),
            "BreakEven": _r1(be, 2), "Distance_Percent": _r1((price - k) / price * 100.0),
            "xEM": _r1(xem, 2), "OpenInterest": _int(c.get("openInterest")), "Volume": _int(c.get("volume")),
            "Spread_Percent": _r1(spread), "DTE": dte, "Expiration": expiry,
        })
    df = pd.DataFrame(rows, columns=OTM_COLUMNS)
    if not df.empty:
        df = df.sort_values("Strike", ascending=False).reset_index(drop=True)
    return df


def filter_by_delta(df, band):
    """band = (lo, hi) לפי |Delta|, או None לכל השורות. שורות בלי Delta נשארות רק ב'הכול'."""
    if band is None or df.empty:
        return df
    d = df["Delta"]
    return df[d.notna() & (d >= band[0]) & (d <= band[1])].reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _closes_10y(ticker: str):
    full = _try_fetch_price_history(ticker)
    if full is None:
        return None
    return full["Close"].iloc[-2520:].astype(float).tolist()


MAX_TICKERS = 10


def _parse_tickers(text):
    out = []
    for x in (text or "").replace(",", " ").split():
        x = x.strip().upper()
        if x and x not in out:
            out.append(x)
    return out


def _send_to_otm(entries):
    """callback של הכפתור בסורק. entries = [(ticker, strike, expiry)]. מכין את הטאב ומבקש טעינה אוטומטית."""
    ss = st.session_state
    picks, exps, tickers = {}, {}, []
    for t, k, e in entries:
        if t not in tickers:
            tickers.append(t)
        if k is not None:
            picks.setdefault(t, set()).add(float(k))
        if e:
            exps.setdefault(t, e)
    tickers = tickers[:MAX_TICKERS]
    extra = list(ss.get("otm_extra", ["NVDA"]))
    for t in tickers:
        if t not in extra:
            extra.append(t)
    ss["otm_extra"] = extra
    ss["otm_picks"] = picks
    ss["otm_expiry"] = exps
    ss["otm_tickers"] = tickers
    ss["otm_autoload"] = True
    ss["otm_jump"] = True


def render_send_to_otm(st_mod, df) -> None:
    """בסורק, מתחת לתוצאות: טבלה עם תיבות סימון (PUT בלבד) וכפתור שליחה לטאב הסתברות OTM."""
    import pandas as pd
    if df is None or df.empty or "Type" not in df.columns:
        return
    puts = df[df["Type"] == "PUT"].reset_index(drop=True)
    if puts.empty:
        st_mod.caption("ניתוח OTM זמין ל-PUT בלבד, ובתוצאות אין PUT.")
        return
    cols = [c for c in ["Ticker", "Strike", "Expiration", "Bid", "Ask", "Delta", "AnnualizedReturn_Percent",
                        "OpenInterest"] if c in puts.columns]
    view = puts[cols].copy()
    view.insert(0, "בחרי", False)
    st_mod.markdown("##### 🔬 נתחי לעומק")
    st_mod.caption("מסמנים וי בעמודה הראשונה ולוחצים על הכפתור. אפשר לסמן כמה חוזים ומניות.")
    edited = st_mod.data_editor(view, hide_index=True, use_container_width=True, key="deep_editor",
                                disabled=cols, height=min(420, 40 + 35 * len(view)),
                                column_config={"בחרי": st_mod.column_config.CheckboxColumn("בחרי", default=False)})
    mask = edited["בחרי"].fillna(False).astype(bool)
    chosen = puts[mask.values]

    def _entries(frame):
        return [(r["Ticker"], r["Strike"], str(r["Expiration"])[:10]) for _, r in frame.iterrows()]
    sel_entries, all_entries = _entries(chosen), _entries(puts)
    b1, b2 = st_mod.columns(2)
    b1.button(f"🔬 נתחי את המסומנים ({len(sel_entries)})", key="deep_btn", disabled=not sel_entries,
              on_click=lambda: _send_to_otm(sel_entries))
    b2.button("🔬 נתחי את כל המניות בתוצאות", key="deep_all_btn", on_click=lambda: _send_to_otm(all_entries))
    st_mod.caption(f"הניתוח נפתח בטאב 📊 הסתברות OTM (עד {MAX_TICKERS} מניות בבת אחת). "
                   "אם הטאב לא נפתח לבד, לחצי עליו למעלה.")


def render_pick_stocks(st_mod, uni) -> None:
    """בשלב 1, מתחת לרשימת המניות: סימון מניות בטבלה ושליחה לטאב OTM (עובד גם כשהשוק סגור)."""
    import pandas as pd
    if not uni:
        return
    df = pd.DataFrame({"בחרי": False, "Ticker": [t for t, _ in uni], "Price": [p for _, p in uni]})
    st_mod.caption("מסמנים וי ליד מניה ולוחצים על הכפתור. הניתוח נפתח בטאב 📊 הסתברות OTM, "
                   "עם הפקיעה הקרובה לתאריך מסעיף 1 וכל הסטרייקים של ה-PUT.")
    edited = st_mod.data_editor(df, hide_index=True, use_container_width=True, key="deep_stock_editor",
                                disabled=["Ticker", "Price"], height=min(420, 40 + 35 * len(df)),
                                column_config={"בחרי": st_mod.column_config.CheckboxColumn("בחרי", default=False)})
    picked = edited[edited["בחרי"].fillna(False).astype(bool).values]
    entries = [(r["Ticker"], None, None) for _, r in picked.iterrows()]
    st_mod.button(f"🔬 נתחי את המסומנים בטאב OTM ({len(entries)})", key="deep_stock_btn",
                  disabled=not entries, on_click=lambda: _send_to_otm(entries))
    if len(entries) > MAX_TICKERS:
        st_mod.caption(f"יישלחו {MAX_TICKERS} הראשונות מתוך {len(entries)} שסומנו.")


def render_tab_jump(st_mod) -> None:
    """ניסיון להעביר אוטומטית לטאב OTM אחרי לחיצה על 'נתחי לעומק'. אם הדפדפן חוסם, עוברים ידנית."""
    if not st_mod.session_state.pop("otm_jump", False):
        return
    try:
        import streamlit.components.v1 as components
        components.html(
            "<script>setTimeout(function(){try{var t=window.parent.document.querySelectorAll("
            "'button[role=\"tab\"]');if(t.length>1){t[1].click();}}catch(e){}},300);</script>",
            height=0)
    except Exception:
        pass  # המעבר האוטומטי הוא בונוס בלבד. אפשר ללחוץ על הטאב ידנית.


def _load_one(dy, engine, ticker, target_expiry, picks, exps):
    """טוען טיקר אחד ומחזיר dict עם df, או dict עם error."""
    price = dy.fetch_spot(ticker)
    exp = exps.get(ticker) or _nearest_expiry(dy.fetch_expirations(ticker), target_expiry)
    if price is None or exp is None:
        return {"error": "אין מחיר או תאריכי פקיעה."}
    chain = dy.fetch_chain(ticker, exp, "PUT")
    closes = _closes_10y(ticker)
    df = build_strike_compare(ticker, float(price), exp, chain, closes=closes, delta_fn=dy.delta_fn,
                              engine=engine, picked=picks.get(ticker))
    return {"df": df, "expiry": exp, "price": float(price), "has_hist": closes is not None}


def render_otm_tab(tickers, target_expiry) -> None:
    """הטאב: בחירת מניה אחת או כמה, טבלת השוואה לפי סטרייק עם הסתברויות OTM, גרף וטבלת סיכום."""
    import pandas as pd
    import data_yf as dy
    import options_engine as engine
    from screener import export_excel
    from styles import html_table

    ss = st.session_state
    ss.setdefault("otm_extra", ["NVDA"])
    st.caption("בוחרים מניה אחת או כמה ורואים כל סטרייק של PUT עם הסתברות OTM משלושה מודלים, Breakeven ומרחק מהמחיר. "
               "הרשימה ממוינת לפי סטרייק. אין ציון, אין דירוג ואין המלצה: ההחלטה שלך. "
               "הפקיעה היא הקרובה לתאריך מסעיף 1, או הפקיעה של החוזה שנשלח מהסורק. "
               "העמודה Picked מסמנת סטרייקים שנשלחו מהסורק.")
    options = list(dict.fromkeys(list(tickers) + list(ss["otm_extra"])))
    ss.setdefault("otm_tickers", [t for t in ["NVDA"] if t in options])
    sel = st.multiselect(f"מניות לניתוח (עד {MAX_TICKERS})", options, key="otm_tickers")
    typed = st.text_input("להוסיף טיקרים ידנית (רווח או פסיק)", value="", key="otm_typed")
    band_label = st.selectbox("טווח דלתא", list(DELTA_BANDS), key="otm_band",
                              help="מסנן את הסטרייקים לפי |Delta|. 'הכול' מציג את כל הסטרייקים בטווח 50% מתחת למחיר.")
    clicked = st.button("טעני ניתוח", key="otm_btn", type="primary")
    auto = ss.pop("otm_autoload", False)
    if clicked or auto:
        names = list(dict.fromkeys(list(sel) + _parse_tickers(typed)))[:MAX_TICKERS]
        if not names:
            ss["otm_data"] = {"error": "לא נבחרה מניה."}
        else:
            picks, exps = ss.get("otm_picks", {}), ss.get("otm_expiry", {})
            if clicked and not auto:
                picks, exps = {}, {}     # טעינה ידנית חדשה: בלי סימוני Picked ישנים
                ss["otm_picks"], ss["otm_expiry"] = {}, {}
            res = {}
            bar = st.progress(0.0, text="טוענת...")
            for i, t in enumerate(names):
                bar.progress(i / len(names), text=f"טוענת {t} ({i + 1}/{len(names)})")
                try:
                    res[t] = _load_one(dy, engine, t, target_expiry, picks, exps)
                except Exception as e:
                    res[t] = {"error": f"{type(e).__name__}: {e}"}
            bar.empty()
            ss["otm_data"] = {"frames": res}
    data = ss.get("otm_data")
    if not data:
        st.info("בחרי מניה אחת או יותר ולחצי 'טעני ניתוח'.")
        return
    if data.get("error"):
        st.warning(data["error"])
        return
    frames = data["frames"]
    for t, f in frames.items():
        if f.get("error"):
            st.warning(f"{t}: {f['error']}")
    good = {t: f for t, f in frames.items() if not f.get("error")}
    if not good:
        return
    if _us_market_open_now() is False:
        st.caption("השוק בארה״ב סגור. Bid, Ask, PriceUsed ו-Breakeven יהיו ריקים, וה-IV ממנו מחושבות הסתברויות "
                   "ה-OTM וה-Delta עשוי להיות ישן. כדאי להשוות שוב בשעות המסחר.")
    band = DELTA_BANDS[band_label]
    views = []
    for t, f in good.items():
        view = filter_by_delta(f["df"], band)
        views.append(view)
        st.markdown(f"##### {t} | מחיר {f['price']:,.2f} | פקיעה {f['expiry']}")
        if not f.get("has_hist"):
            st.caption(f"{t}: אין היסטוריית מחירים, ולכן עמודות Fat-tail והיסטורי ריקות.")
        if view.empty:
            st.info(f"{t}: אין סטרייקים בטווח הדלתא שנבחר. נסי 'הכול'.")
        else:
            st.html(html_table(view[OTM_DISPLAY]))
    allv = pd.concat([v for v in views if not v.empty], ignore_index=True) if any(not v.empty for v in views) else None
    if allv is not None:
        base = "otm_" + "_".join(good)[:60] + "_PUT"
        d1, d2 = st.columns(2)
        d1.download_button(f"⬇️ כל המניות ל-Excel ({len(good)} מניות, כל העמודות)",
                           data=export_excel(allv, sheet_name="OTM"), file_name=f"{base}.xlsx", key="otm_xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        d2.download_button("⬇️ CSV", data=allv.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{base}.csv", mime="text/csv", key="otm_csv")
    st.caption("OTM_Model = Black-Scholes לפי ה-IV של הסטרייק. OTM_FatTail = אותו חישוב עם זנבות שמנים מהתשואות ההיסטוריות. "
               "OTM_Historical = כמה פעמים בעבר המחיר נשאר מעל הסטרייק בטווח זמן דומה (ממוצע משוקלל 1/3/5/10 שנים). "
               "Breakeven = סטרייק פחות Mid. xEM = המרחק בתזוזות צפויות. אלה הערכות, לא הבטחה.")
    chart_t = st.selectbox("גרף והיסטוריה עבור", list(good), key="otm_chart_t")
    _render_stock_history(chart_t, "otm")
