# -*- coding: utf-8 -*-
"""בדיקות IV מול HV (ללא רשת)."""
import math
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from rules import Rules, evaluate_strike, PASS, FAIL, UNKNOWN
from screener import scan, results_frame, COLUMNS
from data_yf import hv_from_closes, delta_fn, xem_fn
from test_screener import mk_chain, run, EXP


def test_hv_constant_growth_is_zero():
    closes = [100 * (1.01 ** i) for i in range(40)]
    assert abs(hv_from_closes(closes, 30)) < 1e-9


def test_hv_known_value():
    # תשואות לוגריתמיות מתחלפות +1% / -1%: סטיית תקן מדגמית ידועה
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] * math.exp(0.01 if i % 2 == 0 else -0.01))
    hv = hv_from_closes(closes, 30)
    rets = [0.01 if i % 2 == 0 else -0.01 for i in range(30)]
    mean = sum(rets) / 30
    expected = math.sqrt(sum((x - mean) ** 2 for x in rets) / 29) * math.sqrt(252)
    assert abs(hv - expected) < 1e-9


def test_hv_not_enough_data():
    assert hv_from_closes([100.0] * 10, 30) is None
    assert hv_from_closes([], 30) is None


def test_rule_off_by_default():
    assert Rules().min_iv_hv_ratio == 0.0
    res = evaluate_strike({"iv_hv": None, "mid": 1.0, "side": "PUT"}, Rules(check_earnings=False, check_exposure=False))
    assert "IV/HV" not in res


def test_rule_pass_fail_unknown():
    r = Rules(check_earnings=False, check_exposure=False, min_iv_hv_ratio=1.2)
    base = {"mid": 1.0, "side": "PUT"}
    assert evaluate_strike({**base, "iv_hv": 1.5}, r)["IV/HV"][0] == PASS
    assert evaluate_strike({**base, "iv_hv": 1.0}, r)["IV/HV"][0] == FAIL
    assert evaluate_strike({**base, "iv_hv": None}, r)["IV/HV"][0] == UNKNOWN


def test_screen_chain_ratio():
    rows = run("PUT", Rules(check_earnings=False), hv=0.20)
    assert rows and all(abs(r["iv_hv"] - 2.0) < 1e-6 for r in rows)    # IV 40% / HV 20%
    rows = run("PUT", Rules(check_earnings=False))
    assert all(r["iv_hv"] is None for r in rows)


def test_unknown_hv_is_reported_as_not_checked():
    rules = Rules(check_earnings=False, check_exposure=False, delta_min=0, delta_max=0,
                  min_open_interest=0, max_spread_pct=0, min_premium=0, min_iv_hv_ratio=1.2)
    rows = run("PUT", rules)           # אין HV: לא נפסל, אבל מסומן "לא נבדק"
    assert rows and all("IV/HV" not in r["failed"] and "IV/HV" in r["unknown"] for r in rows)
    out = _scan(None, rules)
    assert out["rows"] and all("IV/HV" in r["NotChecked"] for r in out["rows"])


def _scan(hv_fn, rules):
    return scan(["AAA"], date(2026, 11, 13), rules, ["PUT"],
                fetch_spot=lambda t: 100.0, fetch_expirations=lambda t: [EXP],
                fetch_chain=lambda t, e, s: mk_chain(s), earnings_fn=lambda t: (False, None),
                delta_fn=delta_fn, xem_fn=xem_fn, today=date(2026, 10, 14), hv_fn=hv_fn)


def test_scan_columns_and_filter():
    rules = Rules(check_earnings=False, check_exposure=False, delta_min=0.05, delta_max=0.25,
                  min_open_interest=0, max_spread_pct=0)
    df = results_frame(_scan(lambda t: 0.25, rules)["rows"])
    assert list(df.columns) == COLUMNS
    assert (df["HV30_Percent"] == 25.0).all() and (df["IV_HV_Ratio"] == 1.6).all()
    strict = Rules(**{**rules.__dict__, "min_iv_hv_ratio": 2.0})
    assert _scan(lambda t: 0.25, strict)["rows"] == []                # 1.6 < 2.0
    assert _scan(lambda t: 0.25, rules)["rows"]


def test_scan_survives_hv_failure():
    def boom(t):
        raise RuntimeError("x")
    rules = Rules(check_earnings=False, check_exposure=False, delta_min=0.05, delta_max=0.25,
                  min_open_interest=0, max_spread_pct=0)
    out = _scan(boom, rules)
    assert out["rows"] and all(r["IV_HV_Ratio"] is None for r in out["rows"])
