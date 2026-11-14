# -*- coding: utf-8 -*-
"""
tracker_tab.py

לשונית "📌 המעקב שלי": מוסיפים חוזים שבחרת (מהניתוח המעמיק), ורואים בטבלה אחת מה קרה להם מאז הבחירה
ועד הפקיעה: מחיר המניה, המרחק מהסטרייק, הפרמיה היום מול הפרמיה בבחירה, ימים שנשארו והתראות.

עקרונות: בלי ציון ובלי המלצה. כל נתון וכל התראה מוצגים בנפרד.
הנתונים נשמרים בקובץ tracking.json לצד האפליקציה. בענן (Streamlit Cloud) הקובץ נמחק כשהאפליקציה
מתעדכנת או נרדמת, ולכן יש הורדה והעלאה של גיבוי.
הפונקציות העליונות טהורות (ללא streamlit ורשת) ונבדקות offline.
"""
from __future__ import annotations

import json
from datetime import date

MULT = 100
TRACK_FILE = "tracking.json"


# ---------------------------------------------------------------- storage (pure)
def load_positions(path: str = TRACK_FILE) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data if isinstance(p, dict) and p.get("id")] if isinstance(data, list) else []
    except Exception:
        return []


def save_positions(items: list, path: str = TRACK_FILE) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def position_id(ticker: str, side: str, strike: float, expiry: str) -> str:
    return f"{ticker.upper()}-{side.upper()}-{float(strike):g}-{expiry}"


def new_position(ticker, side, strike, expiry, *, entry_price, entry_mid=None, contracts=1,
                 entry_iv=None, today=None) -> dict:
    """תמונת מצב ברגע הבחירה. entry_mid (פרמיה למניה) יכול להיות None אם לא הייתה פרמיה חיה."""
    today = today or date.today()
    return {
        "id": position_id(ticker, side, strike, expiry), "ticker": ticker.upper(), "side": side.upper(),
        "strike": float(strike), "expiry": str(expiry)[:10], "entry_date": today.isoformat(),
        "entry_price": float(entry_price),
        "entry_mid": None if entry_mid in (None, 0) else float(entry_mid),
        "contracts": int(contracts), "entry_iv": entry_iv,
    }


def add_position(items: list, pos: dict) -> list:
    """מוסיף, ואם אותו חוזה כבר קיים מחליף אותו."""
    return [p for p in items if p["id"] != pos["id"]] + [pos]


def remove_position(items: list, pid: str) -> list:
    return [p for p in items if p["id"] != pid]


def merge_positions(items: list, incoming: list) -> list:
    out = list(items)
    for p in incoming:
        if isinstance(p, dict) and p.get("id") and all(k in p for k in ("ticker", "side", "strike", "expiry", "entry_price")):
            out = add_position(out, p)
    return out


# ---------------------------------------------------------------- evaluation (pure)
def _dist_pct(side: str, price, strike):
    """מרחק מהסטרייק באחוזים ממחיר המניה. חיובי = מחוץ לכסף (PUT: מעל הסטרייק, CALL: מתחתיו)."""
    if not price or price <= 0:
        return None
    return (price - strike) / price * 100.0 if side == "PUT" else (strike - price) / price * 100.0


def expiry_outcome(pos: dict, close_at_expiry):
    """מה קרה בפקיעה, לפי מחיר הסגירה ביום הפקיעה. None אם אין מחיר."""
    if close_at_expiry is None:
        return None
    k = pos["strike"]
    if pos["side"] == "PUT":
        if close_at_expiry >= k:
            return "פקע מחוץ לכסף: נשארה כל הפרמיה"
        return f"פקע בתוך הכסף: סגירה {close_at_expiry:.2f}, מתחת לסטרייק ב-{k - close_at_expiry:.2f}"
    if close_at_expiry <= k:
        return "פקע מחוץ לכסף: נשארה כל הפרמיה"
    return f"פקע בתוך הכסף: סגירה {close_at_expiry:.2f}, מעל הסטרייק ב-{close_at_expiry - k:.2f}"


def evaluate_position(pos: dict, *, price, mid, today=None, profit_pct=50.0, stop_pct=200.0,
                      alert_dte=21, close_at_expiry=None) -> dict:
    """מחשב את שורת המעקב. ערך שאי אפשר לחשב נשאר None. flags = התראות נפרדות, לא ציון."""
    today = today or date.today()
    exp = date.fromisoformat(pos["expiry"])
    days = (exp - today).days
    side, k, n = pos["side"], pos["strike"], pos.get("contracts", 1)
    e_price, e_mid = pos.get("entry_price"), pos.get("entry_mid")
    change = None if not (price and e_price) else (price / e_price - 1.0) * 100.0
    dist_now, dist_entry = _dist_pct(side, price, k), _dist_pct(side, e_price, k)

    pnl_usd = kept = None
    if e_mid and mid is not None:
        pnl_usd = (e_mid - mid) * MULT * n
        kept = (e_mid - mid) / e_mid * 100.0

    flags = []
    expired = days < 0
    if expired:
        flags.append("פקע")
    else:
        if dist_now is not None and dist_now < 0:
            flags.append("בתוך הכסף")
        if kept is not None and kept >= profit_pct:
            flags.append(f"הגיע ליעד {profit_pct:g}% רווח")
        if e_mid and mid is not None and mid >= e_mid * (1 + stop_pct / 100.0):
            flags.append(f"הגיע לסטופ {stop_pct:g}%")
        if days <= alert_dte:
            flags.append(f"{max(days, 0)} ימים לפקיעה")
    return {
        "days_left": max(days, 0) if not expired else 0, "expired": expired, "price": price, "change_pct": change,
        "dist_now": dist_now, "dist_entry": dist_entry, "mid": mid, "pnl_usd": pnl_usd, "kept_pct": kept,
        "flags": flags, "outcome": expiry_outcome(pos, close_at_expiry) if expired else None,
    }


# ---------------------------------------------------------------- add from the deep tab
def add_to_tracking(st, pos: dict) -> None:
    """callback: שומר בזיכרון הסשן ובקובץ."""
    items = add_position(st.session_state.get("track_items") or load_positions(), pos)
    st.session_state["track_items"] = items
    st.session_state["track_saved"] = save_positions(items)
    st.session_state["track_added"] = pos["id"]


# ---------------------------------------------------------------- UI
def _fmt(v, nd=1, suffix="", sign=False):
    if v is None or v != v:
        return "-"
    return f"{v:+.{nd}f}{suffix}" if sign else f"{v:.{nd}f}{suffix}"


def render_tracker_tab(st, dy, history_fn, html_table) -> None:
    import pandas as pd
    from rules import live_mid

    ss = st.session_state
    if "track_items" not in ss:
        ss["track_items"] = load_positions()
    items = ss["track_items"]

    st.markdown("חוזים שבחרת, ומה קרה להם מאז. מוסיפים מהלשונית 🔬 ניתוח מעמיק (כפתור 'הוסיפי למעקב'). "
                "אין כאן ציון או המלצה: כל נתון והתראה בנפרד.")
    st.caption("הרשימה נשמרת בקובץ tracking.json לצד האפליקציה. בענן (iPhone) היא נמחקת כשהאפליקציה מתעדכנת, "
               "ולכן כדאי להוריד גיבוי בתחתית הלשונית ולהעלות אותו כשצריך.")

    if ss.get("track_added"):
        st.success(f"נוסף למעקב: {ss.pop('track_added')}" + ("" if ss.get("track_saved", True)
                   else " (לא הצלחתי לשמור לקובץ, הרשימה קיימת רק בסשן הזה)"))

    with st.expander("⚙️ הגדרות התראות"):
        a, b, c = st.columns(3)
        pp = a.number_input("יעד רווח (% מהפרמיה)", 10, 95, 50, 5, key="tk_pp")
        sp = b.number_input("סטופ (% הפסד מהפרמיה)", 50, 1000, 200, 25, key="tk_sp")
        ad = c.number_input("התראה (ימים לפקיעה)", 1, 90, 21, 1, key="tk_ad")

    if not items:
        st.info("עדיין אין חוזים במעקב. בלשונית 'ניתוח מעמיק' בחרי חוזה ולחצי 'הוסיפי למעקב'.")
    else:
        if st.button("🔄 רענני נתונים", key="tk_refresh"):
            try:
                dy.clear_cache()
            except Exception:
                pass
        rows, evs = [], {}
        today = date.today()
        for p in sorted(items, key=lambda x: (x["expiry"], x["ticker"], x["strike"])):
            price = mid = close_exp = None
            try:
                price = dy.fetch_spot(p["ticker"])
            except Exception:
                pass
            if date.fromisoformat(p["expiry"]) >= today:
                try:
                    for c_ in dy.fetch_chain(p["ticker"], p["expiry"], p["side"]) or []:
                        ks = c_.get("strike")
                        if ks is not None and abs(float(ks) - p["strike"]) < 1e-6:
                            mid = live_mid(c_.get("bid"), c_.get("ask"))
                            break
                except Exception:
                    pass
            else:
                try:
                    full = history_fn(p["ticker"])
                    if full is not None:
                        cl = full["Close"][full.index <= pd.Timestamp(p["expiry"])]
                        close_exp = float(cl.iloc[-1]) if len(cl) else None
                        price = price if price else close_exp
                except Exception:
                    pass
            ev = evaluate_position(p, price=price, mid=mid, today=today, profit_pct=pp, stop_pct=sp,
                                   alert_dte=ad, close_at_expiry=close_exp)
            evs[p["id"]] = ev
            rows.append({
                "מניה": p["ticker"], "סוג": p["side"], "סטרייק": f"{p['strike']:g}", "פקיעה": p["expiry"],
                "ימים": ev["days_left"], "נבחר ב": p["entry_date"],
                "מחיר בבחירה": _fmt(p["entry_price"], 2), "מחיר היום": _fmt(price, 2),
                "שינוי": _fmt(ev["change_pct"], 1, "%", sign=True),
                "מרחק מהסטרייק בבחירה": _fmt(ev["dist_entry"], 1, "%"),
                "מרחק מהסטרייק היום": _fmt(ev["dist_now"], 1, "%"),
                "פרמיה בבחירה": _fmt(p.get("entry_mid"), 2), "פרמיה היום": _fmt(mid, 2),
                "רווח/הפסד משוער": ("-" if ev["pnl_usd"] is None else f"{ev['pnl_usd']:+,.0f}$"),
                "נשמר מהפרמיה": _fmt(ev["kept_pct"], 0, "%"),
                "התראות": " | ".join(ev["flags"]) or "-",
            })
        st.html(html_table(pd.DataFrame(rows), rtl=True))
        st.caption("מרחק מהסטרייק: חיובי = מחוץ לכסף, שלילי = בתוך הכסף. רווח/הפסד משוער = (פרמיה בבחירה פחות פרמיה היום) "
                   "כפול 100 כפול חוזים, כאילו מכרת בפרמיה שבבחירה. הפרמיה היום חסרה כשהשוק סגור.")
        for p in items:
            ev = evs.get(p["id"])
            if ev and ev["outcome"]:
                st.caption(f"{p['id']}: {ev['outcome']}")

        # ---- מסלול המחיר
        st.markdown("##### 📈 מסלול המחיר מאז הבחירה")
        pid = st.selectbox("חוזה", [p["id"] for p in items], key="tk_chart_sel")
        pos = next(p for p in items if p["id"] == pid)
        try:
            full = history_fn(pos["ticker"])
        except Exception:
            full = None
        if full is None:
            st.info("אין היסטוריית מחירים לטיקר הזה.")
        else:
            s = full["Close"]
            s = s[s.index >= pd.Timestamp(pos["entry_date"]) - pd.Timedelta(days=3)]
            s = s[s.index <= pd.Timestamp(pos["expiry"]) + pd.Timedelta(days=1)]
            if len(s) < 2:
                st.caption("עוד אין מספיק ימי מסחר מאז הבחירה כדי לצייר מסלול.")
            else:
                be = None if not pos.get("entry_mid") else (
                    pos["strike"] - pos["entry_mid"] if pos["side"] == "PUT" else pos["strike"] + pos["entry_mid"])
                try:
                    import plotly.graph_objects as go
                    fig = go.Figure(go.Scatter(x=s.index, y=s, mode="lines+markers", name=pos["ticker"]))
                    fig.add_hline(y=pos["strike"], line_dash="dash", line_color="#EF5350",
                                  annotation_text=f"סטרייק {pos['strike']:g}")
                    if be is not None:
                        fig.add_hline(y=be, line_dash="dot", line_color="#E69F00",
                                      annotation_text=f"נקודת איזון {be:.2f}")
                    fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), hovermode="x",
                                      yaxis_title="מחיר")
                    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
                    st.plotly_chart(fig, key="tk_path_fig")
                except ImportError:
                    st.line_chart(s)
                st.caption("הקו המקווקו האדום הוא הסטרייק. כל עוד המחיר מעליו (PUT) החוזה מחוץ לכסף.")

        c1, c2 = st.columns([3, 1])
        rm = c1.selectbox("להסרה מהמעקב", [p["id"] for p in items], key="tk_rm_sel")
        if c2.button("🗑️ הסירי", key="tk_rm_btn"):
            ss["track_items"] = remove_position(items, rm)
            save_positions(ss["track_items"])
            st.rerun()

    # ---- גיבוי
    with st.expander("💾 גיבוי ושחזור"):
        st.download_button("⬇️ הורדת גיבוי (JSON)", data=json.dumps(items, ensure_ascii=False, indent=1).encode("utf-8"),
                           file_name="tracking_backup.json", mime="application/json", key="tk_dl")
        up = st.file_uploader("שחזור מגיבוי", type=["json"], key="tk_up")
        if up is not None and st.button("שחזרי ומזגי", key="tk_restore"):
            try:
                merged = merge_positions(items, json.loads(up.getvalue().decode("utf-8")))
                ss["track_items"] = merged
                save_positions(merged)
                st.rerun()
            except Exception as e:
                st.error(f"הקובץ לא תקין ({type(e).__name__}).")
