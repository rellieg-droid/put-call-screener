# -*- coding: utf-8 -*-
"""בדיקת רינדור ואינטראקציה של המסך עם נתונים מדומים (ללא רשת)."""
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from streamlit.testing.v1 import AppTest

import data_yf as dy
import universe as uv
from test_screener import mk_chain


def _patch_ok(monkeypatch):
    monkeypatch.setattr(uv, "fetch_index_tickers", lambda name, cache_path=None: (["AAA", "BBB", "CCC"], "web"))
    monkeypatch.setattr(uv, "fetch_last_prices", lambda t: {"AAA": 100.0, "BBB": 50.0, "CCC": 900.0})
    monkeypatch.setattr(dy, "fetch_spot", lambda t: {"AAA": 100.0, "BBB": 100.0}.get(t))
    monkeypatch.setattr(dy, "fetch_expirations", lambda t: ["2026-10-30", "2026-11-13", "2026-12-18"])
    monkeypatch.setattr(dy, "fetch_chain", lambda t, e, s: mk_chain(s))
    monkeypatch.setattr(dy, "fetch_next_earnings", lambda t: (False, None))
    monkeypatch.setattr(dy, "prewarm", lambda *a, **k: None)


def _click(at, key):
    [b for b in at.button if b.key == key][0].click()
    return at.run(timeout=30)


def test_initial_render_no_exception():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    assert not at.exception
    assert any("סורק PUT / CALL" in t.value for t in at.title)


def test_find_then_scan_both_sides(monkeypatch):
    _patch_ok(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    [r for r in at.radio if r.key == "side_choice"][0].set_value("שניהם")
    at.run()
    at = _click(at, "find_btn")
    assert not at.exception
    uni = at.session_state["universe"]
    assert [t for t, _ in uni] == ["AAA", "BBB"]          # CCC ב-$900 נחתך
    # מרפים כללים כדי שיהיו תוצאות על שרשרת סינתטית
    [n for n in at.number_input if n.key == "dmax"][0].set_value(0.30)
    [n for n in at.number_input if n.key == "dmin"][0].set_value(0.03)
    [c for c in at.checkbox if c.key == "chk_e"][0].set_value(False)
    at.run()
    at = _click(at, "run_btn")
    assert not at.exception, [e.value for e in at.exception]
    out = at.session_state["scan_out"]
    assert {r["Type"] for r in out["rows"]} == {"PUT", "CALL"}
    assert {r["Ticker"] for r in out["rows"]} <= {"AAA", "BBB"}


def test_connection_failure_message(monkeypatch):
    monkeypatch.setattr(uv, "fetch_index_tickers", lambda name, cache_path=None: (["AAA", "BBB"], "web"))
    monkeypatch.setattr(uv, "fetch_last_prices", lambda t: {"AAA": 100.0, "BBB": 50.0})
    monkeypatch.setattr(dy, "fetch_spot", lambda t: None)
    monkeypatch.setattr(dy, "fetch_expirations", lambda t: [])
    monkeypatch.setattr(dy, "prewarm", lambda *a, **k: None)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    at = _click(at, "find_btn")
    at = _click(at, "run_btn")
    assert not at.exception
    assert any("בעיית חיבור" in e.value for e in at.error)
