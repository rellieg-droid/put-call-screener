# -*- coding: utf-8 -*-
"""
deep_tab.py

לשונית "ניתוח מעמיק": בוחרים מניה מתוצאות הסריקה, רואים את הגרף שלה (כמו בלשונית הסתברות OTM),
ובוחרים חוזה אחד לניתוח מלא: כסף, סיכון, תנודתיות, ניהול העסקה, השוואה לספרד והכללים שלי.

עקרונות: בלי ציון, בלי רמזור מסכם, בלי המלצה. כל בדיקה מוצגת בנפרד.
הפונקציות בחלק העליון טהורות (ללא streamlit ורשת) כדי שאפשר לבדוק אותן offline.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from math import sqrt

MULT = 100


# ---------------------------------------------------------------- pure calculations
def cushion_pct(price, breakeven):
    """בכמה אחוזים המחיר יכול לרדת מהמחיר הנוכחי עד נקודת האיזון. None אם חסר נתון."""
    if not price or price <= 0 or breakeven is None:
        return None
    return (price - breakeven) / price * 100.0


def exit_plan(credit, expiry_iso, *, profit_pct=50.0, stop_pct=200.0, alert_dte=21,
              contracts=1, today=None):
    """
    credit = פרמיה שהתקבלה למניה (Mid).
    profit_pct: סוגרים כשאפשר לקנות בחזרה בפחות מהפרמיה, כך ש-profit_pct% ממנה נשאר ברווח.
    stop_pct: הפסד של stop_pct% מהפרמיה (200% = קונים בחזרה ב-3 כפול הפרמיה).
    alert_dte: תאריך שבו נשארו alert_dte ימים לפקיעה (נקודת החלטה: לסגור או לגלגל).
    """
    if credit is None or credit <= 0:
        return None
    today = today or date.today()
    buy_profit = credit * (1 - profit_pct / 100.0)
    buy_stop = credit * (1 + stop_pct / 100.0)
    alert = date.fromisoformat(expiry_iso) - timedelta(days=int(alert_dte))
    return {
        "credit": credit,
        "profit_buyback": buy_profit,
        "profit_usd": (credit - buy_profit) * MULT * contracts,
        "stop_buyback": buy_stop,
        "loss_usd": (buy_stop - credit) * MULT * contracts,
        "alert_date": alert,
        "days_to_alert": (alert - today).days,
    }


def expected_range(price, sigma, dte, strike, side):
    """
    טווח תזוזה צפוי לפי S*IV*sqrt(T): סטיית תקן אחת (כ-68%) ושתיים (כ-95%), בהנחת התפלגות נורמלית.
    buffer_pct חיובי = הסטרייק מחוץ לטווח של סטיית תקן אחת, באחוזים ממחיר המניה.
    """
    if not price or price <= 0 or not sigma or sigma <= 0 or dte is None or dte <= 0:
        return None
    move = price * sigma * sqrt(dte / 365.0)
    low1, high1 = price - move, price + move
    if side.upper() == "PUT":
        buffer = (low1 - strike) / price * 100.0
    else:
        buffer = (strike - high1) / price * 100.0
    return {"move": move, "move_pct": move / price * 100.0, "low1": low1, "high1": high1,
            "low2": price - 2 * move, "high2": price + 2 * move,
            "outside_1sd": buffer > 0, "buffer_pct": buffer}


def position_greeks(side, S, K, dte, iv, r, contracts, engine):
    """
    גריקס למניה אחת ולפוזיציה של מכירה (short) בכמות החוזים שנבחרה.
    בפוזיציה: delta שלילית ב-PUT, theta חיובית = רווח יומי משחיקת זמן, vega שלילית = הפסד כש-IV עולה.
    """
    if not iv or iv <= 0 or dte is None or dte <= 0:
        return None
    g = engine.bs_greeks(kind="call" if side.upper() == "CALL" else "put",
                         S=S, K=K, T=dte / 365.0, sigma=iv, r=r)
    n = MULT * contracts
    return {
        "delta": g.delta, "gamma": g.gamma, "vega": g.vega, "theta": g.theta,
        "delta_pos": -g.delta * n, "gamma_pos": -g.gamma * n,
        "theta_pos": -g.theta * n, "vega_pos": -g.vega * n,
    }


def spread_compare(short_k, short_mid, long_k, long_mid, contracts=1):
    """
    Put Credit Spread: מוכרים את הסטרייק הגבוה, קונים הגנה בנמוך.
    השוואה ל-Cash Secured Put באותו סטרייק. None אם הנתונים לא הגיוניים.
    """
    if None in (short_k, short_mid, long_k, long_mid) or long_k >= short_k:
        return None
    net = short_mid - long_mid
    width = short_k - long_k
    if net <= 0:
        return None
    max_loss_ps = width - net
    n = MULT * contracts
    return {
        "net_credit": net, "width": width, "protection_cost": long_mid,
        "max_profit_usd": net * n, "max_loss_usd": max_loss_ps * n,
        "collateral_usd": width * n, "breakeven": short_k - net,
        "ror_pct": (net / max_loss_ps * 100.0) if max_loss_ps > 0 else None,
        "return_on_collateral_pct": net / width * 100.0,
        "csp_collateral_usd": short_k * n,
        "csp_return_on_collateral_pct": short_mid / short_k * 100.0,
        "csp_max_loss_usd": (short_k - short_mid) * n,
    }


def rule_lines(row: dict, rules):
    """
    בודק את החוזה מול הכללים שלי (בלי דוחות וחשיפה, שיש להם נתונים אחרים).
    מחזיר [(שם כלל, 'pass' | 'fail' | 'unknown', הסבר)].
    """
    from rules import evaluate_strike
    delta = row.get("Delta")
    ev = {
        "side": row.get("Type"),
        "delta_abs": None if delta is None else abs(float(delta)),
        "xem": row.get("xEM"),
        "mid": row.get("PriceUsed"),
        "annual_yield_pct": row.get("AnnualizedReturn_Percent"),
        "spread_pct": row.get("Spread_Percent"),
        "open_interest": row.get("OpenInterest"),
        "iv_hv": row.get("IV_HV_Ratio"),
    }
    for k, v in list(ev.items()):
        if v != v:                       # NaN -> None
            ev[k] = None
    res = evaluate_strike(ev, replace(rules, check_earnings=False, check_exposure=False))
    return [(name, st_, why) for name, (st_, why) in res.items()]


def rows_from_otm(frames: dict, existing=None):
    """
    ממיר את טבלאות לשונית הסתברות OTM (PUT בלבד) לשורות בפורמט של תוצאות הסריקה,
    כדי שאפשר יהיה לנתח אותן גם בלי סריקה (למשל כשהשוק סגור ואין חוזים עם ציטוט חי).
    שורות שכבר קיימות ב-existing (אותו טיקר, סוג, סטרייק ופקיעה) מדולגות.
    """
    def clean(v):
        if v is None:
            return None
        try:
            return None if v != v else v
        except Exception:
            return v

    seen = {(r["Ticker"], r["Type"], float(r["Strike"]), r["Expiration"]) for r in (existing or [])}
    out = []
    for t, f in (frames or {}).items():
        df = (f or {}).get("df")
        price = (f or {}).get("price")
        if df is None or getattr(df, "empty", True) or not price:
            continue
        for rec in df.to_dict("records"):
            k = clean(rec.get("Strike"))
            if k is None or (t, "PUT", float(k), rec.get("Expiration")) in seen:
                continue
            mid = clean(rec.get("PriceUsed"))
            dte = int(rec.get("DTE") or 1)
            out.append({
                "Ticker": t, "Type": "PUT", "Strike": float(k), "UnderlyingPrice": float(price),
                "PriceUsed": mid, "Delta": clean(rec.get("Delta")), "IV_Percent": clean(rec.get("IV_Percent")),
                "DTE": dte, "Expiration": rec.get("Expiration"),
                "AnnualizedReturn_Percent": (None if mid is None else round(mid / float(k) * 365.0 / dte * 100.0, 1)),
                "OpenInterest": clean(rec.get("OpenInterest")), "Volume": clean(rec.get("Volume")),
                "Distance_Percent": clean(rec.get("Distance_Percent")), "xEM": clean(rec.get("xEM")),
                "BreakEven": clean(rec.get("BreakEven")), "Spread_Percent": clean(rec.get("Spread_Percent")),
                "OTM_Probability_Percent": clean(rec.get("OTM_Model_Percent")),
                "HV30_Percent": None, "IV_HV_Ratio": None, "source": "otm",
            })
    return out


# ---------------------------------------------------------------- UI
def _usd(v, nd=2):
    if v is None:
        return "-"
    return f"-${abs(v):,.{nd}f}" if v < 0 else f"${v:,.{nd}f}"


def _pct(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}%"


def _label(r):
    p = r.get("PriceUsed")
    return (f"{r['Type']} {r['Strike']:g} | {r['Expiration']} | "
            f"פרמיה {'-' if p is None or p != p else f'{p:.2f}'}")


def render_deep_pick(st, df) -> None:
    """בלשונית הסריקה: בוחרים חוזה מהתוצאות ועוברים לניתוח מעמיק."""
    if df is None or df.empty:
        return
    rows = df.to_dict("records")
    idx = list(range(len(rows)))
    with st.container(border=True):
        st.markdown("**🔬 ניתוח מעמיק לחוזה אחד**")
        c1, c2 = st.columns([4, 1])
        pick = c1.selectbox("בחרי חוזה", idx, key="deep_pick_idx", label_visibility="collapsed",
                            format_func=lambda i: f"{rows[i]['Ticker']} | {_label(rows[i])}")
        if c2.button("נתחי", key="deep_pick_btn", type="primary"):
            st.session_state["deep_ticker"] = rows[pick]["Ticker"]
            st.session_state["deep_contract"] = pick
            st.session_state["deep_jump"] = True


def render_deep_jump(st) -> None:
    """ניסיון להעביר אוטומטית ללשונית הניתוח המעמיק (השלישית). אם הדפדפן חוסם, עוברים ידנית."""
    if not st.session_state.pop("deep_jump", False):
        return
    try:
        import streamlit.components.v1 as components
        components.html(
            "<script>setTimeout(function(){try{var t=window.parent.document.querySelectorAll("
            "'button[role=\"tab\"]');if(t.length>2){t[2].click();}}catch(e){}},300);</script>",
            height=0)
    except Exception:
        pass


def render_deep_tab(st, dy, engine, load_rules_fn, history_fn, closes_fn, compare_fn, atm_iv_fn,
                    html_table, r: float = 0.045) -> None:
    import pandas as pd

    ss = st.session_state
    rows = list((ss.get("scan_out") or {}).get("rows") or [])
    rows += rows_from_otm((ss.get("otm_data") or {}).get("frames") or {}, existing=rows)
    st.markdown("בוחרים מניה, רואים את הגרף שלה, ואז בוחרים חוזה לניתוח מלא. החוזים מגיעים מתוצאות הסריקה "
                "ומהמניות שנטענו בלשונית הסתברות OTM. אין כאן ציון או המלצה: כל מספר מוצג בנפרד וההחלטה שלך.")
    tickers = sorted({r_["Ticker"] for r_ in rows})
    if not tickers:
        st.info("אין חוזים לניתוח עדיין. הריצי סריקה בלשונית הראשונה (בשוק פתוח), או טעני מניה בלשונית "
                "הסתברות OTM. אפשר גם להקליד טיקר כדי לראות רק גרף.")
        manual = st.text_input("טיקר לגרף", key="deep_manual").strip().upper()
        if manual:
            history_fn(manual, "deepm")
        return

    if ss.get("deep_ticker") not in tickers:
        ss.pop("deep_ticker", None)
    ticker = st.selectbox("מניה", tickers, key="deep_ticker")
    history_fn(ticker, "deep")

    mine = [i for i, r_ in enumerate(rows) if r_["Ticker"] == ticker]
    if ss.get("deep_contract") not in mine:
        ss.pop("deep_contract", None)
    gi = st.selectbox("חוזה", mine, key="deep_contract", format_func=lambda i: _label(rows[i]))
    row = rows[gi]
    side, K, S = row["Type"], float(row["Strike"]), float(row["UnderlyingPrice"])
    expiry, dte = row["Expiration"], int(row["DTE"])
    mid = row.get("PriceUsed")
    iv = (row.get("IV_Percent") or 0) / 100.0

    st.markdown(f"#### {ticker} | {side} {K:g} | פקיעה {expiry} ({dte} ימים)")
    contracts = int(st.number_input("מספר חוזים", 1, 1000, 1, 1, key="deep_contracts"))

    # ---- מעקב
    from tracker_tab import add_to_tracking, new_position
    with st.container(border=True):
        t1, t2 = st.columns([2, 1])
        e_mid = t1.number_input("פרמיה בעת הבחירה ($ למניה)", 0.0, 1000.0, float(mid) if mid else 0.0, 0.05,
                                key=f"deep_entry_mid_{gi}",
                                help="נלקחת מה-Mid החי. אם השוק סגור והשדה 0, הרווח/הפסד במעקב לא יחושב. אפשר להקליד ידנית.")
        t2.button("📌 הוסיפי למעקב", key="deep_track_btn", type="primary", on_click=add_to_tracking,
                  args=(st, new_position(ticker, side, K, expiry, entry_price=S, entry_mid=e_mid or None,
                                         contracts=contracts, entry_iv=row.get("IV_Percent"))))

    # ---- כסף
    st.markdown("##### 💰 כסף")
    commitment = (K if side == "PUT" else S) * MULT * contracts
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("פרמיה שתתקבל", _usd(None if mid is None else mid * MULT * contracts, 0),
              help="Mid כפול 100 כפול מספר החוזים.")
    c2.metric("התחייבות", _usd(commitment, 0), help="PUT: סטרייק x 100. CALL מכוסה: שווי 100 מניות במחיר הנוכחי.")
    c3.metric("תשואה על ההתחייבות", _pct(None if mid is None else mid / (K if side == 'PUT' else S) * 100.0, 2))
    c4.metric("תשואה שנתית", _pct(row.get("AnnualizedReturn_Percent")))

    # ---- סיכון
    st.markdown("##### 🛡️ סיכון")
    be = row.get("BreakEven")
    cush = cushion_pct(S, be)
    dist = row.get("Distance_Percent")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("מרחק הסטרייק מהמחיר", _pct(dist))
    c2.metric("נקודת איזון", _usd(be), help="PUT: סטרייק פחות פרמיה. CALL מכוסה: מחיר פחות פרמיה.")
    c3.metric("כרית ביטחון", _pct(cush), help="בכמה אחוזים המניה יכולה לרדת עד שמתחילים להפסיד.")
    c4.metric("מרחק ב-xEM", "-" if row.get("xEM") is None or row.get("xEM") != row.get("xEM") else f"{row['xEM']:.2f}",
              help="כמה 'תזוזות צפויות' הסטרייק רחוק מהמחיר.")

    sigma, sigma_src = None, ""
    chain = []
    try:
        chain = dy.fetch_chain(ticker, expiry, side) or []
        sigma = atm_iv_fn(chain, S)
        sigma_src = "IV של הסטרייק הקרוב למחיר"
    except Exception:
        chain = []
    if not sigma and iv:
        sigma, sigma_src = iv, "IV של החוזה (אין שרשרת זמינה)"
    er = expected_range(S, sigma, dte, K, side)
    if er:
        c1, c2 = st.columns(2)
        c1.metric("טווח צפוי ל-1σ (כ-68%)", f"{er['low1']:,.2f} עד {er['high1']:,.2f}",
                  help=f"תזוזה של {er['move_pct']:.1f}% לפי {sigma_src}.")
        c2.metric("טווח צפוי ל-2σ (כ-95%)", f"{er['low2']:,.2f} עד {er['high2']:,.2f}")
        if er["outside_1sd"]:
            st.caption(f"הסטרייק מחוץ לטווח של סטיית תקן אחת, במרחק {er['buffer_pct']:.1f}% ממחיר המניה.")
        else:
            st.caption(f"הסטרייק בתוך הטווח של סטיית תקן אחת (חסרים {abs(er['buffer_pct']):.1f}% כדי לצאת ממנו).")
        st.caption("הטווח מחושב מ-IV בהנחת התפלגות נורמלית. בפועל לזנבות יש משקל גדול יותר, ולכן זו הערכה בלבד.")

    # הסתברויות OTM
    prob_rows = [("מודל Black-Scholes (מהסריקה)", row.get("OTM_Probability_Percent"))]
    if side == "PUT":
        try:
            cmp_df = compare_fn(ticker, S, expiry, dte, chain, closes_fn(ticker))
            m = cmp_df[cmp_df["Strike"] == K]
            if not m.empty:
                prob_rows = [("מודל Black-Scholes", m.iloc[0]["OTM_Model_Percent"]),
                             ("זנבות שמנים (תשואות היסטוריות)", m.iloc[0]["OTM_FatTail_Percent"]),
                             ("היסטורי (10 שנים)", m.iloc[0]["OTM_Historical_Percent"])]
        except Exception:
            pass
    pdf = pd.DataFrame([{"מודל": n, "הסתברות לפקיעה מחוץ לכסף": ("-" if v is None or v != v else f"{v:.1f}%")}
                        for n, v in prob_rows])
    st.html(html_table(pdf, rtl=True))
    st.caption("כל מודל מוצג בנפרד, בלי ממוצע ובלי ציון. פער גדול ביניהם הוא סימן שהמספרים לא בטוחים.")

    # ---- גריקס
    st.markdown("##### 🧮 Greeks (למכירה, בכמות החוזים שבחרת)")
    pg = None
    try:
        pg = position_greeks(side, S, K, dte, iv, r, contracts, engine)
    except Exception:
        pg = None
    if pg:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Delta", f"{pg['delta_pos']:+.1f}", help="כמה מניות 'שקולות' את מחזיקה. שלילי = מרוויחה כשהמניה יורדת (CALL) או מפסידה כשהיא יורדת (PUT).")
        c2.metric("Theta ליום", _usd(pg["theta_pos"]), help="הרווח היומי המשוער משחיקת זמן, אם שום דבר אחר לא משתנה.")
        c3.metric("Vega", _usd(pg["vega_pos"]), help="שינוי ברווח/הפסד על כל עליית IV של 1%. שלילי = מפסידה כש-IV עולה.")
        c4.metric("Gamma", f"{pg['gamma_pos']:+.2f}", help="כמה ה-Delta משתנה על כל $1 תזוזה במניה.")
    else:
        st.caption("אין IV לחוזה, ולכן אי אפשר לחשב Greeks.")

    # ---- תנודתיות
    st.markdown("##### 📊 תנודתיות")
    c1, c2, c3 = st.columns(3)
    c1.metric("IV של החוזה", _pct(row.get("IV_Percent")))
    c2.metric("HV ל-30 יום", _pct(row.get("HV30_Percent")))
    q = row.get("IV_HV_Ratio")
    c3.metric("IV חלקי HV", "-" if q is None or q != q else f"{q:.2f}",
              help="מעל 1 = השוק מתמחר יותר תנודתיות ממה שהמניה עשתה ב-30 הימים האחרונים. HV של 30 יום תקופה קצרה.")

    # ---- ניהול
    st.markdown("##### 🎯 ניהול העסקה")
    a, b, c = st.columns(3)
    pp = a.number_input("יעד רווח (% מהפרמיה)", 10, 95, 50, 5, key="deep_pp")
    sp = b.number_input("סטופ (% הפסד מהפרמיה)", 50, 1000, 200, 25, key="deep_sp")
    ad = c.number_input("נקודת החלטה (ימים לפקיעה)", 1, 90, 21, 1, key="deep_ad")
    ep = exit_plan(mid, expiry, profit_pct=pp, stop_pct=sp, alert_dte=ad, contracts=contracts)
    if ep:
        c1, c2, c3 = st.columns(3)
        c1.metric(f"סגירה ברווח {pp}%", f"קנייה חזרה ב-{ep['profit_buyback']:.2f}",
                  help=f"רווח של {_usd(ep['profit_usd'], 0)}.")
        c2.metric(f"סטופ {sp}%", f"קנייה חזרה ב-{ep['stop_buyback']:.2f}",
                  help=f"הפסד של {_usd(ep['loss_usd'], 0)}.")
        if ep["days_to_alert"] >= 0:
            c3.metric(f"{ad} ימים לפקיעה", ep["alert_date"].strftime("%d/%m/%Y"),
                      help=f"בעוד {ep['days_to_alert']} ימים. זה המועד להחליט אם לסגור, לגלגל או להחזיק.")
        else:
            c3.metric(f"{ad} ימים לפקיעה", "כבר עברנו", help="נשארו פחות ימים מנקודת ההחלטה שהגדרת.")
        st.caption("סטופ של 200% פירושו שהאופציה עלתה לפי 3 מהפרמיה שקיבלת. אלה הכללים שהגדרת, לא חוק כללי.")
    else:
        st.caption("אין פרמיה חיה, ולכן אי אפשר לחשב מחירי יציאה.")

    # ---- ספרד
    if side == "PUT":
        st.markdown("##### 🔀 השוואה ל-Put Credit Spread")
        longs = []
        for c_ in chain:
            ks, lm = c_.get("strike"), None
            try:
                from rules import live_mid
                lm = live_mid(c_.get("bid"), c_.get("ask"))
            except Exception:
                pass
            if ks is not None and ks < K and lm is not None:
                longs.append((float(ks), lm))
        if mid is None or not longs:
            st.caption("אין סטרייקים נמוכים עם מחיר חי להשוואה.")
        else:
            longs.sort(reverse=True)
            target = K - max(1.0, round(S * 0.05))
            di = min(range(len(longs)), key=lambda i: abs(longs[i][0] - target))
            li = st.selectbox("סטרייק הגנה (קנייה)", range(len(longs)), index=di, key="deep_long",
                              format_func=lambda i: f"{longs[i][0]:g} | מחיר {longs[i][1]:.2f}")
            sc = spread_compare(K, mid, longs[li][0], longs[li][1], contracts)
            if sc:
                tbl = pd.DataFrame([
                    {"": "קרדיט נטו למניה", "Cash Secured Put": f"{mid:.2f}", "Put Credit Spread": f"{sc['net_credit']:.2f}"},
                    {"": "התחייבות", "Cash Secured Put": _usd(sc["csp_collateral_usd"], 0), "Put Credit Spread": _usd(sc["collateral_usd"], 0)},
                    {"": "תשואה על ההתחייבות", "Cash Secured Put": _pct(sc["csp_return_on_collateral_pct"], 2), "Put Credit Spread": _pct(sc["return_on_collateral_pct"], 2)},
                    {"": "הפסד מקסימלי", "Cash Secured Put": _usd(sc["csp_max_loss_usd"], 0) + " (אם המניה הולכת לאפס)", "Put Credit Spread": _usd(sc["max_loss_usd"], 0)},
                    {"": "נקודת איזון", "Cash Secured Put": _usd(K - mid), "Put Credit Spread": _usd(sc["breakeven"])},
                ])
                st.html(html_table(tbl, rtl=True))
                st.caption(f"תשואה על הסיכון בספרד: {_pct(sc['ror_pct'])}. הספרד מקטין את ההתחייבות והסיכון, "
                           "ובתמורה משלם על ההגנה ומוותר על חלק מהפרמיה.")

    # ---- כללים ודוחות
    st.markdown("##### ✅ הכללים שלי")
    try:
        lines = rule_lines(row, load_rules_fn())
    except Exception:
        lines = []
    icon = {"pass": "✓ עבר", "fail": "✗ נכשל", "unknown": "? לא נבדק"}
    if lines:
        st.html(html_table(pd.DataFrame([{"כלל": n, "מצב": icon.get(s, s), "פירוט": w} for n, s, w in lines]), rtl=True))
    try:
        known, ed = dy.fetch_next_earnings(ticker)
    except Exception:
        known, ed = False, None
    if not known:
        st.caption("דוח הרווחים הבא: לא ידוע. כדאי לבדוק ידנית לפני כניסה.")
    else:
        inside = date.fromisoformat(expiry) >= ed >= date.today()
        st.caption(f"דוח הרווחים הבא: {ed.strftime('%d/%m/%Y')}" + (" (לפני הפקיעה)" if inside else " (אחרי הפקיעה)"))
    st.caption("הנתונים מ-yfinance, מושהים ולא רשמיים. כדאי לאמת את הפרמיה אצל הברוקר לפני ביצוע.")
