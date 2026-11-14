# -*- coding: utf-8 -*-
"""בדיקות לשונית הניתוח המעמיק (ללא רשת)."""
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import pytest
from streamlit.testing.v1 import AppTest

import data_yf as dy
import deep_tab as dt
import options_engine as oe
import otm_tab
import universe as uv
from rules import Rules
from test_screener import mk_chain


def test_cushion():
    assert dt.cushion_pct(100.0, 90.0) == pytest.approx(10.0)
    assert dt.cushion_pct(100.0, None) is None
    assert dt.cushion_pct(0, 5) is None


def test_exit_plan():
    ep = dt.exit_plan(2.0, "2026-12-18", profit_pct=50, stop_pct=200, alert_dte=21,
                      contracts=2, today=date(2026, 11, 14))
    assert ep["profit_buyback"] == pytest.approx(1.0)
    assert ep["stop_buyback"] == pytest.approx(6.0)          # 3 כפול הפרמיה
    assert ep["profit_usd"] == pytest.approx(200.0)          # (2-1)*100*2
    assert ep["loss_usd"] == pytest.approx(800.0)            # (6-2)*100*2
    assert ep["alert_date"] == date(2026, 11, 27)
    assert ep["days_to_alert"] == 13
    assert dt.exit_plan(None, "2026-12-18") is None
    assert dt.exit_plan(0, "2026-12-18") is None


def test_exit_plan_alert_already_passed():
    ep = dt.exit_plan(1.0, "2026-11-20", alert_dte=21, today=date(2026, 11, 14))
    assert ep["days_to_alert"] < 0


def test_expected_range_put_and_call():
    r = dt.expected_range(100.0, 0.40, 30, 85.0, "PUT")
    move = 100 * 0.40 * (30 / 365) ** 0.5
    assert r["move"] == pytest.approx(move)
    assert r["low1"] == pytest.approx(100 - move) and r["high2"] == pytest.approx(100 + 2 * move)
    assert r["outside_1sd"] == (85.0 < 100 - move)
    assert r["buffer_pct"] == pytest.approx((100 - move - 85.0))
    rc = dt.expected_range(100.0, 0.40, 30, 130.0, "CALL")
    assert rc["buffer_pct"] == pytest.approx(130.0 - (100 + move))
    assert dt.expected_range(100.0, None, 30, 85.0, "PUT") is None
    assert dt.expected_range(100.0, 0.4, 0, 85.0, "PUT") is None


def test_position_greeks_short_signs():
    g = dt.position_greeks("PUT", 100.0, 90.0, 30, 0.40, 0.045, 2, oe)
    assert g["delta_pos"] > 0          # מכירת PUT = דלתא חיובית (כמו החזקת מניה)
    assert g["theta_pos"] > 0          # שחיקת זמן לטובת המוכרת
    assert g["vega_pos"] < 0           # עליית IV מזיקה
    gc = dt.position_greeks("CALL", 100.0, 110.0, 30, 0.40, 0.045, 1, oe)
    assert gc["delta_pos"] < 0
    assert dt.position_greeks("PUT", 100.0, 90.0, 30, None, 0.045, 1, oe) is None


def test_spread_compare():
    sc = dt.spread_compare(90.0, 2.00, 85.0, 0.80, contracts=1)
    assert sc["net_credit"] == pytest.approx(1.20)
    assert sc["width"] == 5.0
    assert sc["max_loss_usd"] == pytest.approx((5.0 - 1.2) * 100)
    assert sc["collateral_usd"] == 500.0
    assert sc["ror_pct"] == pytest.approx(1.2 / 3.8 * 100)
    assert sc["csp_collateral_usd"] == 9000.0
    assert sc["breakeven"] == pytest.approx(88.8)
    assert dt.spread_compare(90.0, 2.0, 95.0, 1.0) is None       # הגנה מעל הסטרייק
    assert dt.spread_compare(90.0, 0.5, 85.0, 0.8) is None       # קרדיט שלילי


def test_rule_lines_pass_fail_unknown():
    row = {"Type": "PUT", "Delta": -0.07, "xEM": None, "PriceUsed": 1.5, "AnnualizedReturn_Percent": 20.0,
           "Spread_Percent": 10, "OpenInterest": 10, "IV_HV_Ratio": None}
    rules = Rules(delta_min=0.05, delta_max=0.10, min_open_interest=50, min_iv_hv_ratio=1.2,
                  min_xem=1.0, check_earnings=True, check_exposure=True)
    got = {n: s for n, s, _ in dt.rule_lines(row, rules)}
    assert got["Delta"] == "pass"
    assert got["Open Interest"] == "fail"
    assert got["IV/HV"] == "unknown"
    assert got["xEM"] == "unknown"
    assert "דוחות" not in got and "חשיפה" not in got


def _patch_all(monkeypatch):
    monkeypatch.setattr(uv, "fetch_index_tickers", lambda name, cache_path=None: (["AAA", "BBB"], "web"))
    monkeypatch.setattr(uv, "fetch_last_prices", lambda t: {"AAA": 100.0, "BBB": 50.0})
    monkeypatch.setattr(dy, "fetch_spot", lambda t: 100.0)
    monkeypatch.setattr(dy, "fetch_expirations", lambda t: ["2026-12-18"])
    monkeypatch.setattr(dy, "fetch_chain", lambda t, e, s: mk_chain(s))
    monkeypatch.setattr(dy, "fetch_next_earnings", lambda t: (False, None))
    monkeypatch.setattr(dy, "fetch_hv", lambda t, window=30: 0.25)
    monkeypatch.setattr(dy, "prewarm", lambda *a, **k: None)
    monkeypatch.setattr(otm_tab, "_try_fetch_price_history", lambda t: None)


def _click(at, key):
    [b for b in at.button if b.key == key][0].click()
    return at.run(timeout=30)


def test_deep_tab_flow(monkeypatch):
    _patch_all(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    # לפני סריקה: הודעה ולא קריסה
    assert any("אין חוזים לניתוח" in i.value for i in at.info)
    [r for r in at.radio if r.key == "side_choice"][0].set_value("שניהם")
    at.run()
    at = _click(at, "find_btn")
    [n for n in at.number_input if n.key == "dmax"][0].set_value(0.30)
    [n for n in at.number_input if n.key == "dmin"][0].set_value(0.03)
    [c for c in at.checkbox if c.key == "chk_e"][0].set_value(False)
    at.run()
    at = _click(at, "run_btn")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["scan_out"]["rows"]
    # בוחרים חוזה ולוחצים "נתחי"
    at = _click(at, "deep_pick_btn")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["deep_ticker"] in ("AAA", "BBB")
    texts = " ".join(m.value for m in at.markdown)
    assert "כסף" in texts and "ניהול העסקה" in texts and "הכללים שלי" in texts
    # מחירי יציאה מופיעים כמדדים
    labels = [m.label for m in at.metric]
    assert any("סטופ" in l for l in labels) and any("כרית ביטחון" in l for l in labels)


def test_deep_tab_switch_to_call(monkeypatch):
    _patch_all(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    [r for r in at.radio if r.key == "side_choice"][0].set_value("שניהם")
    at.run()
    at = _click(at, "find_btn")
    [n for n in at.number_input if n.key == "dmax"][0].set_value(0.30)
    [n for n in at.number_input if n.key == "dmin"][0].set_value(0.03)
    [c for c in at.checkbox if c.key == "chk_e"][0].set_value(False)
    at.run()
    at = _click(at, "run_btn")
    rows = at.session_state["scan_out"]["rows"]
    ci = next(i for i, r in enumerate(rows) if r["Type"] == "CALL")
    at.session_state["deep_ticker"] = rows[ci]["Ticker"]
    at.session_state["deep_contract"] = ci
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_rows_from_otm_converts_and_dedupes():
    df = otm_tab.build_strike_compare("AAA", 100.0, "2026-12-18", mk_chain("PUT"), closes=None,
                                      delta_fn=dy.delta_fn, engine=oe, today=date(2026, 11, 14))
    frames = {"AAA": {"df": df, "price": 100.0, "expiry": "2026-12-18"}, "BAD": {"error": "x"}}
    rows = dt.rows_from_otm(frames)
    assert rows and all(r["Type"] == "PUT" and r["Ticker"] == "AAA" and r["UnderlyingPrice"] == 100.0 for r in rows)
    r0 = rows[0]
    assert r0["AnnualizedReturn_Percent"] is None or r0["AnnualizedReturn_Percent"] > 0
    assert all(v == v for r in rows for v in r.values() if isinstance(v, float))   # בלי NaN
    # כפילות עם שורה קיימת מדולגת
    again = dt.rows_from_otm(frames, existing=[rows[0]])
    assert len(again) == len(rows) - 1


def test_deep_tab_from_otm_data_without_scan(monkeypatch):
    _patch_all(monkeypatch)
    df = otm_tab.build_strike_compare("AAA", 100.0, "2026-12-18", mk_chain("PUT"), closes=None,
                                      delta_fn=dy.delta_fn, engine=oe, today=date(2026, 11, 14))
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30)
    at.session_state["otm_data"] = {"frames": {"AAA": {"df": df, "price": 100.0, "expiry": "2026-12-18"}}}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["deep_ticker"] == "AAA"
    texts = " ".join(m.value for m in at.markdown)
    assert "ניהול העסקה" in texts


def test_manual_tickers_button_sends_to_otm(monkeypatch):
    _patch_all(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    [t for t in at.text_area if t.key == "manual_t"][0].set_value("aaa, bbb")
    at.run()
    at = _click(at, "manual_analyze_btn")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["otm_tickers"] == ["AAA", "BBB"]
    frames = at.session_state["otm_data"]["frames"]
    assert set(frames) == {"AAA", "BBB"}


def test_manual_button_with_empty_box_is_harmless(monkeypatch):
    _patch_all(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30).run()
    at = _click(at, "manual_analyze_btn")
    assert not at.exception, [e.value for e in at.exception]


def test_otm_to_deep_button(monkeypatch):
    _patch_all(monkeypatch)
    df = otm_tab.build_strike_compare("AAA", 100.0, "2026-12-18", mk_chain("PUT"), closes=None,
                                      delta_fn=dy.delta_fn, engine=oe, today=date(2026, 11, 14))
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30)
    at.session_state["otm_data"] = {"frames": {"AAA": {"df": df, "price": 100.0, "expiry": "2026-12-18"}}}
    at.run()
    at = _click(at, "otm_to_deep_btn")
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["deep_ticker"] == "AAA"
