# -*- coding: utf-8 -*-
"""בדיקות מעקב החוזים (ללא רשת)."""
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import pytest
from streamlit.testing.v1 import AppTest

import data_yf as dy
import options_engine as oe
import otm_tab
import tracker_tab as tt
import universe as uv
from test_screener import mk_chain


def _pos(**kw):
    base = dict(ticker="aaa", side="put", strike=90.0, expiry="2026-12-18", entry_price=100.0, entry_mid=2.0,
                contracts=2, today=date(2026, 11, 14))
    base.update(kw)
    t, s, k, e = base.pop("ticker"), base.pop("side"), base.pop("strike"), base.pop("expiry")
    return tt.new_position(t, s, k, e, **base)


def test_new_position_and_id():
    p = _pos()
    assert p["id"] == "AAA-PUT-90-2026-12-18" and p["ticker"] == "AAA" and p["side"] == "PUT"
    assert p["entry_date"] == "2026-11-14" and p["entry_mid"] == 2.0
    assert _pos(entry_mid=0)["entry_mid"] is None


def test_add_replace_remove_merge():
    a, b = _pos(), _pos(strike=85.0)
    items = tt.add_position(tt.add_position([], a), b)
    assert len(items) == 2
    items = tt.add_position(items, _pos(entry_mid=3.0))            # אותו חוזה מוחלף
    assert len(items) == 2 and [p for p in items if p["strike"] == 90.0][0]["entry_mid"] == 3.0
    assert len(tt.remove_position(items, a["id"])) == 1
    merged = tt.merge_positions(items, [_pos(strike=80.0), {"junk": 1}, "x"])
    assert len(merged) == 3


def test_save_load_roundtrip(tmp_path):
    f = str(tmp_path / "t.json")
    items = [_pos(), _pos(strike=85.0)]
    assert tt.save_positions(items, f)
    assert tt.load_positions(f) == items
    assert tt.load_positions(str(tmp_path / "missing.json")) == []
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    assert tt.load_positions(str(tmp_path / "bad.json")) == []


def test_evaluate_put_in_progress():
    p = _pos()
    ev = tt.evaluate_position(p, price=104.0, mid=0.8, today=date(2026, 12, 1))
    assert ev["days_left"] == 17 and not ev["expired"]
    assert ev["change_pct"] == pytest.approx(4.0)
    assert ev["dist_entry"] == pytest.approx(10.0)
    assert ev["dist_now"] == pytest.approx((104 - 90) / 104 * 100)
    assert ev["pnl_usd"] == pytest.approx((2.0 - 0.8) * 100 * 2)
    assert ev["kept_pct"] == pytest.approx(60.0)
    assert any("יעד 50%" in f for f in ev["flags"])               # 60% >= 50%
    assert any("ימים לפקיעה" in f for f in ev["flags"])           # 17 <= 21


def test_evaluate_flags_itm_and_stop():
    p = _pos()
    ev = tt.evaluate_position(p, price=88.0, mid=6.5, today=date(2026, 11, 20))
    assert "בתוך הכסף" in ev["flags"]
    assert any("סטופ" in f for f in ev["flags"])                  # 6.5 >= 2*(1+2)=6
    assert ev["dist_now"] < 0


def test_evaluate_unknown_premium_and_call():
    p = _pos(entry_mid=0)
    ev = tt.evaluate_position(p, price=100.0, mid=None, today=date(2026, 11, 20))
    assert ev["pnl_usd"] is None and ev["kept_pct"] is None
    c = _pos(side="call", strike=110.0)
    evc = tt.evaluate_position(c, price=112.0, mid=3.0, today=date(2026, 11, 20))
    assert "בתוך הכסף" in evc["flags"] and evc["dist_now"] < 0


def test_expired_outcome():
    p = _pos()
    ev = tt.evaluate_position(p, price=95.0, mid=None, today=date(2026, 12, 20), close_at_expiry=95.0)
    assert ev["expired"] and "פקע מחוץ לכסף" in ev["outcome"]
    ev2 = tt.evaluate_position(p, price=85.0, mid=None, today=date(2026, 12, 20), close_at_expiry=85.0)
    assert "בתוך הכסף" in ev2["outcome"] and "5.00" in ev2["outcome"]
    assert tt.expiry_outcome(p, None) is None


def _patch_all(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(uv, "fetch_index_tickers", lambda name, cache_path=None: (["AAA"], "web"))
    monkeypatch.setattr(uv, "fetch_last_prices", lambda t: {"AAA": 100.0})
    monkeypatch.setattr(dy, "fetch_spot", lambda t: 103.0)
    monkeypatch.setattr(dy, "fetch_expirations", lambda t: ["2026-12-18"])
    monkeypatch.setattr(dy, "fetch_chain", lambda t, e, s: mk_chain(s))
    monkeypatch.setattr(dy, "fetch_next_earnings", lambda t: (False, None))
    monkeypatch.setattr(dy, "fetch_hv", lambda t, window=30: 0.25)
    monkeypatch.setattr(dy, "prewarm", lambda *a, **k: None)
    monkeypatch.setattr(otm_tab, "_try_fetch_price_history", lambda t: None)


def test_tracker_tab_empty_and_flow(monkeypatch, tmp_path):
    _patch_all(monkeypatch, tmp_path)
    df = otm_tab.build_strike_compare("AAA", 100.0, "2026-12-18", mk_chain("PUT"), closes=None,
                                      delta_fn=dy.delta_fn, engine=oe, today=date(2026, 11, 14))
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30)
    at.session_state["otm_data"] = {"frames": {"AAA": {"df": df, "price": 100.0, "expiry": "2026-12-18"}}}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("אין חוזים במעקב" in i.value for i in at.info)
    # מוסיפים מהניתוח המעמיק
    at = at.run()
    [b for b in at.button if b.key == "deep_track_btn"][0].click()
    at.run(timeout=30)
    assert not at.exception, [e.value for e in at.exception]
    items = at.session_state["track_items"]
    assert len(items) == 1 and items[0]["ticker"] == "AAA" and items[0]["side"] == "PUT"
    assert os.path.exists(tmp_path / "tracking.json")              # נשמר לקובץ
    assert not any("אין חוזים במעקב" in i.value for i in at.info)
