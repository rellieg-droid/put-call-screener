# -*- coding: utf-8 -*-
"""
app.py - סורק PUT / CALL לפי הכללים שלי, עם מציאת מניות לפי מדד.
אפליקציה עצמאית, בנויה לשימוש באייפון. אין כאן ציון ואין דירוג: החלטה אחרונה שלך.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import data_yf as dy
import universe as uv
from styles import CSS, html_table
from rules import Rules, load_rules, save_rules
from screener import scan, results_frame, summary_frame, tally_frame, export_excel, SHEET_COLUMNS
from screener import COLUMNS as ALL_COLUMNS
from chain_preview import render_chain_preview
from otm_tab import render_otm_tab

st.set_page_config(page_title="סורק PUT / CALL", page_icon="🔎", layout="centered")

st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def sec(n, title):
    st.markdown(f'<div class="sec"><span class="n">{n}</span><span class="t">{title}</span></div>', unsafe_allow_html=True)


def us_market_open_now(now=None):
    """True/False לפי שעון ניו יורק (ב׳-ו׳ 9:30-16:00). בלי לוח חגים. None אם אין נתוני אזור זמן."""
    try:
        from zoneinfo import ZoneInfo
        n = now or datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return None
    if n.weekday() >= 5:
        return False
    return (9, 30) <= (n.hour, n.minute) < (16, 0)


def otm_prob_fn(side, S, K, T, iv, r):
    """הסתברות OTM של מודל Black-Scholes מ-options_engine (אותו מודל כמו בטאב RiskShield)."""
    from options_engine import bs_greeks
    return bs_greeks(kind=side.lower(), S=S, K=K, T=T, sigma=iv, r=r).prob_otm


ss = st.session_state
ss.setdefault("universe", None)       # [(ticker, price)]
ss.setdefault("scan_out", None)
ss.setdefault("scan_meta", None)

st.title("סורק PUT / CALL")
_mkt = us_market_open_now()
if _mkt is True:
    _pill = '<span class="pill open"><span class="dot"></span>השוק בארה״ב פתוח</span>'
elif _mkt is False:
    _pill = '<span class="pill closed"><span class="dot"></span>השוק בארה״ב סגור</span>'
else:
    _pill = ""
st.markdown(f'<div class="statusbar">{_pill}<span class="pill">מסחר 16:30 עד 23:00 שעון ישראל</span></div>',
            unsafe_allow_html=True)
st.caption("עובר על חוזי אופציה לפי הכללים שלך. אין ציון ואין דירוג: הרשימה ממוינת לפי טיקר, סוג וסטרייק, "
           "וההחלטה שלך. PUT = מכירה מכוסה במזומן. CALL = מכירה מכוסה במניות שכבר יש לך.")

tab_scan, tab_otm = st.tabs(["🔎 סורק", "📊 הסתברות OTM"])

with tab_scan:
    # ------------------------------------------------------------------ connection test
    with st.expander("🔌 בדיקת חיבור לנתונים"):
        st.caption("אם הסריקה מחזירה ריק, התחילי כאן. הבדיקה מראה איזה שלב נכשל ומה השגיאה האמיתית.")
        if st.button("הרץ בדיקה על SPY", key="conn_btn"):
            dy.clear_errors()
            with st.spinner("בודקת..."):
                ss["conn_steps"] = dy.connection_test("SPY")
        for s in ss.get("conn_steps", []):
            (st.success if s["ok"] else st.error)(f"{s['step']}: {s['detail']}")
        errs = dy.recent_errors()
        if errs:
            st.caption("שגיאות אחרונות מהסריקות:")
            st.code("\n".join(errs[-8:]), language=None)

    # ------------------------------------------------------------------ 1. what to scan
    with st.container(border=True):
        sec(1, "מה לסרוק")
        side_choice = st.radio("סוג חוזה", ["PUT", "CALL", "שניהם"], horizontal=True, key="side_choice")
        sides = ["PUT", "CALL"] if side_choice == "שניהם" else [side_choice]
        c1, c2 = st.columns(2)
        target_expiry = c1.date_input("פקיעה קרובה ל", value=date.today() + timedelta(days=30), key="target_exp",
                                      help="לכל מניה נבחרת תאריך הפקיעה הקרוב ביותר לתאריך הזה.")
        contracts = c2.number_input("חוזים לכל סטרייק", 1, 100, 1, key="contracts")

    # ------------------------------------------------------------------ 2. universe
    with st.container(border=True):
        sec(2, "מאילו מניות")
        idx = st.multiselect("מדד", list(uv.INDEX_SOURCES.keys()), default=["S&P 500"], key="idx_sel")
        manual = st.text_area("או טיקרים ידנית (מופרדים ברווח או פסיק)", "", key="manual_t", height=70,
                              help="אם תכתבי כאן טיקרים ותבחרי גם מדד, הרשימות מתאחדות.")
        p1, p2, p3 = st.columns(3)
        pmin = p1.number_input("מחיר מינימום", 0.0, 10000.0, 5.0, 5.0, key="pmin")
        pmax = p2.number_input("מחיר מקסימום", 0.0, 10000.0, 150.0, 10.0, key="pmax")
        cap = p3.number_input("מקס׳ מניות", 5, 300, 60, 5, key="cap",
                              help="סריקה של הרבה מניות איטית, במיוחד בטלפון. אפשר להגדיל בהדרגה.")

        if st.button("📋 מצאי מניות לפי מחיר", key="find_btn"):
            tickers: list[str] = []
            notes = []
            for name in idx:
                try:
                    t, src = uv.fetch_index_tickers(name, cache_path="universe_cache.json")
                    tickers = uv.merge_universes(tickers, t)
                    notes.append(f"{name}: {len(t)} מניות ({'מהמטמון' if src != 'web' else 'מהרשת'})")
                except Exception as e:
                    notes.append(f"{name}: נכשל ({e})")
            man = [uv.normalize_ticker(x) for x in manual.replace(",", " ").split()]
            man = [x for x in man if x]
            if idx:
                with st.spinner(f"מביאה מחירים ל-{len(tickers)} מניות..."):
                    prices = uv.fetch_last_prices(tickers) if tickers else {}
                picked = uv.filter_by_price(prices, pmin, pmax)
                if tickers and not prices:
                    notes.append("לא התקבלו מחירים. בדקי את החיבור למעלה.")
                # טיקרים ידניים נכנסים בלי סינון מחיר
                have = {t for t, _ in picked}
                extra = [(t, None) for t in man if t not in have]
                ss["universe"] = picked + extra
            else:
                ss["universe"] = [(t, None) for t in man]
            ss["universe_notes"] = notes

        if ss.get("universe_notes"):
            for n in ss["universe_notes"]:
                st.caption(n)
        uni = ss.get("universe")
        if uni is not None:
            if uni:
                shown = len(uni)
                st.success(f"נמצאו {shown} מניות." + (f" יסרקו הראשונות לפי סדר א-ב: {int(cap)}." if shown > cap else ""))
                uni_df = pd.DataFrame({"Ticker": [t for t, _ in uni], "Price": [p for _, p in uni]})
                d1, d2 = st.columns(2)
                d1.download_button("⬇️ רשימת מניות בלבד (Excel)", data=export_excel(uni_df, sheet_name="Tickers"),
                                   file_name="stock_list.xlsx", key="uni_xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                d2.download_button("⬇️ רשימת מניות בלבד (CSV)", data=uni_df.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="stock_list.csv", mime="text/csv", key="uni_csv")
                with st.expander("הרשימה"):
                    st.html(html_table(uni_df))
                with st.expander("🔍 שרשרת אופציות של מניה אחת (Bid, Ask, Delta)"):
                    render_chain_preview(st, [t for t, _ in uni], target_expiry)
            else:
                st.warning("לא נמצאו מניות. הרחיבי את טווח המחיר או הוסיפי טיקרים ידנית.")

    # ------------------------------------------------------------------ 3. rules
    with st.container(border=True):
        sec(3, "הכללים שלי")
        r0 = load_rules()
        a, b = st.columns(2)
        dmin = a.number_input("Delta מינימום", 0.0, 1.0, float(r0.delta_min), 0.005, format="%.3f", key="dmin",
                              help="|Delta| בערך הסתברות שהאופציה תסתיים בתוך הכסף. 0 = כלל כבוי.")
        dmax = b.number_input("Delta מקסימום", 0.0, 1.0, float(r0.delta_max), 0.005, format="%.3f", key="dmax")
        mprem = st.number_input("פרמיה מינימום ($ למניה, Mid)", 0.0, 1000.0, float(r0.min_premium), 0.25, key="mprem",
                                format="%.2f",
                                help="Mid של האופציה בדולרים למניה. 1.00 = $100 לחוזה אחד. 0 = כלל כבוי.")
        a, b = st.columns(2)
        myield = a.number_input("תשואה שנתית מינימום (%)", 0.0, 500.0, float(r0.min_annual_yield_pct), 5.0, key="myield",
                                help="פרמיה חלקי סטרייק (PUT) או חלקי מחיר המניה (CALL), מנורמל לשנה. 0 = כבוי.")
        mspread = b.number_input("מרווח Bid/Ask מקסימום (%)", 0.0, 200.0, float(r0.max_spread_pct), 5.0, key="mspread")
        a, b = st.columns(2)
        moi = a.number_input("Open Interest מינימום", 0, 1_000_000, int(r0.min_open_interest), 10, key="moi")
        mxem = b.number_input("xEM מינימום", 0.0, 10.0, float(r0.min_xem), 0.25, key="mxem",
                              help="כמה תזוזות צפויות הסטרייק רחוק מהמחיר. 0 = כבוי.")
        chk_e = st.checkbox("לפסול אם יש דוח רווחים לפני הפקיעה", value=bool(r0.check_earnings), key="chk_e")
        chk_x = st.checkbox("PUT: לפסול אם החשיפה המצטברת חורגת מהגבול בחשבון", value=bool(r0.check_exposure), key="chk_x")

        with st.expander("💼 החשבון שלי (לכלל החשיפה ב-PUT)"):
            settled = st.number_input("Settled Cash ($)", 0.0, 100_000_000.0, 0.0, 500.0, key="settled",
                                      help="מזומן פנוי. אם נשאר 0, כלל החשיפה יסומן 'לא נבדק'.")
            alloc = st.number_input("מקסימום הקצאה מה-Settled Cash (%)", 0.0, 100.0, 70.0, 5.0, key="alloc")
            open_commit = st.number_input("התחייבות של PUT פתוחים היום ($)", 0.0, 100_000_000.0, 0.0, 500.0,
                                          key="open_commit", help="סכום סטרייק x 100 של כל ה-PUT שכבר פתוחים.")
            st.caption("הערכים האלה לא נשמרים בין ביקורים באפליקציה. הזיני אותם מחדש כשצריך.")

    rules = Rules(delta_min=dmin, delta_max=dmax, min_premium=mprem, min_xem=mxem, min_annual_yield_pct=myield,
                  max_spread_pct=mspread, min_open_interest=int(moi), check_earnings=chk_e, check_exposure=chk_x)

    if us_market_open_now() is False:
        st.info("השוק בארה״ב סגור עכשיו. מחירי Bid/Ask עשויים להיות ישנים או חסרים, ולכן חוזים בלי מחיר חי ייפסלו. "
                "כדאי לסרוק בשעות המסחר (16:30 עד 23:00 שעון ישראל).")

    # ------------------------------------------------------------------ run
    if st.button("🔎 סנני לפי הכללים שלי", type="primary", key="run_btn"):
        tick = [t for t, _ in (ss.get("universe") or [])][: int(cap)]
        if not tick:
            st.warning("אין מניות לסריקה. קודם לחצי על 'מצאי מניות לפי מחיר' או הוסיפי טיקרים ידנית.")
        else:
            save_rules(rules)
            dy.clear_errors()
            bar = st.progress(0.0, text="טוענת נתונים...")
            dy.prewarm(tick, target_expiry, sides, rules.check_earnings,
                       progress=lambda d, n: bar.progress(d / n, text=f"טוענת נתונים {d}/{n}"))
            bar.progress(1.0, text="מסננת...")
            market = us_market_open_now()
            out = scan(
                tick, target_expiry, rules, sides,
                fetch_spot=dy.fetch_spot, fetch_expirations=dy.fetch_expirations, fetch_chain=dy.fetch_chain,
                earnings_fn=dy.fetch_next_earnings, delta_fn=dy.delta_fn, xem_fn=dy.xem_fn,
                prob_fn=otm_prob_fn,
                contracts=int(contracts), settled_cash=float(settled),
                alloc_limit=float(alloc) or None, open_commitment=float(open_commit),
                off_hours=None if market is None else (not market))
            bar.empty()
            ss["scan_out"] = out
            ss["scan_meta"] = {"n": len(tick), "sides": sides, "when": datetime.now().strftime("%H:%M")}

    # ------------------------------------------------------------------ results
    out = ss.get("scan_out")
    if out:
        meta = ss["scan_meta"]
        sec("✓", "תוצאות")
        summ = out["summary"]
        no_data = [s for s in summ if s["note"].startswith(("לא נמצא מחיר", "אין תאריכי פקיעה"))]
        if summ and len(no_data) >= 0.8 * len(summ):
            st.error(
                "כמעט לכל המניות לא הגיעו נתונים. זו בעיית חיבור ולא בעיית כללים. "
                "פתחי את 'בדיקת חיבור לנתונים' למעלה כדי לראות את השגיאה."
            )
        rows = out["rows"]
        tot = out["tally"]["strikes"]
        nq = out["tally"]["fail"].get("ציטוט חי", 0)
        if tot and nq >= 0.5 * tot:
            st.warning(f"ל-{nq} מתוך {tot} החוזים אין מחיר חי (Bid/Ask), ולכן נפסלו. זה קורה בדרך כלל כשהשוק סגור. "
                       "התוצאות לא משקפות את שאר הכללים במלואם. מומלץ להריץ שוב בשעות המסחר.")
        m1, m2, m3 = st.columns(3)
        m1.metric("עברו את כל הכללים", len(rows))
        m2.metric("מניות שנסרקו", meta["n"])
        m3.metric("שעת סריקה", meta["when"])
        if rows:
            df = results_frame(rows)
            extra = st.toggle("הצג עמודות נוספות (מרחק, xEM, Breakeven, הסתברות OTM, התחייבות, NotChecked)", value=False, key="extra_cols")
            st.html(html_table(df if extra else df[SHEET_COLUMNS]))
            n_unchecked = int((df["NotChecked"] != "").sum())
            if n_unchecked:
                st.warning(f"ב-{n_unchecked} חוזים יש כלל שחסר לו נתון ולכן לא נבדק (למשל תאריך דוח לא ידוע). "
                           "זה לא אישור. הפעילי 'הצג עמודות נוספות' כדי לראות איזה כלל, בעמודה NotChecked.")
            st.caption("PriceUsed = Mid מציטוט חי (Bid ו-Ask חיים), בלי Last. 18 העמודות הראשונות זהות לגיליון שלך. הקובץ להורדה כולל אחריהן גם Breakeven, הסתברות OTM, מרחק ו-xEM, למיון באקסל.")
            out_df = df  # הייצוא תמיד כולל את כל העמודות: עמודות הגיליון ואחריהן הנוספות
            suffix = ""
            c_a, c_b = st.columns(2)
            c_a.download_button("⬇️ הטבלה המלאה ל-Excel", data=export_excel(out_df),
                                file_name=f"put_call_candidates{suffix}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="xlsx_btn", type="primary")
            c_b.download_button("⬇️ הטבלה המלאה CSV", data=out_df.to_csv(index=False).encode("utf-8-sig"),
                                file_name=f"put_call_candidates{suffix}.csv", mime="text/csv", key="csv_btn")
        else:
            st.info("אף חוזה לא עבר. אפשר לרופף כלל אחד ולהריץ שוב. הטבלה למטה מראה איזה כלל פוסל הכי הרבה.")
            st.download_button("⬇️ הטבלה המלאה ל-Excel (כותרות בלבד, אין חוזים)",
                               data=export_excel(pd.DataFrame(columns=ALL_COLUMNS)),
                               file_name="put_call_candidates_empty.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key="xlsx_empty_btn")

        with st.expander("איזה כלל פוסל כמה חוזים"):
            tf = tally_frame(out["tally"])
            if tf.empty:
                st.caption("אין.")
            else:
                st.html(html_table(tf, rtl=True))
                st.caption("חוזה יכול להיפסל ביותר מכלל אחד. 'נפסלו רק בגללו' = כמה חוזים היו עוברים אם רק הכלל הזה היה מתרופף.")
        with st.expander("סיכום לפי מניה"):
            st.html(html_table(summary_frame(summ), rtl=True))
        errs = dy.recent_errors()
        if errs:
            with st.expander(f"שגיאות שליפה ({len(errs)})"):
                st.code("\n".join(errs), language=None)


# ------------------------------------------------------------------ OTM probability tab
with tab_otm:
    render_otm_tab([t for t, _ in (ss.get("universe") or [])], target_expiry)
