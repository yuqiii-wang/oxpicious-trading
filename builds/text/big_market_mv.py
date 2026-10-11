"""builds.text.big_market_mv — the is_big_fluctuation flag pass.

Maintains the ``is_big_fluctuation`` column on ``text.news`` and
``text.llm_qa``: true when the row's mapped industry had a BIG market
move on the row's date. Both tables take the SAME per-industry event
series, computed from ``stats.industry_basic_stats`` (``pool_size =
'all'`` mean_close composite — the identical series the
``today_industry_change`` pass reads):

  trigger  — an (industry_id, date) qualifies when BOTH hold that date:

               * |single-day change| >= ``BIG_MV_CHANGE_PCT`` (3%) —
                 mean_close(d) / mean_close(prev stored date) - 1, in
                 percent, either direction;
               * Wilder RSI(3) over the mean_close series is pinned at
                 a rail: >= 100 - ``BIG_MV_RSI3_RAIL`` (97) or <=
                 ``BIG_MV_RSI3_RAIL`` (3) — the formula mirrors
                 ``analysis.mov_ave_spread.steps.rsi`` window 3 (EWM
                 alpha=1/3, adjust=False, min_periods=3,
                 ignore_na=True; RSI=100/0 pure-trend overrides). A 3%
                 day with RSI(3) mid-range (one-off spike inside a flat
                 run) does NOT qualify; both rails together keep the
                 trigger direction-symmetric.

  cooldown — the FIRST qualifying date per industry survives; a
             qualifying date whose [cutoff, date] window (date + the
             last 5 trading days, via ``recent_trading_day_cutoff(
             cooldown + 1, d)``) contains a SURVIVING date stays false —
             the same ask-day-inclusive 5-trading-day window the
             ai_daily movers cooldown applies
             (``MOVERS_COOLDOWN_BD``). The cooldown anchors on
             SURVIVING TRIGGER DATES, not on flagged rows: late-arriving
             articles of a suppressed day stay false and the surviving
             set is deterministic however often the pass re-runs.

  apply    — full recompute, idempotent: ONE UPDATE per table sets
             ``is_big_fluctuation`` to the (industry_id, date) membership
             test against the surviving pairs (false otherwise), so a
             re-run converges and late ``llm_agents`` stores are healed
             by the next ``builds.text`` run. ``text.llm_qa`` rows match
             on the Shanghai calendar date of ``qa_date`` (the same
             cast the movers cooldown uses).

This module runs under PLAIN pandas (builds.text does not activate
cudf.pandas; the frame is ~10^2 industries x ~10^3 dates).
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Tuple

import pandas as pd

from _common._holidays_and_weekdays import recent_trading_day_cutoff

logger = logging.getLogger(__name__)

NEWS_TABLE = "text.news"
QA_TABLE = "text.llm_qa"

# Trigger thresholds. Change is in PERCENT (matching
# today_industry_change's stored scale); rsi3 is the 0..100 Wilder RSI(3),
# so the rail is symmetric around 50 (>= 97 / <= 3 at the default).
BIG_MV_CHANGE_PCT = 3.0
BIG_MV_RSI3_RAIL = 3.0
BIG_MV_RSI3_WINDOW = 3
# 5-trading-day cooldown per industry, the same ask-day-inclusive window
# the ai_daily movers cooldown uses (MOVERS_COOLDOWN_BD).
BIG_MV_COOLDOWN_BD = 5

# One surviving (industry_id, date): the industry's composite close series.
# pool_size filters by its stats.label_dim SMALLINT code (3 = 'all').
INDUSTRY_CLOSE_SQL = """
    SELECT industry_id, date, mean_close::float8 AS mean_close
    FROM stats.industry_basic_stats
    WHERE pool_size = 3 AND mean_close IS NOT NULL
    ORDER BY industry_id, date
"""

# Membership test against the surviving pairs as one UPDATE: true where
# (industry_id, date) is a surviving pair, false otherwise (industry-tagged
# rows only — untagged rows keep the column default). The unnest arrays are
# empty when nothing survived, which correctly clears every flag.
NEWS_FLAG_SQL = f"""
    UPDATE {NEWS_TABLE} AS n
    SET is_big_fluctuation = EXISTS (
        SELECT 1 FROM unnest($1::text[], $2::date[]) AS t(industry_id, d)
        WHERE t.industry_id = n.industry_id AND t.d = n.date)
    WHERE n.industry_id IS NOT NULL
"""

QA_FLAG_SQL = f"""
    UPDATE {QA_TABLE} AS q
    SET is_big_fluctuation = EXISTS (
        SELECT 1 FROM unnest($1::text[], $2::date[]) AS t(industry_id, d)
        WHERE t.industry_id = q.industry_id
          AND t.d = (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date)
    WHERE q.industry_id IS NOT NULL
"""


# ----------------------------------------------------------------------------
# Pure compute
# ----------------------------------------------------------------------------
def _grouped_ewm(s: pd.Series, keys: pd.Series) -> pd.Series:
    """Wilder EWM mean per industry, realigned to s.index.

    Mirrors ``analysis.mov_ave_spread.steps.rsi._grouped_ewm_pandas``
    (the pandas path only — builds.text does not activate cudf.pandas):
    ``groupby(keys).ewm(...)`` returns a MultiIndex Series whose group-key
    levels are stripped before realigning to the original row order.
    """
    res = (
        s.groupby(keys, sort=False)
        .ewm(alpha=1.0 / BIG_MV_RSI3_WINDOW, adjust=False,
             min_periods=BIG_MV_RSI3_WINDOW, ignore_na=True)
        .mean()
    )
    return res.reset_index(level=0, drop=True).reindex(s.index)


def _wilder_rsi(close: pd.Series, keys: pd.Series) -> pd.Series:
    """Wilder RSI(3) per industry over the close series (0..100).

    Same formula and edge handling as
    ``analysis.mov_ave_spread.steps.rsi.compute_rsi``: gain/loss keep NaN
    where the delta is NaN (null prices skip the smoothing, they do not
    count as flat days), and RSI=100 / 0 override the 0/0 division on
    pure up/down trends. NaN head rows (min_periods) stay NaN and never
    fire the trigger (NaN comparisons are False).
    """
    delta = close - close.groupby(keys, sort=False).shift(1)
    gain = delta.where(delta > 0, 0.0).where(delta.notna())
    loss = (-delta).where(delta < 0, 0.0).where(delta.notna())
    avg_gain = _grouped_ewm(gain, keys)
    avg_loss = _grouped_ewm(loss, keys)
    rsi = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    return rsi


def compute_big_mv_days(
    stats_rows: List[Any],
) -> List[Tuple[str, datetime.date, float, float]]:
    """Surviving big-move days as (industry_id, date, change_pct, rsi3).

    *stats_rows* are asyncpg Records of INDUSTRY_CLOSE_SQL (industry_id,
    date, mean_close), ordered by (industry_id, date). Vectorized
    whole-frame pass for change/RSI; the cooldown walk is a small
    sequential pass over the qualifying days only (per-industry state,
    like the movers cooldown loop).
    """
    if not stats_rows:
        return []
    df = pd.DataFrame({
        "industry_id": [r["industry_id"] for r in stats_rows],
        "date": [r["date"] for r in stats_rows],
        "close": [r["mean_close"] for r in stats_rows],
    }).reset_index(drop=True)

    keys = df["industry_id"]
    prev = df["close"].groupby(keys, sort=False).shift(1)
    change = (df["close"] / prev - 1.0) * 100.0
    rsi = _wilder_rsi(df["close"], keys)
    fires = (
        (change.abs() >= BIG_MV_CHANGE_PCT)
        & ((rsi >= 100.0 - BIG_MV_RSI3_RAIL) | (rsi <= BIG_MV_RSI3_RAIL))
    )
    hits = sorted(zip(df.loc[fires, "industry_id"], df.loc[fires, "date"],
                      change[fires], rsi[fires]))

    kept: List[Tuple[str, datetime.date, float, float]] = []
    last_kept: Dict[str, datetime.date] = {}
    for industry_id, d, chg, r3 in hits:
        cutoff = recent_trading_day_cutoff(BIG_MV_COOLDOWN_BD + 1, d)
        prev_kept = last_kept.get(industry_id)
        if prev_kept is not None and prev_kept >= cutoff:
            continue  # absorbed by the 5d cooldown
        last_kept[industry_id] = d
        kept.append((industry_id, d, float(chg), float(r3)))
    return kept


# ----------------------------------------------------------------------------
# DB apply
# ----------------------------------------------------------------------------
async def apply_big_market_mv(conn) -> Dict[str, int]:
    """Recompute and write is_big_fluctuation on text.news + text.llm_qa.

    Full recompute (idempotent — see the module docstring). Returns the
    surviving-day count and the per-table UPDATE row counts (rows
    TOUCHED, i.e. every industry-tagged row, not just set-true ones).
    """
    kept = compute_big_mv_days(await conn.fetch(INDUSTRY_CLOSE_SQL))
    industries = [k[0] for k in kept]
    days = [k[1] for k in kept]
    news_status = await conn.execute(NEWS_FLAG_SQL, industries, days)
    qa_status = await conn.execute(QA_FLAG_SQL, industries, days)
    logger.info("    [DB] is_big_fluctuation: %d surviving day(s) across %d "
                "industry(ies) (|chg| >= %g%% AND rsi3 <= %g or >= %g, "
                "%dd cooldown)", len(kept), len(set(industries)),
                BIG_MV_CHANGE_PCT, BIG_MV_RSI3_RAIL,
                100.0 - BIG_MV_RSI3_RAIL, BIG_MV_COOLDOWN_BD)
    return {
        "surviving_days": len(kept),
        "industries": len(set(industries)),
        "news_touched": int(news_status.split()[-1]),
        "qa_touched": int(qa_status.split()[-1]),
    }
