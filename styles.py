# -*- coding: utf-8 -*-
"""
styles.py - עיצוב האפליקציה (CSS בלבד). אין כאן לוגיקה.
כיוון: מסוף מסחר שקט. גרפיט כחלחל, קווים דקים, מספרים בגובה קבוע, צבע רק היכן שיש משמעות.
"""

FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=IBM+Plex+Sans+Hebrew:wght@400;500;600;700&display=swap');"
)

CSS = FONT_IMPORT + """
:root {
  --bg: #0B0F17;
  --panel: #111723;
  --field: #0D121C;
  --line: #222B3D;
  --line-soft: #1A2232;
  --text: #F7F9FC;
  --muted: #F2F5FA;
  --accent: #5B8CFF;
  --accent-ink: #FFFFFF;
  --ok: #2EBD85;
  --warn: #E5B454;
  --bad: #F0616D;
  --r-panel: 12px;
  --r-field: 8px;
}

html { color-scheme: dark; -webkit-text-size-adjust: 100%; }
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  direction: rtl;
  background: var(--bg);
  color: var(--text);
  font-family: 'IBM Plex Sans Hebrew', 'Heebo', system-ui, -apple-system, 'Segoe UI', sans-serif;
  font-feature-settings: 'tnum' 1, 'lnum' 1;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"] { display: none; }
[data-testid="stDataFrame"], [data-testid="stDataFrame"] * { direction: ltr; }

.block-container { max-width: 720px; padding: 3.2rem 1rem 4rem 1rem; }

/* ---------- כיווניות וקריאות ---------- */
.stApp { overflow-x: hidden; font-size: 16px; }
h1, h2, h3, p, label, li, summary, [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"],
[data-testid="stWidgetLabel"], [data-testid="stAlert"], [data-testid="stExpander"] { direction: rtl; text-align: right; }
.stApp p, .stApp li, .stApp summary, .stApp label, .stApp span { opacity: 1; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *, [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] *,
[data-testid="stExpander"] summary *, [data-testid="stMarkdownContainer"] * { color: var(--text) !important; opacity: 1 !important; }
.pill { color: var(--text); }
.pill.open { color: var(--ok); }
/* 16px לפחות בשדות, אחרת Safari באייפון מקרב את המסך בלחיצה */
input, textarea, [data-baseweb="select"] * { font-size: 16px !important; }

/* ---------- כותרת ---------- */
h1 {
  font-size: 1.55rem !important; font-weight: 700 !important; letter-spacing: -0.01em;
  padding: 0 !important; margin: 0 0 .25rem 0 !important;
}
[data-testid="stCaptionContainer"], .stCaption, [data-testid="stCaptionContainer"] p { color: var(--text) !important; font-size: 1rem; line-height: 1.55; }

.statusbar { display: flex; align-items: center; gap: .6rem; margin: .1rem 0 1rem 0; }
.pill {
  display: inline-flex; align-items: center; gap: .45rem;
  padding: .22rem .65rem; border: 1px solid var(--line); border-radius: 999px;
  font-size: .85rem; color: var(--text); background: var(--panel);
}
.pill .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.pill.open { color: var(--ok); border-color: rgba(46,189,133,.35); }
.pill.open .dot { background: var(--ok); }
.pill.closed .dot { background: var(--bad); }

/* ---------- פאנלים ---------- */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--panel);
  border: 1px solid var(--line) !important;
  border-radius: var(--r-panel) !important;
  padding: .35rem .35rem .2rem .35rem;
}
.sec { display: flex; align-items: baseline; gap: .6rem; margin: .35rem 0 .7rem 0; }
.sec .n {
  font-size: .78rem; font-weight: 600; color: var(--accent);
  border: 1px solid rgba(91,140,255,.4); border-radius: 6px; padding: 0 .4rem; line-height: 1.5;
}
.sec .t { font-size: 1.12rem; font-weight: 600; }

/* ---------- שדות ---------- */
label, [data-testid="stWidgetLabel"] p {
  font-size: .98rem !important; font-weight: 500; color: var(--text) !important;
}
div[data-baseweb="input"], div[data-baseweb="base-input"], div[data-baseweb="select"] > div,
div[data-baseweb="textarea"], textarea {
  background: var(--field) !important;
  border: 1px solid var(--line) !important;
  border-radius: var(--r-field) !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within,
div[data-baseweb="select"] > div:focus-within {
  border-color: var(--accent) !important; box-shadow: 0 0 0 3px rgba(91,140,255,.18) !important;
}
input, textarea { color: var(--text) !important; font-variant-numeric: tabular-nums; }
div[data-testid="stNumberInput"] input { text-align: right; font-size: 1rem; }
/* בלי כפתורי + ו-, מקלדת המספרים באייפון מספיקה */
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"] { display: none; }
div[data-testid="stNumberInput"] > div > div { border-radius: var(--r-field); }

[data-testid="stCheckbox"] label p { color: var(--text) !important; font-size: 1rem !important; }
[data-testid="stRadio"] [role="radiogroup"] { gap: .4rem; }
[data-testid="stRadio"] label[data-testid="stRadioOption"] {
  background: var(--field); border: 1px solid var(--line); border-radius: var(--r-field);
  padding: .3rem .8rem; margin: 0 !important;
}
[data-testid="stRadio"] label[data-testid="stRadioOption"][data-selected="true"] { border-color: var(--accent); background: rgba(91,140,255,.1); }
[data-testid="stRadio"] label[data-testid="stRadioOption"] p { color: var(--text) !important; font-size: 1rem !important; }

/* ---------- כפתורים ---------- */
[data-testid="stElementContainer"]:has([data-testid="stButton"]), [data-testid="stElementContainer"]:has([data-testid="stDownloadButton"]),
[data-testid="stButton"], [data-testid="stDownloadButton"] { width: 100% !important; }
.stButton > button, .stDownloadButton > button, [data-testid="stButton"] button, [data-testid="stDownloadButton"] button {
  width: 100%; min-height: 2.9rem; border-radius: var(--r-field);
  background: transparent; color: var(--text); border: 1px solid var(--line);
  font-weight: 600; font-size: .95rem; transition: border-color .15s, background .15s;
}
.stButton > button:hover, .stDownloadButton > button:hover { border-color: var(--accent); color: var(--text); }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
  background: #3566D6; color: #FFFFFF; border-color: #3566D6; min-height: 3.1rem;
}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover {
  background: #4B7BE5; border-color: #4B7BE5; color: #FFFFFF;
}
button:focus-visible { outline: 2px solid var(--accent) !important; outline-offset: 2px; }

/* ---------- הודעות ---------- */
[data-testid="stAlert"] {
  background: var(--panel) !important; border: 1px solid var(--line); border-radius: var(--r-field);
  border-inline-start: 3px solid var(--accent);
}
[data-testid="stAlert"] * { color: var(--text) !important; font-size: .98rem; }
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) { border-inline-start-color: var(--ok); }
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) { border-inline-start-color: var(--warn); }
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) { border-inline-start-color: var(--bad); }

/* ---------- אקורדיון ---------- */
[data-testid="stExpander"] {
  border: 1px solid var(--line) !important; border-radius: var(--r-field) !important; background: var(--field);
}
[data-testid="stExpander"] summary { font-size: 1rem; font-weight: 500; }

/* ---------- מדדים בתוצאות ---------- */
[data-testid="stMetric"] {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-field); padding: .6rem .8rem;
}
[data-testid="stMetricLabel"] p { font-size: .78rem !important; color: var(--text) !important; }
[data-testid="stTooltipIcon"] svg, [data-testid="stTooltipHoverTarget"] svg { color: var(--muted) !important; }
::placeholder { color: #9AA5BA !important; opacity: 1 !important; }
[data-testid="stMetricValue"] { font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }

/* ---------- טבלה ---------- */
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: var(--r-field); overflow: hidden; }

/* עמודות נשארות זו לצד זו גם באייפון */
[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: .6rem; }
[data-testid="stColumn"] { min-width: 0 !important; flex: 1 1 0 !important; width: auto !important; }

/* ---------- טבלאות HTML (טקסט לבן, מיושר לפי השפה) ---------- */
.tbl-wrap { max-height: 65vh; overflow: auto; border: 1px solid var(--line); border-radius: var(--r-field); background: var(--field); margin: .3rem 0 .6rem 0; }
.tbl-wrap.ltr { direction: ltr; }
.tbl-wrap.rtl { direction: rtl; }
.tbl { border-collapse: collapse; width: 100%; font-size: .98rem; color: var(--text); font-variant-numeric: tabular-nums; }
.tbl th { position: sticky; top: 0; background: #1B2438; color: #FFFFFF; font-weight: 600; text-align: start; padding: .6rem .8rem; border-bottom: 1px solid var(--line); white-space: nowrap; }
.tbl td { padding: .55rem .8rem; border-bottom: 1px solid var(--line-soft); color: #FFFFFF; text-align: start; }
.tbl tr:nth-child(even) td { background: rgba(255,255,255,.03); }
.tbl td { white-space: nowrap; }
.tbl-wrap.ltr .tbl td.num, .tbl-wrap.ltr .tbl th.num { text-align: right; }

hr { border-color: var(--line-soft); }
@media (max-width: 480px) {
  .block-container { padding: 1rem .7rem 4rem .7rem; }
  h1 { font-size: 1.35rem !important; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""


def html_table(df, rtl=False):
    """טבלת HTML: טקסט לבן, גופן גדול, טקסט בעברית מיושר לימין, אנגלית לשמאל. גלילה פנימית עם כותרת קבועה."""
    import html as _h

    def fmt(v):
        if v is None or (isinstance(v, float) and v != v):
            return ""
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float):
            return f"{v:.4f}".rstrip("0").rstrip(".")
        return str(v)

    def is_num(v):
        if hasattr(v, "item"):
            v = v.item()
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    numcols = {c for c in df.columns
               if len(df) and all(v is None or (isinstance(v, float) and v != v) or is_num(v) for v in df[c])}
    head = "".join(f'<th dir="auto"{" class=num" if c in numcols else ""}>{_h.escape(str(c))}</th>' for c in df.columns)
    body = []
    for rec in df.to_dict("records"):
        tds = "".join(
            f'<td dir="auto"{" class=num" if is_num(rec[c]) else ""}>{_h.escape(fmt(rec[c]))}</td>'
            for c in df.columns)
        body.append(f"<tr>{tds}</tr>")
    side = "rtl" if rtl else "ltr"
    return (f'<div class="tbl-wrap {side}"><table class="tbl"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')
