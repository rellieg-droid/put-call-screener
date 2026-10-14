"""
options_engine.py
=================
מנוע החישוב של RiskShield. מודול עצמאי לחלוטין:
אין בו Streamlit, אין yfinance, אין מצב גלובלי.
מקבל מספרים, מחזיר מספרים. ניתן לבדיקה מלאה ב-pytest.

תלויות: ספריית התקן של פייתון בלבד.
מקור אמת (source of truth): הקובץ הזה, ב-rellieg-droid/stock-trading-app.
הריפו options-analysis כבר לא בשימוש - אין סנכרון ידני לשום מקום.

מבנה
----
1. Black-Scholes  : מחיר וגריקס, IV/RV, RV Rank, תזוזה צפויה, הסתברות היסטורית ו-Fat-tail
2. היפוך דלתא     : סטרייק מדלתא רצויה (מחליף את current_price * (1 - delta))
3. מודל הרגליים   : Leg / Position - payoff, max_loss, max_profit, breakevens
4. תבניות         : אסטרטגיות מוכנות (כולל bull_put_spread) + טבלת הגנה
5. מנוע הסיכון    : חוק ה-1% + רצפת תיק, שני דליים נפרדים
6. סטרס ו-CVaR    : תרחישי ירידה והפסד בזנב לשורט-פוט
7. סורק רב-מניות  : שורה לכל טיקר, מעל הפונקציות הקיימות
8. ציטוט שוק      : Mid/מרווח מ-bid/ask, הסטרייק הקרוב בשרשרת
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, log, sqrt, isfinite
from statistics import NormalDist
from typing import Literal, Sequence

_N = NormalDist()          # התפלגות נורמלית תקנית
CONTRACT_MULTIPLIER = 100  # חוזה אופציה אחד = 100 מניות

Kind = Literal["stock", "call", "put"]


def norm_cdf(x: float) -> float:
    """התפלגות נורמלית תקנית מצטברת. נחשף כדי שקוד קיים יוכל לייבא במקום להגדיר."""
    return _N.cdf(x)


# =============================================================================
# 1. BLACK-SCHOLES
# =============================================================================

def _d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float):
    """מחזיר (d1, d2). קלט חייב להיות חיובי ממש."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        raise ValueError(f"S, K, T, sigma must all be > 0 (got S={S}, K={K}, T={T}, sigma={sigma})")
    vol_t = sigma * sqrt(T)
    d1 = (log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / vol_t
    return d1, d1 - vol_t


@dataclass(frozen=True)
class Greeks:
    """גריקס בקונבנציות שמוצגות למשתמש (לא ביחידות גולמיות)."""
    price: float    # מחיר האופציה למניה אחת
    delta: float    # שינוי במחיר האופציה על כל $1 בנכס הבסיס
    gamma: float    # שינוי בדלתא על כל $1
    vega: float     # שינוי במחיר על כל 1% שינוי ב-IV
    theta: float    # שחיקה יומית בדולרים (ליום קלנדרי)
    prob_otm: float  # הסתברות ניטרלית לסיכון לפקיעה מחוץ לכסף


def bs_greeks(
    kind: Kind,
    S: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.045,
    q: float = 0.0,
) -> Greeks:
    """
    מחיר וגריקס לאופציה אירופאית.

    kind  : "call" או "put"
    S     : מחיר הנכס
    K     : סטרייק
    T     : זמן לפקיעה בשנים (30 יום = 30/365)
    sigma : תנודתיות גלומה כשבר עשרוני (54% -> 0.54)
    r     : ריבית חסרת סיכון
    q     : תשואת דיבידנד שנתית
    """
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    disc_r, disc_q = exp(-r * T), exp(-q * T)
    pdf_d1 = _N.pdf(d1)
    vol_t = sigma * sqrt(T)

    # רכיבים משותפים
    gamma = disc_q * pdf_d1 / (S * vol_t)
    vega = S * disc_q * pdf_d1 * sqrt(T) / 100.0          # ל-1% שינוי ב-IV
    theta_common = -S * disc_q * pdf_d1 * sigma / (2 * sqrt(T))

    if kind == "call":
        price = S * disc_q * _N.cdf(d1) - K * disc_r * _N.cdf(d2)
        delta = disc_q * _N.cdf(d1)
        theta_yr = theta_common - r * K * disc_r * _N.cdf(d2) + q * S * disc_q * _N.cdf(d1)
        prob_otm = _N.cdf(-d2)      # P(S_T < K)
    else:
        price = K * disc_r * _N.cdf(-d2) - S * disc_q * _N.cdf(-d1)
        delta = -disc_q * _N.cdf(-d1)
        theta_yr = theta_common + r * K * disc_r * _N.cdf(-d2) - q * S * disc_q * _N.cdf(-d1)
        prob_otm = _N.cdf(d2)       # P(S_T > K)

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta_yr / 365.0,
        prob_otm=prob_otm,
    )


# =============================================================================
# 1b. תנודתיות: גלומה מול ממומשת
# =============================================================================

def implied_vol(
    kind: Kind,
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = 0.045,
    q: float = 0.0,
    lo: float = 1e-4,
    hi: float = 6.0,
    tol: float = 1e-8,
    max_iter: int = 200,
) -> float:
    """
    התנודתיות שמייצרת התאמה בין המודל למחיר שראית בשוק.
    חציית ביסקציה. מחיר האופציה עולה מונוטונית ב-sigma, לכן החיפוש יציב.
    """
    if market_price <= 0:
        raise ValueError(f"market_price must be > 0, got {market_price}")
    if not 0 < lo < hi:
        raise ValueError("need 0 < lo < hi")

    price = lambda v: bs_greeks(kind, S, K, T, v, r, q).price
    if market_price > price(hi):
        raise ValueError(
            f"price ${market_price:,.4f} exceeds the model at sigma={hi:.0%}; "
            "widen hi or check the quote"
        )
    if market_price < price(lo):
        raise ValueError(
            f"price ${market_price:,.4f} is below the model at sigma={lo:.2%} "
            f"(~${price(lo):,.4f}); check the quote or the call/put selection"
        )

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if hi - lo < tol:
            return mid
        if price(mid) < market_price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def realized_vol(closes: Sequence[float], window: int = 252,
                 periods_per_year: int = 252) -> float:
    """
    תנודתיות ממומשת מהיסטוריית המחירים עצמה, במונחים שנתיים.
    זה הצד ה-RV בתנאי הכניסה IV > RV.
    סדרה שטוחה לגמרי מחזירה 0.0.
    """
    tail = list(closes[-window:]) if len(closes) > window else list(closes)
    if len(tail) < 3:
        return 0.0
    if any(c <= 0 for c in tail):
        raise ValueError("closes must all be positive")

    rets = [log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return sqrt(var * periods_per_year)


def iv_rv_ratio(iv: float, rv: float) -> float:
    """
    יחס IV/RV. מעל 1 אומר שהשוק מתמחר יותר תנודתיות ממה שהמניה עשתה בפועל,
    וזה מה שמצדיק מכירת תנודתיות. rv אפס מחזיר אינסוף.
    """
    return float("inf") if rv <= 0 else iv / rv


# =============================================================================
# 1d. RV Rank / RV Percentile - תחליף זמני ומוצהר ל-IV Rank
# =============================================================================
#
# אין ל-yfinance/Yahoo ארכיון היסטורי של IV (הם נותנים רק שרשרת אופציות
# נוכחית - חוזים ישנים נעלמים ברגע שהם פוקעים). RV, לעומת זאת, נגזר ממחיר
# המניה שיש לו ארכיון מלא. לכן RV Rank זמין מיד, אבל הוא מודד דבר אחר
# (מה שקרה בפועל) לעומת IV Rank (מה שהשוק מצפה) - חובה לתייג בהתאם בממשק.


def _rolling_rv_series(
    closes: Sequence[float],
    vol_window: int = 20,
    lookback_days: int = 252,
) -> list[float]:
    """סדרת RV מתגלגלת: לכל יום בטווח ה-lookback, ה-RV שחושב מ-vol_window הימים שקדמו לו."""
    needed = lookback_days + vol_window
    tail = list(closes[-needed:]) if len(closes) >= needed else list(closes)
    series = []
    for i in range(vol_window, len(tail)):
        window_slice = tail[i - vol_window: i + 1]
        series.append(realized_vol(window_slice, window=vol_window))
    return series


def rv_rank(
    closes: Sequence[float],
    vol_window: int = 20,
    lookback_days: int = 252,
) -> float | None:
    """
    (RV הנוכחי - RV מינימלי בטווח) / (RV מקסימלי - RV מינימלי) * 100.
    None אם אין מספיק נתונים לבניית סדרה, או שה-RV לא זז כלל בטווח (max==min).
    """
    series = _rolling_rv_series(closes, vol_window, lookback_days)
    if len(series) < 2:
        return None
    current, lo, hi = series[-1], min(series), max(series)
    if hi == lo:
        return None
    return (current - lo) / (hi - lo) * 100.0


def rv_percentile(
    closes: Sequence[float],
    vol_window: int = 20,
    lookback_days: int = 252,
) -> float | None:
    """אחוז הימים בטווח שבהם ה-RV היה נמוך או שווה ל-RV הנוכחי. None אם אין מספיק נתונים."""
    series = _rolling_rv_series(closes, vol_window, lookback_days)
    if len(series) < 2:
        return None
    current = series[-1]
    count_leq = sum(1 for v in series if v <= current)
    return count_leq / len(series) * 100.0


# =============================================================================
# 1e. תזוזה צפויה (Expected Move) ומרחק הסטרייק ממנה
# =============================================================================
#
# קירוב מהנוסחה הסטנדרטית S*IV*sqrt(T), לא מ-ATM straddle (מדויק יותר אבל
# דורש מחירי אופציות בפועל - לא זמין כאן כרגע). מתויג ככזה בפלט.


@dataclass(frozen=True)
class ExpectedMove:
    move: float          # תזוזה צפויה בדולרים (סטיית תקן אחת, לא טווח מובטח)
    low: float           # S - move
    high: float          # S + move
    method: str = "formula"  # "formula" (S*IV*sqrt(T)) לעומת "straddle" (לא ממומש עדיין)


def expected_move(S: float, sigma: float, T_days: float) -> ExpectedMove:
    """
    תזוזה צפויה = S * sigma * sqrt(T בשנים). זו סטיית תקן אחת - כ-68% מהמקרים
    (בהנחת התפלגות נורמלית, שכבר ידוע לנו שלא תמיד מחזיקה - ראו מודל Fat-tail).
    """
    if S <= 0:
        raise ValueError("S must be positive")
    if sigma < 0:
        raise ValueError("sigma must be non-negative")
    if T_days < 0:
        raise ValueError("T_days must be non-negative")
    move = S * sigma * sqrt(T_days / 365.0)
    return ExpectedMove(move=move, low=S - move, high=S + move, method="formula")


def strike_distance_in_expected_moves(S: float, K: float, move: float) -> float | None:
    """
    כמה 'תזוזות צפויות' מרוחק הסטרייק מהמחיר הנוכחי. חיובי = הסטרייק מתחת
    למחיר (רלוונטי לפוט). None אם move==0 (למשל IV=0 או T=0) - לא חלוקה באפס.
    """
    if move == 0:
        return None
    return (S - K) / move


# =============================================================================
# 1c. הסתברות היסטורית ו-Fat-tail
# =============================================================================
#
# עקרון: כל מודל מוצג בנפרד. אין מיזוג למספר/ציון יחיד, ואין "המלצה" -
# ראי /projects/.../ways-of-working.md: "RiskShield rejects composite scores...
# displays raw metrics separately".

from math import lgamma


@dataclass(frozen=True)
class HistoricalProbability:
    """הסתברות היסטורית ל-OTM של פוט, לפי חלון זמן. לא ממוזג לציון יחיד."""
    by_period: dict            # שנות-לוקבאק -> הסתברות OTM (או None אם אין מספיק נתונים)
    weighted_otm: float | None  # ממוצע משוקלל של התקופות הזמינות בלבד, מוצג לצד הפירוט
    periods_available: int
    note: str = ""


def historical_put_otm_probability(
    closes: Sequence[float],
    strike: float,
    dte_days: int,
    trading_days_per_year: int = 252,
) -> HistoricalProbability:
    """
    לכל יום מסחר היסטורי: האם המחיר dte_days ימי-מסחר קדימה נשאר מעל הסטרייק (OTM)?
    מחושב בנפרד לחלונות 1/3/5/10 שנים אחרונות (משתמש בכל ההיסטוריה הזמינה בכל חלון).

    אם אין מספיק היסטוריה לתקופה מסוימת - היא None, לא מומצאת.
    ה-weighted_otm משוקלל רק על התקופות הזמינות (40/30/20/10 מנורמל מחדש),
    ומוצג *לצד* הפירוט המלא, לא במקומו.
    """
    periods_years = (1, 3, 5, 10)
    base_weights = {1: 0.40, 3: 0.30, 5: 0.20, 10: 0.10}
    n = len(closes)
    by_period: dict = {}

    for years in periods_years:
        window_days = years * trading_days_per_year
        needed = window_days + dte_days
        if n < needed:
            # תיקון: אין מספיק היסטוריה לתקופה הזו - None, לא נפילה חזרה
            # לדאטה חלקי שמתחזה לתקופה שלא קיימת בפועל.
            by_period[years] = None
            continue
        tail = closes[-needed:]
        total = len(tail) - dte_days
        breaches = sum(1 for i in range(total) if tail[i + dte_days] <= strike)
        by_period[years] = 1.0 - (breaches / total)

    available = {y: p for y, p in by_period.items() if p is not None}
    if not available:
        return HistoricalProbability(
            by_period, None, 0, "אין מספיק היסטוריה לחישוב הסתברות היסטורית"
        )

    weight_sum = sum(base_weights[y] for y in available)
    weighted = sum(base_weights[y] * p for y, p in available.items()) / weight_sum

    note = ""
    if len(available) < len(periods_years):
        note = (
            f"היסטוריה זמינה: {len(available)} מתוך {len(periods_years)} תקופות "
            "(1/3/5/10 שנים). ביטחון מופחת בהתאם."
        )

    return HistoricalProbability(by_period, weighted, len(available), note)


# ---- Fat-tail (Student-t), ללא scipy ------------------------------------

def _betacf(a: float, b: float, x: float, max_iter: int = 200, eps: float = 1e-12) -> float:
    """שבר משולב לפונקציית הבטא הבלתי-שלמה (Numerical Recipes, betacf)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log(1.0 - x)
    front = exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(x: float, df: float) -> float:
    """
    CDF של התפלגות t-Student, בלי scipy - דרך הבטא הבלתי-שלמה המוסדרת
    (math.gamma/lgamma הם ספריית תקן, אין תלות חיצונית).
    """
    if df <= 0:
        raise ValueError(f"df must be > 0, got {df}")
    x_beta = df / (df + x * x)
    ib = _regularized_incomplete_beta(x_beta, df / 2.0, 0.5)
    return 1.0 - 0.5 * ib if x >= 0 else 0.5 * ib


@dataclass(frozen=True)
class FatTailProbability:
    """הסתברות ל-OTM לפי מודל Fat-tail. קירוב מוצהר, לא מדויק - ראי note."""
    otm_probability: float | None
    degrees_of_freedom: float | None
    excess_kurtosis: float | None
    note: str = ""


def fat_tail_otm_probability(
    daily_log_returns: Sequence[float],
    S: float,
    K: float,
    dte_days: int,
    min_observations: int = 30,
) -> FatTailProbability:
    """
    הסתברות ל-OTM של פוט לפי התפלגות t-Student, עם דרגות חופש שנאמדות
    מהקורטוזיס העודף של התשואות היומיות ההיסטוריות (שיטת המומנטים).

    קירוב מוצהר: קנה מידה לאופק T ימים לפי sqrt(T) (לא מדויק סטטיסטית עבור
    סכום משתני t, אך נהוג כקירוב סביר - בדיוק כמו Probability of Touch).
    למניפים ממונפים (SOXL וכו') מומלץ משקל גבוה יותר למודל הזה על פני Normal,
    לפי ההנחיה הקיימת בסקילים.
    """
    n = len(daily_log_returns)
    if n < min_observations:
        return FatTailProbability(
            None, None, None,
            f"אין מספיק תצפיות היסטוריות למודל Fat-tail (יש {n}, נדרשים {min_observations}+)",
        )

    mean = sum(daily_log_returns) / n
    var = sum((r - mean) ** 2 for r in daily_log_returns) / (n - 1)
    if var <= 0:
        return FatTailProbability(None, None, None, "שונות התשואות היומיות אפס - לא ניתן לחשב")

    m4 = sum((r - mean) ** 4 for r in daily_log_returns) / n
    excess_kurtosis = (m4 / (var ** 2)) - 3.0

    if excess_kurtosis <= 0:
        df = 30.0  # אין עודף קורטוזיס מובהק - קרוב לנורמלי, df גבוה
    else:
        df = max(6.0 / excess_kurtosis + 4.0, 2.1)  # df>2 נדרש לשונות סופית

    std = sqrt(var)
    horizon_mean = mean * dte_days
    horizon_std = std * sqrt(dte_days)
    if horizon_std <= 0:
        return FatTailProbability(None, df, excess_kurtosis, "סטיית תקן לאופק אפס")

    log_strike_return = log(K / S)
    t_stat = (log_strike_return - horizon_mean) / horizon_std
    prob_itm = student_t_cdf(t_stat, df)
    prob_otm = 1.0 - prob_itm

    return FatTailProbability(prob_otm, df, excess_kurtosis, "")


# =============================================================================
# 2. היפוך דלתא -> סטרייק
# =============================================================================

def strike_from_delta(
    kind: Kind,
    target_delta: float,
    S: float,
    T: float,
    sigma: float,
    r: float = 0.045,
    q: float = 0.0,
    round_to: float | None = None,
) -> float:
    """
    הסטרייק שנותן את הדלתא המבוקשת.
    מחליף את current_price * (1 - delta) שהיה בפרוטוטייפ.

    target_delta : גודל מוחלט, 0 עד 1. עבור פוט מוחזר סטרייק מתחת ל-S.
    round_to     : עיגול לרשת סטרייקים אמיתית (0.5 / 1.0 / 5.0). None = מדויק.
    """
    d = abs(target_delta)
    if not 0 < d < 1:
        raise ValueError(f"target_delta must be strictly between 0 and 1, got {target_delta}")
    if S <= 0 or T <= 0 or sigma <= 0:
        raise ValueError(f"S, T, sigma must all be > 0 (got S={S}, T={T}, sigma={sigma})")

    adj = d * exp(q * T)  # ניטרול הדיסקאונט של הדיבידנד
    if not 0 < adj < 1:
        raise ValueError(
            f"delta {target_delta} unreachable with q={q}, T={T} (adjusted={adj:.4f})"
        )

    # call : delta = e^-qT * N(d1)   -> d1 =  N^-1(adj)
    # put  : delta = -e^-qT * N(-d1) -> d1 = -N^-1(adj)
    d1 = _N.inv_cdf(adj) * (1 if kind == "call" else -1)

    K = S * exp((r - q + 0.5 * sigma ** 2) * T - d1 * sigma * sqrt(T))
    if not isfinite(K) or K <= 0:
        raise ValueError("strike solution diverged; check inputs")

    return round(K / round_to) * round_to if round_to else K


# =============================================================================
# 3. מודל הרגליים
# =============================================================================

@dataclass(frozen=True)
class Leg:
    """
    רגל בודדת בפוזיציה.

    direction : +1 לונג (קניתי), -1 שורט (כתבתי)
    qty       : מניות עבור stock, חוזים עבור call/put
    entry     : מחיר כניסה למניה אחת. באופציה זו הפרמיה, לא כפול 100.
    """
    kind: Kind
    direction: int
    qty: float
    entry: float
    strike: float | None = None
    label: str = ""

    def __post_init__(self):
        if self.direction not in (1, -1):
            raise ValueError(f"direction must be +1 or -1, got {self.direction}")
        if self.qty <= 0:
            raise ValueError(f"qty must be > 0, got {self.qty}")
        if self.kind == "stock":
            if self.strike is not None:
                raise ValueError("stock leg must not have a strike")
        elif self.kind in ("call", "put"):
            if self.strike is None or self.strike <= 0:
                raise ValueError(f"{self.kind} leg needs a positive strike")
        else:
            raise ValueError(f"unknown kind {self.kind!r}")

    @property
    def multiplier(self) -> float:
        return 1.0 if self.kind == "stock" else CONTRACT_MULTIPLIER

    @property
    def cash_flow(self) -> float:
        """תזרים בפתיחה. שלילי = שילמתי, חיובי = קיבלתי."""
        return -self.direction * self.qty * self.entry * self.multiplier

    def payoff_at(self, spot: float) -> float:
        """רווח/הפסד בדולרים בפקיעה, במחיר spot נתון."""
        if self.kind == "stock":
            intrinsic = spot
        elif self.kind == "call":
            intrinsic = max(spot - self.strike, 0.0)
        else:
            intrinsic = max(self.strike - spot, 0.0)
        return self.direction * self.qty * self.multiplier * (intrinsic - self.entry)


@dataclass
class Position:
    """אוסף רגליים = אסטרטגיה. כל האסטרטגיות עוברות דרך המחלקה הזו."""
    legs: list[Leg] = field(default_factory=list)
    name: str = ""
    underlying: str = ""

    # ---- תזרים ----
    @property
    def net_cash(self) -> float:
        """תזרים נטו בפתיחה. חיובי = עסקת קרדיט."""
        return sum(leg.cash_flow for leg in self.legs)

    @property
    def is_hedge(self) -> bool:
        """פוזיציה שמכילה מניה מוגדרת כהגנה, לא כספקולציה."""
        return any(leg.kind == "stock" for leg in self.legs)

    # ---- עקומת תשלום ----
    def payoff_at(self, spot: float) -> float:
        return sum(leg.payoff_at(spot) for leg in self.legs)

    def payoff_curve(self, lo: float, hi: float, points: int = 400):
        """מחזיר (spots, pnl) לגרף Plotly. נקודות השבירה תמיד נכללות."""
        if hi <= lo:
            raise ValueError("hi must be greater than lo")
        step = (hi - lo) / (points - 1)
        grid = {lo + i * step for i in range(points)}
        grid |= {k for k in self._kinks() if lo <= k <= hi}
        spots = sorted(grid)
        return spots, [self.payoff_at(s) for s in spots]

    def _kinks(self) -> list[float]:
        """נקודות השבירה: כל הסטרייקים ואפס. הפונקציה קווית למקוטעין ביניהן."""
        return [0.0] + sorted({leg.strike for leg in self.legs if leg.strike is not None})

    # ---- שיפועים בקצוות ----
    def _slope_right(self) -> float:
        """שיפור לכל $1 מעל הסטרייק הגבוה ביותר."""
        return sum(
            leg.direction * leg.qty * leg.multiplier
            for leg in self.legs
            if leg.kind in ("stock", "call")
        )

    # ---- סיכון ----
    def max_loss(self) -> float:
        """
        הפסד מקסימלי בדולרים כמספר חיובי.
        float('inf') = סיכון בלתי מוגדר (שורט קול לא מכוסה).
        הפונקציה קווית למקוטעין, לכן המינימום נמצא תמיד בנקודת שבירה.
        """
        if self._slope_right() < 0:
            return float("inf")
        candidates = self._kinks()
        upper = max(candidates) * 3 + 1
        worst = min(self.payoff_at(s) for s in candidates + [upper])
        return max(-worst, 0.0)

    def max_profit(self) -> float:
        if self._slope_right() > 0:
            return float("inf")
        candidates = self._kinks()
        upper = max(candidates) * 3 + 1
        return max(self.payoff_at(s) for s in candidates + [upper])

    def breakevens(self, tol: float = 1e-9) -> list[float]:
        """נקודות איזון. חיפוש ליניארי בין נקודות שבירה סמוכות."""
        pts = sorted(set(self._kinks() + [max(self._kinks()) * 3 + 1]))
        out = []
        for a, b in zip(pts, pts[1:]):
            fa, fb = self.payoff_at(a), self.payoff_at(b)
            if abs(fa) < tol:
                out.append(a)
            elif fa * fb < 0:
                out.append(a + (b - a) * fa / (fa - fb))  # אינטרפולציה ליניארית
        return sorted(set(round(x, 6) for x in out))

    def floor_value(self, shares_price: float) -> float:
        """
        שווי הרצפה של פוזיציה מגודרת: כמה הפוזיציה שווה בקריסה מוחלטת.
        זה המדד הרלוונטי לביטוח, לא max_loss.
        """
        stock_value = sum(
            leg.direction * leg.qty * shares_price
            for leg in self.legs if leg.kind == "stock"
        )
        return stock_value - self.max_loss()


# =============================================================================
# 4. תבניות אסטרטגיה
# =============================================================================

def protective_put(shares: int, stock_entry: float, put_strike: float,
                   put_premium: float, contracts: int | None = None) -> Position:
    """מניה + פוט מגן. ברירת מחדל: חוזה אחד לכל 100 מניות."""
    contracts = contracts if contracts is not None else shares / CONTRACT_MULTIPLIER
    return Position(
        name="Protective Put",
        legs=[
            Leg("stock", +1, shares, stock_entry, label="מניות"),
            Leg("put", +1, contracts, put_premium, put_strike, label="ביטוח"),
        ],
    )


# =============================================================================
# 4b. טבלת הגנה - כמה PUT מגן שווה בפועל, בדולרים, בכל תרחיש
# =============================================================================
#
# בונה על protective_put()/Position/Leg הקיימים. אין כאן ציון "הגנה", אין
# "הגנה הכי טובה" - רק המספרים הגולמיים בכל תרחיש, כמו שכל שאר הטבלאות
# בקובץ הזה עובדות.

_DEFAULT_PROTECTION_MOVES = (0.0, -0.10, -0.20, -0.30, -0.40, -0.50, -0.60)


@dataclass(frozen=True)
class ProtectionRow:
    pct_move: float
    stressed_price: float
    unhedged_pnl: float       # P/L של מניה בלבד, בלי ביטוח
    hedged_pnl: float         # P/L של מניה + פוט מגן
    protection_amount: float  # hedged_pnl - unhedged_pnl: כמה ההגנה שיפרה את התוצאה
    protection_pct: float | None  # % מההפסד (הלא-מוגן) שקוזז. None אם לא הייתה הפסד מלכתחילה


def protection_table(
    shares: int,
    stock_entry: float,
    strike: float,
    premium: float,
    contracts: int | None = None,
    pct_moves: Sequence[float] = _DEFAULT_PROTECTION_MOVES,
) -> list[ProtectionRow]:
    """
    משווה P/L של 'מניה בלבד' מול 'מניה + פוט מגן' על פני סדרת תרחישי ירידה.
    protection_pct מחושב רק כשהייתה הפסד ללא הגנה (unhedged_pnl < 0) - אחרת
    השאלה 'כמה % מההפסד קוזז' לא מוגדרת, ומוצג None ולא 0 או ערך שרירותי.
    """
    hedged = protective_put(shares, stock_entry, strike, premium, contracts)
    unhedged = Position(legs=[Leg("stock", +1, shares, stock_entry, label="מניות בלבד")])

    rows = []
    for pct in pct_moves:
        price = stock_entry * (1.0 + pct)
        unhedged_pnl = unhedged.payoff_at(price)
        hedged_pnl = hedged.payoff_at(price)
        protection_amount = hedged_pnl - unhedged_pnl
        protection_pct = (protection_amount / -unhedged_pnl) if unhedged_pnl < 0 else None
        rows.append(ProtectionRow(
            pct_move=pct, stressed_price=price,
            unhedged_pnl=unhedged_pnl, hedged_pnl=hedged_pnl,
            protection_amount=protection_amount, protection_pct=protection_pct,
        ))
    return rows


def insurance_cost(premium: float, contracts: float) -> float:
    """עלות הביטוח בדולרים: פרמיה × 100 × חוזים. לא כולל עמלות (מתווספות בשכבת ה-UI אם רלוונטי)."""
    return premium * CONTRACT_MULTIPLIER * contracts


def short_put_breakeven(strike: float, premium: float) -> float:
    """
    נקודת האיזון של שורט-פוט: המחיר שמתחתיו העסקה מתחילה להפסיד בפועל
    (אחרי שהפרמיה כבר כוסתה במלואה). strike - premium, לא יותר מזה -
    אבל ממורכז כאן כדי שלא ייכתב שוב בכל מקום.
    """
    return strike - premium


def collar(shares: int, stock_entry: float, put_strike: float, put_premium: float,
           call_strike: float, call_premium: float, contracts: int | None = None) -> Position:
    """מניה + פוט מגן + קול כתוב שמממן אותו."""
    contracts = contracts if contracts is not None else shares / CONTRACT_MULTIPLIER
    return Position(
        name="Collar",
        legs=[
            Leg("stock", +1, shares, stock_entry, label="מניות"),
            Leg("put", +1, contracts, put_premium, put_strike, label="ביטוח"),
            Leg("call", -1, contracts, call_premium, call_strike, label="מימון"),
        ],
    )


def solve_zero_cost_collar(
    shares: int, stock_entry: float, S: float, T: float, sigma: float,
    put_delta: float = 0.25, r: float = 0.045, q: float = 0.0,
    strike_step: float = 5.0, max_search: float = 3.0,
) -> Position:
    """
    סולבר הצווארון. המשתמשת בוחרת רק את הרצפה, המערכת פותרת את התקרה.

    ככל שסטרייק הקול גבוה יותר כך הפרמיה שלו נמוכה יותר, לכן מחפשים את
    הסטרייק הגבוה ביותר שהפרמיה שלו עדיין מכסה את עלות הפוט.
    זו התקרה הכי נדיבה שאפשר לקבל בעלות אפס.
    """
    put_k = strike_from_delta("put", put_delta, S, T, sigma, r, q, round_to=strike_step)
    put_prem = bs_greeks("put", S, put_k, T, sigma, r, q).price

    best_k = best_prem = None
    k = round(S / strike_step) * strike_step
    limit = S * max_search
    while k <= limit:
        prem = bs_greeks("call", S, k, T, sigma, r, q).price
        if prem >= put_prem:
            best_k, best_prem = k, prem   # עדיין מכסה, ננסה גבוה יותר
        else:
            break                          # מכאן והלאה כבר לא יכסה
        k += strike_step

    if best_k is None:
        raise ValueError(
            f"no call strike at or above ${S:,.2f} covers a put costing ${put_prem:,.2f}; "
            f"lower put_delta or shorten T"
        )

    return collar(shares, stock_entry, put_k, put_prem, best_k, best_prem)


def bull_put_spread(contracts: int,
                    short_put: float, short_put_prem: float,
                    long_put: float, long_put_prem: float) -> Position:
    """
    מרווח פוטים בקרדיט: כותבים פוט בסטרייק הגבוה יותר (short_put),
    קונים הגנה בסטרייק נמוך יותר (long_put). סיכון מוגדר מראש:
    רוחב המרווח (short_put - long_put) פחות הקרדיט שהתקבל, כפול 100,
    כפול חוזים - לא יותר מזה ולא פחות, עקב העובדה בסיכון מוגדר.
    """
    if not (long_put < short_put):
        raise ValueError(
            f"long_put must be below short_put, got long_put={long_put}, short_put={short_put}"
        )
    return Position(
        name="Bull Put Spread",
        legs=[
            Leg("put", -1, contracts, short_put_prem, short_put, label="כתיבה"),
            Leg("put", +1, contracts, long_put_prem, long_put, label="הגנה"),
        ],
    )


def iron_condor(contracts: int,
                short_put: float, short_put_prem: float,
                long_put: float, long_put_prem: float,
                short_call: float, short_call_prem: float,
                long_call: float, long_call_prem: float) -> Position:
    """ארבע רגליים. הלונגים חייבים להיות מחוץ לשורטים."""
    if not (long_put < short_put < short_call < long_call):
        raise ValueError(
            "strikes must satisfy long_put < short_put < short_call < long_call, "
            f"got {long_put}, {short_put}, {short_call}, {long_call}"
        )
    return Position(
        name="Iron Condor",
        legs=[
            Leg("put", +1, contracts, long_put_prem, long_put, label="הגנה תחתונה"),
            Leg("call", +1, contracts, long_call_prem, long_call, label="הגנה עליונה"),
            Leg("put", -1, contracts, short_put_prem, short_put, label="כתיבה תחתונה"),
            Leg("call", -1, contracts, short_call_prem, short_call, label="כתיבה עליונה"),
        ],
    )


def entry_order(position: Position) -> list[Leg]:
    """
    סדר ההזנה הכפוי לברוקר: מניה, אחר כך כל הלונגים, ורק בסוף השורטים.
    קניית ההגנה לפני הכתיבה מונעת דרישת בטוחות מנופחת.
    """
    rank = {("stock", 1): 0, ("stock", -1): 0}
    return sorted(
        position.legs,
        key=lambda l: rank.get((l.kind, l.direction), 1 if l.direction == 1 else 2),
    )


# =============================================================================
# 5. מנוע הסיכון
# =============================================================================

@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    contracts: int            # כמה חוזים מותרים בפועל. 0 = חסום.
    risk_usd: float           # ההפסד המקסימלי בכמות המאושרת. חסום -> 0.
    risk_pct: float           # אותו סכום כאחוז מהתיק
    budget_usd: float         # תקציב הסיכון
    reason: str
    unit_risk_usd: float = 0.0  # סיכון לחוזה בודד. תמיד מלא, גם כשחסום.


def size_position(
    template: Position,
    portfolio_usd: float,
    max_risk_pct: float = 0.01,
    open_positions: int = 0,
    max_concurrent: int = 5,
    template_contracts: int = 1,
) -> RiskVerdict:
    """
    שומר חוק ה-1% לעסקאות ספקולטיביות.

    template : פוזיציה בנויה עם template_contracts חוזים
    מחזיר את מספר החוזים המרבי שעומד בתקציב. אם אפילו חוזה אחד חורג,
    התוצאה היא 0 חוזים. אין עיגול כלפי מעלה.
    """
    if portfolio_usd <= 0:
        return RiskVerdict(False, 0, 0.0, 0.0, 0.0, "שווי תיק חייב להיות חיובי")

    budget = portfolio_usd * max_risk_pct
    unit_risk = template.max_loss() / template_contracts

    if open_positions >= max_concurrent:
        return RiskVerdict(
            False, 0, 0.0, 0.0, budget,
            f"כבר פתוחות {open_positions} עסקאות. המקסימום הוא {max_concurrent} "
            f"(חשיפה כוללת {max_concurrent * max_risk_pct:.0%})",
            unit_risk,
        )

    if unit_risk == float("inf"):
        return RiskVerdict(False, 0, 0.0, 0.0, budget,
                           "סיכון בלתי מוגדר. האפליקציה מאשרת רק מבנים מוגדרי סיכון",
                           float("inf"))

    if unit_risk <= 0:
        return RiskVerdict(True, template_contracts, 0.0, 0.0, budget,
                           "אין סיכון בפקיעה בפוזיציה הזו", 0.0)

    allowed = int(budget // unit_risk)

    if allowed == 0:
        return RiskVerdict(
            False, 0, 0.0, 0.0, budget,
            f"חוזה בודד מסכן ${unit_risk:,.0f} מול תקציב של ${budget:,.0f}. "
            f"צמצמי את רוחב הכנף, בחרי נכס זול יותר, או שהתיק קטן מדי לעסקה הזו",
            unit_risk,
        )

    risk = allowed * unit_risk
    return RiskVerdict(True, allowed, risk, risk / portfolio_usd, budget,
                       f"מאושר: {allowed} חוזים, סיכון ${risk:,.0f} "
                       f"({risk / portfolio_usd:.2%} מהתיק)",
                       unit_risk)


def portfolio_floor(hedged: Sequence[tuple[Position, float]],
                    unhedged_value: float = 0.0,
                    stress_drawdown: float = 0.20) -> float:
    """
    הדלי השני: רצפת התיק. זה המדד לביטוח, ולא חוק ה-1%.

    hedged          : רשימת (פוזיציה מגודרת, מחיר מניה נוכחי)
    unhedged_value  : שווי מניות ללא הגנה
    stress_drawdown : תרחיש קיצון למניות החשופות
    """
    return (
        sum(pos.floor_value(price) for pos, price in hedged)
        + unhedged_value * (1 - stress_drawdown)
    )


def pop_iron_condor(short_put_delta: float, short_call_delta: float) -> float:
    """
    הסתברות לרווח מלא באיירון קונדור.
    שני סטרייקים קצרים, לכן זו לא 1 מינוס דלתא בודדת.
    בדלתא 0.15 משני הצדדים התוצאה היא כ-70%, לא 85%.
    """
    return max(0.0, 1.0 - abs(short_put_delta) - abs(short_call_delta))


# =============================================================================
# 6. סטרס טסט ו-CVaR לשורט-פוט - בונה על Leg/Position הקיימים, לא מכפיל נוסחה
# =============================================================================

_DEFAULT_STRESS_MOVES = (0.20, 0.10, 0.0, -0.10, -0.20, -0.30, -0.40, -0.50)


@dataclass(frozen=True)
class StressTestRow:
    pct_move: float                  # לדוגמה -0.30 = ירידה של 30%
    stressed_price: float
    pnl: float
    pct_of_capital: float | None      # None אם לא סופק הון זמין


def stress_test_table(
    S: float,
    strike: float,
    premium: float,
    contracts: int,
    available_capital: float | None = None,
    pct_moves: Sequence[float] = _DEFAULT_STRESS_MOVES,
) -> list[StressTestRow]:
    """
    P/L לפוזיציית שורט-פוט יחיד (cash-secured/naked) על פני תרחישי % קבועים.
    משתמש ב-Position/Leg הקיים - לא נוסחת PnL עצמאית.
    """
    position = Position(legs=[Leg("put", -1, contracts, premium, strike, label="שורט פוט")])
    rows = []
    for pct in pct_moves:
        stressed_price = S * (1.0 + pct)
        pnl = position.payoff_at(stressed_price)
        pct_of_capital = (pnl / available_capital) if available_capital else None
        rows.append(StressTestRow(pct_move=pct, stressed_price=stressed_price, pnl=pnl, pct_of_capital=pct_of_capital))
    return rows


def historical_short_put_pnl_distribution(
    closes: Sequence[float],
    strike: float,
    dte_days: int,
    premium: float,
    contracts: int,
) -> list[float]:
    """
    פילוג P/L אמפירי: לכל יום מסחר היסטורי, מה היה ה-P/L של שורט-פוט הזה
    אילו נפתח אז ופג dte_days ימים קדימה, לפי המחיר שבאמת קרה. אותה שיטת
    'מחיר עתידי' כמו historical_put_otm_probability - לא נוסחה חדשה.
    """
    n = len(closes)
    total = n - dte_days
    if total <= 0:
        return []
    position = Position(legs=[Leg("put", -1, contracts, premium, strike, label="שורט פוט")])
    return [position.payoff_at(closes[i + dte_days]) for i in range(total)]


def expected_shortfall(pnl_distribution: Sequence[float], tail_fraction: float = 0.05) -> float | None:
    """
    CVaR: ממוצע ה-tail_fraction (ברירת מחדל 5%) הגרועים בפילוג. None אם
    הפילוג ריק. פונקציה גנרית - לא תלויה בסוג הפוזיציה שהפיקה את הפילוג.
    """
    if not pnl_distribution:
        return None
    ordered = sorted(pnl_distribution)
    n_tail = max(1, int(len(ordered) * tail_fraction))
    worst = ordered[:n_tail]
    return sum(worst) / len(worst)


# =============================================================================
# 7. סורק רב-מניות - שכבת ריכוז מעל הפונקציות הקיימות, לא נוסחה חדשה
# =============================================================================
#
# דלתא יעד אחידה לכל הטיקרים (למשל 0.20 לכולם), לא סטרייק דולרי קבוע -
# כך ההשוואה בין מניות שונות הוגנת (אותו "עומק" יחסי מהמחיר הנוכחי).
# put_premium כאן תיאורטי (Black-Scholes לפי ה-IV שנמסר) - לא מחיר שוק
# בפועל. שליפת bid/ask אמיתי דורשת קריאת options chain נפרדת לכל טיקר,
# שכבה נוספת ויקרה יותר מעל זה - מוצהר כאן ב-note, לא מוסתר.

@dataclass(frozen=True)
class ScreenerRow:
    """שורה אחת בטבלת הסריקה. כל שדה מגיע מפונקציה קיימת - אין חישוב חדש כאן."""
    ticker: str
    spot: float
    strike: float | None
    dte_days: int
    iv: float | None
    rv_rank: float | None
    rv_percentile: float | None
    iv_rv_spread: float | None
    expected_move_pct: float | None
    put_premium: float | None
    put_delta: float | None
    breakeven: float | None
    prob_otm_model: float | None
    prob_otm_fat_tail: float | None
    prob_otm_historical: float | None
    xem_distance: float | None
    cvar_5pct: float | None
    max_loss: float | None
    note: str = ""


def build_screener_row(
    ticker: str,
    S: float,
    closes: Sequence[float] | None,
    iv: float | None,
    target_put_delta: float,
    dte_days: int,
    r: float = 0.045,
    strike_round_to: float | None = None,
) -> ScreenerRow:
    """
    בונה שורת סריקה אחת. S / closes / iv כבר שאובים מבחוץ (yfinance
    וכו') - אותה הפרדת I/O-מול-חישוב שכבר קיימת ברמת הקובץ הזה.
    """

    def _empty(note: str) -> ScreenerRow:
        return ScreenerRow(
            ticker=ticker, spot=S, strike=None, dte_days=dte_days, iv=iv,
            rv_rank=None, rv_percentile=None, iv_rv_spread=None,
            expected_move_pct=None, put_premium=None, put_delta=None,
            breakeven=None, prob_otm_model=None, prob_otm_fat_tail=None,
            prob_otm_historical=None, xem_distance=None, cvar_5pct=None,
            max_loss=None, note=note,
        )

    if iv is None or S <= 0:
        return _empty("אין IV זמין - טיקר דולג")

    T = dte_days / 365.0
    try:
        strike = strike_from_delta("put", target_put_delta, S, T, iv, r, round_to=strike_round_to)
        g = bs_greeks("put", S, strike, T, iv, r)
    except ValueError as e:
        return _empty(f"לא נמצא סטרייק לדלתא {target_put_delta}: {e}")

    premium = g.price
    rv = realized_vol(closes) if closes else None
    rank = rv_rank(closes) if closes else None
    pct = rv_percentile(closes) if closes else None
    iv_rv_spread = (iv - rv) if rv is not None else None
    breakeven = short_put_breakeven(strike, premium)

    em = expected_move(S, iv, dte_days)
    em_pct = em.move / S
    xem = strike_distance_in_expected_moves(S, strike, em.move)

    prob_hist = fat = cvar = None
    if closes:
        hist = historical_put_otm_probability(closes, strike, dte_days)
        prob_hist = hist.weighted_otm
        log_returns = [log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        fat_result = fat_tail_otm_probability(log_returns, S, strike, dte_days)
        fat = fat_result.otm_probability
        pnl_dist = historical_short_put_pnl_distribution(closes, strike, dte_days, premium, contracts=1)
        cvar = expected_shortfall(pnl_dist, tail_fraction=0.05)

    max_loss = Position(legs=[Leg("put", -1, 1, premium, strike)]).max_loss()
    note = "פרמיה תיאורטית (BS), לא מחיר שוק" if closes else "אין היסטוריית מחירים - חלק מהמדדים לא זמינים"

    return ScreenerRow(
        ticker=ticker, spot=S, strike=strike, dte_days=dte_days, iv=iv,
        rv_rank=rank, rv_percentile=pct, iv_rv_spread=iv_rv_spread,
        expected_move_pct=em_pct, put_premium=premium, put_delta=g.delta,
        breakeven=breakeven, prob_otm_model=g.prob_otm, prob_otm_fat_tail=fat,
        prob_otm_historical=prob_hist, xem_distance=xem, cvar_5pct=cvar,
        max_loss=max_loss, note=note,
    )


# =============================================================================
# 8. ציטוט שוק - bid/ask אמיתי לסטרייק בודד (השליפה עצמה ב-riskshield_tab.py)
# =============================================================================
#
# ציטוט "חי" = bid > 0 וגם ask > 0 וגם ask >= bid. מחוץ לשעות המסחר ב-US
# ה-bid לרוב 0 - במקרה הזה אין Mid, ואין fallback ל-lastPrice: אותו עיקרון
# כמו ה-filter של bid > 0 ב-fetch_atm_iv.

@dataclass(frozen=True)
class QuoteSummary:
    is_live: bool
    mid: float | None
    spread: float | None       # ask - bid, בדולרים למניה
    spread_pct: float | None   # spread / mid (0.05 = 5%)


def quote_summary(bid: float | None, ask: float | None) -> QuoteSummary:
    """Mid ומרווח מ-bid/ask. לא חי -> כל השדות None מלבד is_live=False."""
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return QuoteSummary(False, None, None, None)
    if not (isfinite(b) and isfinite(a)) or b <= 0 or a <= 0 or a < b:
        return QuoteSummary(False, None, None, None)
    mid = (a + b) / 2.0
    spread = a - b
    return QuoteSummary(True, mid, spread, spread / mid)


def nearest_strike(strikes: Sequence[float], K: float) -> float | None:
    """
    הסטרייק בשרשרת הקרוב ביותר ל-K. בשוויון מרחק - הנמוך מביניהם
    (לפוט שנמכר: רחוק יותר מהכסף, כלומר הכיוון השמרני). None אם אין סטרייקים תקינים.
    """
    valid = []
    for s in strikes or ():
        try:
            f = float(s)
        except (TypeError, ValueError):
            continue
        if isfinite(f) and f > 0:
            valid.append(f)
    if not valid:
        return None
    return min(valid, key=lambda s: (abs(s - K), s))


# =============================================================================
# 9. שורות שרשרת לטבלת ההשוואה - שכבת ריכוז, לא נוסחה חדשה
# =============================================================================
#
# לכל סטרייק: IV מחושב מה-Mid שלו עצמו (לא IV אחיד לכל השרשרת), ומה-IV הזה
# דלתא, הסתברות OTM ותזוזה צפויה. כך כל שורה עקבית עם הפרמיה שלה - ה-skew
# נכנס לחישוב במקום להיעלם. סטרייק בלי ציטוט חי, או בלי IV (Mid מתחת לערך
# הפנימי), מדולג - לא מנוחש.

@dataclass(frozen=True)
class ChainStrikeRow:
    strike: float
    premium: float                  # Mid, מעוגל לסנט
    spread_pct: float               # (ask - bid) / mid
    iv: float                       # מה-Mid, שבר (0.35 = 35%)
    delta: float                    # ערך מוחלט
    prob_otm_model: float
    prob_otm_fat_tail: float | None
    breakeven: float
    xem_distance: float | None
    volume: int
    open_interest: int


def _as_count(v) -> int:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if isfinite(f) and f > 0 else 0


def chain_strike_rows(
    chain: Sequence[dict],
    S: float,
    dte_days: int,
    r: float = 0.045,
    delta_band: tuple[float, float] = (0.10, 0.35),
    daily_log_returns: Sequence[float] | None = None,
    max_rows: int = 10,
) -> list[ChainStrikeRow]:
    """
    chain: רשימת dict עם strike, bid, ask, volume, openInterest (כמו yfinance puts).
    יותר מ-max_rows בטווח -> הקרובים לאמצע טווח הדלתא. התוצאה ממוינת לפי סטרייק.
    """
    if S <= 0 or dte_days < 1:
        return []
    lo, hi = delta_band
    band_mid = (lo + hi) / 2.0
    T = dte_days / 365.0
    rows: list[ChainStrikeRow] = []
    for row in chain or ():
        try:
            K = float(row.get("strike"))
        except (TypeError, ValueError):
            continue
        if not (isfinite(K) and K > 0):
            continue
        q = quote_summary(row.get("bid"), row.get("ask"))
        if not q.is_live:
            continue
        premium = round(max(q.mid, 0.01), 2)
        try:
            iv = implied_vol("put", premium, S, K, T, r)
        except ValueError:
            continue
        if not (0.0 < iv < 6.0):
            continue
        g = bs_greeks("put", S, K, T, iv, r)
        delta = abs(g.delta)
        if not (lo <= delta <= hi):
            continue
        em = expected_move(S=S, sigma=iv, T_days=dte_days)
        fat = None
        if daily_log_returns:
            fat = fat_tail_otm_probability(daily_log_returns, S=S, K=K, dte_days=dte_days).otm_probability
        rows.append(ChainStrikeRow(
            strike=K, premium=premium, spread_pct=q.spread_pct, iv=iv, delta=delta,
            prob_otm_model=g.prob_otm, prob_otm_fat_tail=fat,
            breakeven=short_put_breakeven(strike=K, premium=premium),
            xem_distance=strike_distance_in_expected_moves(S=S, K=K, move=em.move),
            volume=_as_count(row.get("volume")), open_interest=_as_count(row.get("openInterest")),
        ))
    if len(rows) > max_rows:
        rows = sorted(rows, key=lambda x: abs(x.delta - band_mid))[:max_rows]
    return sorted(rows, key=lambda x: x.strike)


# =============================================================================
# 10. סיכום טבלת ההשוואה - עובדות והתראות, לא המלצה
# =============================================================================
#
# הקלט: שורה לכל סטרייק (dict), באחוזים כמו בטבלה (87.6 ולא 0.876).
# מפתחות: strike, premium, breakeven, prob_model, fat_tail (או None),
# xem (או None), iv, spread_pct (או None), low_liquidity (bool), expiry (או None).

FAT_TAIL_GAP_PTS = 1.0      # Fat-tail נמוך מהמודל בנקודה ומעלה -> התראה
WIDE_SPREAD_PCT = 10.0      # מרווח Bid/Ask מ-10% ומעלה -> התראה


@dataclass(frozen=True)
class CompareStep:
    from_strike: float
    to_strike: float
    premium_add: float          # כמה פרמיה נוספת (למניה)
    prob_drop_pts: float        # כמה נקודות הסתברות OTM יורדות (חיובי = ירידה)
    premium_per_pt: float | None  # פרמיה נוספת לכל נקודת הסתברות שמוותרים עליה


@dataclass(frozen=True)
class CompareSummary:
    n: int
    strikes: tuple[float, ...]
    premium_range: tuple[float, float]          # (סטרייק נמוך, סטרייק גבוה)
    breakeven_range: tuple[float, float]
    drop_to_breakeven_pct: tuple[float, float] | None   # כמה המניה צריכה לרדת עד האיזון
    prob_model_range: tuple[float, float]
    fat_below_model: tuple[float, ...]          # סטרייקים שבהם ההיסטוריה מסוכנת מהמודל
    outside_em: tuple[float, ...]               # xEM >= 1 - מחוץ לתזוזה הצפויה
    inside_em: tuple[float, ...]
    iv_range: tuple[float, float]               # (סטרייק נמוך, סטרייק גבוה)
    skew: str                                   # "down" (IV יורד כשהסטרייק עולה) / "up" / "flat"
    low_liquidity: tuple[float, ...]
    wide_spread: tuple[float, ...]
    spread_range: tuple[float, float] | None
    mixed_expiries: bool
    steps: tuple[CompareStep, ...]


def compare_summary(rows: Sequence[dict], S: float | None = None) -> CompareSummary | None:
    """None אם יש פחות מ-2 סטרייקים (בלי השוואה אין trade-off)."""
    rs = sorted((r for r in rows or () if r.get("strike") is not None), key=lambda r: float(r["strike"]))
    if len(rs) < 2:
        return None
    lo, hi = rs[0], rs[-1]
    strikes = tuple(float(r["strike"]) for r in rs)

    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if isfinite(f) else None

    drop = None
    if S and S > 0:
        drop = ((S - float(lo["breakeven"])) / S * 100.0, (S - float(hi["breakeven"])) / S * 100.0)

    fat_below = tuple(
        float(r["strike"]) for r in rs
        if _num(r.get("fat_tail")) is not None
        and _num(r.get("fat_tail")) <= float(r["prob_model"]) - FAT_TAIL_GAP_PTS
    )
    xems = [(float(r["strike"]), _num(r.get("xem"))) for r in rs]
    outside = tuple(k for k, x in xems if x is not None and x >= 1.0)
    inside = tuple(k for k, x in xems if x is not None and x < 1.0)

    iv_lo, iv_hi = float(lo["iv"]), float(hi["iv"])
    skew = "flat" if abs(iv_lo - iv_hi) < 0.5 else ("down" if iv_lo > iv_hi else "up")

    spreads = [(float(r["strike"]), _num(r.get("spread_pct"))) for r in rs]
    spread_vals = [s for _, s in spreads if s is not None]

    expiries = {r.get("expiry") for r in rs if r.get("expiry")}

    steps = []
    for a, b in zip(rs, rs[1:]):
        add = float(b["premium"]) - float(a["premium"])
        drop_pts = float(a["prob_model"]) - float(b["prob_model"])
        steps.append(CompareStep(
            from_strike=float(a["strike"]), to_strike=float(b["strike"]),
            premium_add=add, prob_drop_pts=drop_pts,
            premium_per_pt=(add / drop_pts) if drop_pts > 0 else None,
        ))

    return CompareSummary(
        n=len(rs), strikes=strikes,
        premium_range=(float(lo["premium"]), float(hi["premium"])),
        breakeven_range=(float(lo["breakeven"]), float(hi["breakeven"])),
        drop_to_breakeven_pct=drop,
        prob_model_range=(float(lo["prob_model"]), float(hi["prob_model"])),
        fat_below_model=fat_below, outside_em=outside, inside_em=inside,
        iv_range=(iv_lo, iv_hi), skew=skew,
        low_liquidity=tuple(float(r["strike"]) for r in rs if r.get("low_liquidity")),
        wide_spread=tuple(k for k, s in spreads if s is not None and s >= WIDE_SPREAD_PCT),
        spread_range=(min(spread_vals), max(spread_vals)) if spread_vals else None,
        mixed_expiries=len(expiries) > 1,
        steps=tuple(steps),
    )
