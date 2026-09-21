"""Per-code self-adaptive market-regime WEIGHTS step
(analyze.analysis_forecasts.regime_weights).

Populates analysis_forecasts.regime_weights — the walk-forward
w(code, family, regime; stat_month) fitted ONLY on information realized
by the snapshot month (the Phase-A study's algorithm, §3 of
docs/market_regimes_study.md):

  fit window   the snapshot's months (M-2-K, M-2] — every month m in
               the window has its own trailing-window forward stats
               fully realized by month-end M (a month's mixed metrics
               reach m + 1 month + 20 trading days), so NO look-ahead.
               (The study's realized definition re-detected next-month
               days under frozen bars; this production fit uses each
               fit-month's OWN realized mixed metric — the same
               information, exactly computable from the emitted tables,
               documented as the display-tier deviation.)
  evidence     per (family, code, regime): the fit window's mixed rows
               (period='mixed', non-flat sides), sign-aligned
               dir_ave = ±ave_change (top/upper −, bottom/lower +)
               n-weighted by occurrence_count.
  weights      raw = Σ(occ·dir)/Σocc;  shrink = N/(N+10);
               what = GREATEST(raw,0)·shrink normalized per
               (family, code) (all-zero → uniform 1/4);
               w = 0.7·what + 0.075.

EVIDENCE / DISPLAY tier (the study's §4 verdict): score = confidence ×
w was tested head-to-head against the equal-confidence ordering and did
NOT win out-of-sample — signal_order stays on confidence; the weights
feed the forecasts UI (which regimes pay for each code) and future
research.

Incremental: a stat_month's weights never change once written (its fit
window is frozen), so only months MISSING from the table are computed;
--force recomputes all of the sec_type's months.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from analyze.analysis_forecasts.config import (
    REGIME_WEIGHTS_TABLE,
    WEIGHT_FAMILIES,
    WEIGHT_K_MONTHS,
    WEIGHT_LAMBDA,
    WEIGHT_SHRINK_N,
)

import logging
logger = logging.getLogger(__name__)


def _month_end_shift(m: date, k: int) -> date:
    """The month-END k months before month-end ``m`` (repeat "first of
    month minus one day" — lands on the prior month's last day)."""
    for _ in range(k):
        m = m.replace(day=1) - timedelta(days=1)
    return m

# (family, motivation table, has_flat_side) — the evidence SELECTs'
# family-specific shapes. Every family table carries regime_state +
# side; px_vol_state / margin_ratio_state additionally have 'flat'
# sides (no directional claim — excluded from the evidence).
_FAMILY_SOURCES: tuple[tuple[str, str, bool], ...] = (
    ("mov_rsi", "analysis_forecasts.mov_rsi", False),
    ("mov_std", "analysis_forecasts.mov_std", False),
    ("px_vol_state", "analysis_forecasts.px_vol_state", True),
    ("margin_ratio_state", "analysis_forecasts.margin_ratio_state", True),
    ("mov_pairs", "analysis_forecasts.mov_pairs", False),
    ("mov_pairs_ema", "analysis_forecasts.mov_pairs_ema", False),
    ("high_low_streaks", "analysis_forecasts.high_low_streaks", False),
    ("pe_state", "analysis_forecasts.pe_state", False),
    ("dividend_state", "analysis_forecasts.dividend_state", False),
)
assert {f for f, _, _ in _FAMILY_SOURCES} == set(WEIGHT_FAMILIES)

_SIGN = ("CASE WHEN m.side IN ('top', 'upper') THEN -1.0 "
         "ELSE 1.0 END")

# One INSERT ... SELECT per (sec_type, stat_month): union the families'
# mixed-row evidence over the fit window, aggregate per
# (family, code, regime), shrink + normalize + blend, and insert.
_WEIGHTS_INSERT_SQL = """
WITH evidence AS (
    {family_selects}
), per_group AS (
    SELECT family, code, regime_state,
           SUM(occ * dir_ave) / NULLIF(SUM(occ), 0) AS raw,
           SUM(occ) AS n
    FROM evidence
    GROUP BY family, code, regime_state
), shrunk AS (
    SELECT *,
           GREATEST(raw, 0.0) * n / (n + {shrink_n}) AS pos
    FROM per_group
), norm AS (
    SELECT *,
           SUM(pos) OVER (PARTITION BY family, code) AS tot
    FROM shrunk
)
INSERT INTO analysis_forecasts.regime_weights
    (stat_month, sec_type, code, family, regime,
     weight, evidence, k_months, shrink_n, lambda)
SELECT
    $1::date, $2::text, code, family, regime_state,
    ROUND(({lam} * CASE WHEN tot > 0 THEN pos / tot
                        ELSE 0.25 END
           + {uniform})::numeric, 6),
    jsonb_build_object('raw', ROUND(raw::numeric, 6),
                       'n', n,
                       'pos', ROUND(pos::numeric, 6),
                       'tot', ROUND(tot::numeric, 6)),
    {k}, {shrink_n}, {lam}
FROM norm
"""

_MISSING_MONTHS_SQL = """
    SELECT DISTINCT i.stat_month
    FROM analysis_forecasts.forecast_identities i
    WHERE i.sec_type = $1::text
      AND i.bucket = ANY($2::text[])
      AND NOT EXISTS (
          SELECT 1 FROM analysis_forecasts.regime_weights w
          WHERE w.sec_type = i.sec_type
            AND w.stat_month = i.stat_month)
    ORDER BY i.stat_month
"""


def _family_select(family: str, table: str, has_flat: bool) -> str:
    flat_filter = "  AND m.side <> 'flat'" if has_flat else ""
    return f"""
    SELECT '{family}'::text AS family,
           m.code,
           m.regime_state,
           ({_SIGN}) * r.ave_change AS dir_ave,
           r.occurrence_count AS occ
    FROM {table} m
    JOIN analysis_forecasts.forecast_identities i
         ON i.forecast_id = m.forecast_id
    JOIN analysis_forecasts.forecast_results r
         ON r.forecast_id = m.forecast_id AND r.period = 'mixed'
        -- the delay-0 (fresh-signal) blended row — one per forecast_id
         AND r.delay = 0
    WHERE i.sec_type = $2::text
      AND i.stat_month > $3::date AND i.stat_month <= $4::date
      AND m.regime_state IS NOT NULL{flat_filter}
    """


def _build_insert_sql() -> str:
    selects = "\n    UNION ALL\n".join(
        _family_select(f, t, flat) for f, t, flat in _FAMILY_SOURCES)
    return _WEIGHTS_INSERT_SQL.format(
        family_selects=selects,
        shrink_n=WEIGHT_SHRINK_N,
        lam=WEIGHT_LAMBDA,
        uniform=round((1.0 - WEIGHT_LAMBDA) / len(
            ("calm", "hot", "panic", "quiet")), 6),
        k=WEIGHT_K_MONTHS,
    )


async def run_regime_weights(conn, sec_type: str, *, force: bool = False,
                             ) -> int:
    """Compute the sec_type's MISSING regime-weight months (all of them
    under --force). Returns rows written."""
    t0 = time.time()
    logger.info(f"  [{sec_type}] Regime weights "
          f"(K={WEIGHT_K_MONTHS}, shrink={WEIGHT_SHRINK_N:g}, "
          f"λ={WEIGHT_LAMBDA:g})...")
    if force:
        await conn.execute(
            f"DELETE FROM {REGIME_WEIGHTS_TABLE} WHERE sec_type = $1",
            sec_type,
        )

    months = [r["stat_month"] for r in await conn.fetch(
        _MISSING_MONTHS_SQL, sec_type, list(WEIGHT_FAMILIES),
    )]
    if not months:
        logger.info(f"  [{sec_type}]   up to date; nothing to compute.")
        return 0
    logger.info(f"  [{sec_type}]   {len(months)} months to compute "
          f"({months[0]} .. {months[-1]})")

    sql = _build_insert_sql()
    n_total = 0
    for m in months:
        # fit window: the K emitted months ending 2 before M — every
        # month's own forward stats are fully realized by month-end M.
        hi = _month_end_shift(m, 2)                       # inclusive
        lo = _month_end_shift(m, WEIGHT_K_MONTHS + 2)     # exclusive
        status = await conn.execute(sql, m, sec_type, lo, hi)
        n = int(status.rsplit(" ", 1)[-1]) if status else 0
        n_total += n
        logger.info(f"    [{m}] regime_weights: {n:,} rows")
    logger.info(f"  [{sec_type}]   wrote {n_total:,} weight rows "
          f"({time.time() - t0:.1f}s)")
    return n_total
