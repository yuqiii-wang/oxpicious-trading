"""Internal hypes_and_drains step for analyze.industry_sentiments.

Pre-computes the top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by their
hype (industry_return - benchmark_return) relative to a BROAD-MARKET benchmark
over a trailing window, for every (date, benchmark_code, period, weighting).

Populates analysis.industry_hypes_and_drains with 10 rows per
(date, benchmark_code, period, weighting): rank 1..5 HYPE + rank 1..5 DRAIN.

BENCHMARKS
  Uses ALL broad-market benchmarks that appear in analysis.industry_attributions
  (i.e. all benchmark_codes with is_broad_market=TRUE in stats.sec_index_tags).
  This is the SAME set offered by the Benchmark Attribution dropdown — the UI
  reuses the same Autocomplete.

METHODOLOGY (hype = industry_return - benchmark_return)
  The industry return is the SHARED PORTFOLIO's return (the benchmark
  members that belong to the industry), recovered by inverting the daily
  return-decomposition identity — NOT by inverting the materialized
  rolling_{N}days_price column. Inverting the rolling column amplifies
  its construction artifacts (clamped / NULL->0 daily returns, exp/ln
  compounding) by (1-swf)/swf, which for narrow industries (swf < 1%)
  yields +-hundreds-of-percent noise that flooded both ranking sides
  (fixed 2026-09-13; the daily identity inverts cleanly because the
  non-industry daily price column was built FROM it).

  For each (date, industry, benchmark, period N):
    bench_return_1d       = close[t] / close[t-1] - 1
    non_industry_return_1d = benchmark_non_this_industry_price[t]
                             / close[t-1] - 1
                             (the build materializes the level as
                             price[t] = close[t-1] * (1 + r_t) — dividing
                             by the PREVIOUS BENCHMARK CLOSE recovers r_t
                             exactly; dividing by the industry price's own
                             previous level instead compounds a cross-term
                             that random-walks to ~±20% error over the
                             window and flips industries between sides)
    swf                    = benchmark_shared_weight / 100.0
    shared_return_1d       = (bench_return_1d
                              - (1 - swf) * non_industry_return_1d) / swf
    industry_return_Nd     = 100 * exp(SUM ln(1 + shared_return_1d))
                             over the trailing N trading-day rows - 100
                             (window must be FULL — COUNT = N; daily
                             returns outside (-0.5, 0.5] contribute 0,
                             the same convention as the attributions
                             build's rolling columns)
    benchmark_return_Nd    = benchmark.close[t] / benchmark.close[t-N] - 1
    hype                   = industry_return_Nd - benchmark_return_Nd

  Positive hype = HYPE (industry's shared stocks outperformed the benchmark);
  negative = DRAIN (underperformed).

  Industries with a NULL shared_return_Nd (swf = 0, no overlap with the
  benchmark, or insufficient history for the full N-row window) are
  excluded from ranking.

WEIGHTING VARIANTS
  Two weighting variants are materialized, one per attribution_type:
    'equal'       (attribution_type='equal'):       metric_value = hype
    'amt'         (attribution_type='trading_amt'): metric_value = hype * shared_trading_amt

  The daily decomposition inputs (shared weight / non-industry price /
  non-industry trading amount) are IDENTICAL on both attribution_types'
  rows — the attributions build writes the same daily columns per type —
  so ONE input frame (attribution_type='trading_amt', the canonical set
  COUNT_SOURCE_SQL has always keyed on) feeds BOTH weightings; only
  metric_value differs. Emitting a weighting for a (benchmark, date,
  industry) present in either type's rows therefore matches the former
  per-type INSERTs (both types are always written over the same keys by
  run_attributions).

PERIODS: {5, 20, 60, 120, 255, 500}. 120d is the UI default. The 120d
column on industry_attributions is added by 08_industry_hypes_and_drains.sql
and populated by the attributions step (ROLLING_WINDOWS includes 120).

PIPELINE (pandas vectorized — migrated 2026-09-25 from the former
per-(benchmark, period, weighting) server-side INSERT...SELECT, whose
CASE WHEN guards + window arithmetic now live here):
  1. Guard: bail out if industry_attributions has no broad-market rows
     with daily non-industry price data.
  2. Fetch three RAW frames (no SQL-side computation):
       bench_daily  — (benchmark, date) close + trading_amount;
       attr_daily   — (benchmark, date, industry) daily decomposition
                      columns;
       label pairs  — industry_id -> industry_label.
  3. Vectorized compute (cudf.pandas-accelerated under the __main__
     bootstrap): LAG returns via groupby shift, the daily identity
     inversion, per-period exp/ln rolling compounding via
     grouped_rolling_agg (FULL-window gate = rolling count == N),
     hype / metric_value per weighting, and the HYPE/DRAIN top-5 via
     stable sort + groupby cumcount.
  4. Truncate + ONE chunked CSV COPY into industry_hypes_and_drains
     (rank on UNROUNDED metric_value, store ROUNDed to 6 — the former
     SQL ranked pre-ROUND and rounded in the final SELECT, identical).
  5. Upsert analysis.analysis_identity + sanity summary.
  6. Seasonal (monthly) aggregation — same compute from the stored
     (rounded) per-date rows: per (month, benchmark, period, weighting,
     side, industry) peak = MAX (HYPE) / MIN (DRAIN), rank top-5.

Tie note: the former ROW_NUMBER() ORDER BY metric_value DESC/ASC had no
deterministic tiebreak (Postgres leaves equal-key order unspecified); the
stable-sort + cumcount equivalent keeps the input row order for exact
ties (measure-zero at the rank-5 boundary; metrics stored at 6dp).

This module is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the attributions step, reusing the same DB
connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import gc
import time

import numpy as np
import pandas as pd

from _common.build_commons import (
    rec_cols,
    truncate_table_async,
)
from _common.db_commons import (
    copy_frame_chunked_async,
)
from _common.df_utils import (
    epoch_col_to_dt64,
    grouped_rolling_agg,
    host_array,
)
from analyze._common import upsert_analysis_identity

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_hypes_and_drains"
SEASONAL_TABLE = "analysis.industry_hypes_seasonal"
ANALYSIS_NAME = "industry_hypes_and_drains"
ANALYSIS_DESCRIPTION = (
    "Pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by "
    "hype (industry_return - benchmark_return) relative to a BROAD-MARKET "
    "benchmark over a trailing window. One row per (date, benchmark_code, "
    "period_days, weighting, rank_side, rank). Two weighting variants: "
    "'equal' (metric_value = hype, attribution_type='equal') and 'amt' "
    "(metric_value = hype * shared_trading_amt, attribution_type="
    "'trading_amt'). hype = industry_return_Nd - benchmark_return_Nd "
    "where industry_return_Nd is the shared portfolio's trailing-N-day "
    "return, recovered by inverting the DAILY decomposition identity "
    "(shared_return_1d = (bench_1d - (1-swf)*non_ind_1d) / swf, swf = "
    "benchmark_shared_weight / 100) and compounding over the full N-row "
    "window — never by inverting the materialized rolling column, whose "
    "construction artifacts the 1/swf inversion would amplify into noise. "
    "period_days in {5,20,60,120,255,500} (120 default). Built by "
    "analyze.industry_sentiments.hypes_and_drains (internal step, "
    "truncate-then-recompute, pandas-vectorized). Depends on "
    "analysis.industry_attributions (incl. the daily "
    "benchmark_non_this_industry_price column) being populated first."
)

# Trailing windows (trading days). Must match the rolling_{N}days_price
# columns materialized in analysis.industry_attributions (see
# attributions.ROLLING_WINDOWS). 120d is the UI default.
PERIODS: tuple[int, ...] = (5, 20, 60, 120, 255, 500)

# Output column order (mirrors the former INSERT column list).
HD_COLUMNS: list[str] = [
    "date", "benchmark_code", "period_days", "weighting", "rank_side",
    "rank", "industry_id", "industry_label", "metric_value",
    "shared_trading_amt", "benchmark_return_nd", "industry_return_nd",
    "benchmark_shared_weight",
]
HD_FLOAT_COLUMNS = [
    "metric_value", "shared_trading_amt", "benchmark_return_nd",
    "industry_return_nd", "benchmark_shared_weight",
]
SEASONAL_COLUMNS: list[str] = [
    "season_qkey", "season_year", "season_month", "season_start",
    "season_end", "benchmark_code", "period_days", "weighting",
    "rank_side", "rank", "industry_id", "industry_label",
    "peak_metric_value",
]
SEASONAL_FLOAT_COLUMNS = ["peak_metric_value"]

# The rolling-window partition of the daily identity inversion: one
# series per (benchmark, industry) — the former per-benchmark INSERT's
# PARTITION BY industry_id, scoped by its benchmark_code parameter.
_GROUP_KEYS = ["benchmark_code", "industry_id"]


# ---------------------------------------------------------------------------
#  Raw-input SQL (fetch only — every computation lives in pandas below)
# ---------------------------------------------------------------------------

# Fetch all broad-market benchmark codes that have industry_attributions data.
# Filter on attribution_type='trading_amt' to avoid duplicate benchmarks (each
# benchmark now appears twice — once per attribution_type).
BROAD_MARKET_BENCHMARKS_SQL = """
    SELECT DISTINCT ia.benchmark_code
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
    ORDER BY ia.benchmark_code
"""

# Guard: bail out early if the upstream table is empty / missing OR the
# DAILY non-this-industry price column (the raw material for the daily
# identity inversion) has not been populated.
# Filter on attribution_type='trading_amt' to avoid duplicate counting —
# both variants share the same daily columns.
COUNT_SOURCE_SQL = """
    SELECT COUNT(*) AS n
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
      AND ia.benchmark_non_this_industry_price IS NOT NULL
"""

# Per-(benchmark, date) benchmark legs: the LAG returns (1d + per-period Nd)
# are computed in pandas (_compute_bench_frame) — the former CTE's
# LAG(...) OVER / NULLIF / CASE WHEN arithmetic, verbatim semantics:
#   bench_return_1d    = close / NULLIF(lag1, 0) - 1
#   benchmark_return_N = close / NULLIF(lagN, 0) - 1   (NULL when lagN NULL/0)
# Rows keep close IS NOT NULL (the former CTE's WHERE) so the row space —
# and thus every LAG window position — is identical.
BENCH_DAILY_SQL = """
    SELECT code AS benchmark_code,
           extract(epoch from date)::float8 AS date,
           close::float8                    AS close,
           trading_amount::float8           AS trading_amount
    FROM stats.index_basic_stats
    WHERE code = ANY($1::text[])
      AND close IS NOT NULL
    ORDER BY code, date
"""

# (benchmark, date, industry) daily decomposition legs. attribution_type
# ='trading_amt' only — BOTH weightings read these identical daily columns
# (see the module docstring's WEIGHTING VARIANTS); the 'equal' rows would
# duplicate the same values. The broad-market scope is an IN-subquery, NOT
# a JOIN: sec_index_tags is keyed (code, sector_id, industry_id) and a
# benchmark can carry SEVERAL broad-market tag rows (e.g. 000001 =
# BROAD_SSE + benchmark_broadmarket) — a JOIN would fan every attribution
# row out per tag row (the former per-benchmark INSERT never joined this
# table; only its DISTINCT benchmark-list / >0-count guard queries did,
# whose semantics absorb the fan-out). The shared_return_1d inversion +
# per-period compounding are computed in pandas (_compute_shared_daily /
# _compute_shared_nd).
ATTR_DAILY_SQL = """
    SELECT ia.benchmark_code,
           extract(epoch from ia.date)::float8                 AS date,
           ia.industry_id,
           ia.benchmark_shared_weight::float8                  AS benchmark_shared_weight,
           ia.benchmark_non_this_industry_price::float8        AS non_ind_price,
           ia.benchmark_non_this_industry_trading_amt::float8  AS non_ind_amt
    FROM analysis.industry_attributions ia
    WHERE ia.attribution_type = 'trading_amt'
      AND ia.benchmark_non_this_industry_price IS NOT NULL
      AND ia.benchmark_code IN (
          SELECT sit.code FROM stats.sec_index_tags sit
          WHERE sit.is_broad_market = TRUE)
    ORDER BY ia.benchmark_code, ia.date, ia.industry_id
"""

# industry_id -> industry_label pairs (the former industry_label CTE).
INDUSTRY_LABEL_SQL = """
    SELECT DISTINCT industry_id, industry_label
    FROM stats.sec_classification
    WHERE type = 'index' AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_label IS NOT NULL
      AND is_industry_not_strategy = TRUE
"""


# ---------------------------------------------------------------------------
#  Fetch
# ---------------------------------------------------------------------------

async def _fetch_frames(conn, benchmark_codes: list[str]) -> tuple[
        pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch the three raw frames (epoch-transport + float casts).

    Dates arrive as native float8 (extract(epoch)::float8 — the DB-read
    convention) and are materialized to datetime64[us] in ONE host pass;
    NUMERIC legs arrive as Decimal and are cast to float64 vectorized.
    """
    bench_rows = await conn.fetch(BENCH_DAILY_SQL, benchmark_codes)
    attr_rows = await conn.fetch(ATTR_DAILY_SQL)
    label_rows = await conn.fetch(INDUSTRY_LABEL_SQL)

    bench_df = pd.DataFrame(
        rec_cols(bench_rows),
        columns=["benchmark_code", "date", "close", "trading_amount"],
    )
    attr_df = pd.DataFrame(
        rec_cols(attr_rows),
        columns=["benchmark_code", "date", "industry_id",
                 "benchmark_shared_weight", "non_ind_price", "non_ind_amt"],
    )
    label_df = pd.DataFrame(
        rec_cols(label_rows),
        columns=["industry_id", "industry_label"],
    )

    for df in (bench_df, attr_df):
        df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    for col in ("close", "trading_amount"):
        bench_df[col] = bench_df[col].astype(float)
    for col in ("benchmark_shared_weight", "non_ind_price", "non_ind_amt"):
        attr_df[col] = attr_df[col].astype(float)
    return bench_df, attr_df, label_df


# ---------------------------------------------------------------------------
#  Compute (pure pandas / cuDF — vectorized)
# ---------------------------------------------------------------------------

def _compute_bench_frame(bench_df: pd.DataFrame) -> pd.DataFrame:
    """Add the benchmark LAG legs: prev close, 1d return, per-period Nd
    returns (the former bench_daily CTE).

    ``close / NULLIF(LAG(close[, N]) OVER (ORDER BY date), 0) - 1`` becomes
    a groupby shift + a zero-masked divisor (a NULL/zero lag -> NaN return,
    identical to the SQL CASE/NULLIF outcome).
    """
    df = bench_df.sort_values(
        ["benchmark_code", "date"], kind="mergesort"
    ).reset_index(drop=True)
    g = df.groupby("benchmark_code", sort=False)["close"]
    prev_close = g.shift(1)
    df["bench_prev_close"] = prev_close
    safe_prev = prev_close.where(prev_close != 0.0)
    df["bench_return_1d"] = df["close"] / safe_prev - 1.0
    for n in PERIODS:
        lag_n = g.shift(n)
        df[f"bench_return_nd_{n}"] = (
            df["close"] / lag_n.where(lag_n != 0.0) - 1.0
        )
    return df


def _compute_shared_daily(attr_df: pd.DataFrame,
                          bench_df: pd.DataFrame) -> pd.DataFrame:
    """Invert the DAILY decomposition identity per (benchmark, industry,
    date) — the former shared_daily CTE.

    NULL on any leg (weight NULL/0, bench prev close NULL/0, price NULL)
    -> NULL shared_return_1d, exactly the former CASE WHEN guard; the
    bench 1d return is non-NULL whenever that guard passes (close is
    NOT NULL in the bench row space), so it needs no extra leg.
    """
    m = attr_df.merge(
        bench_df[["benchmark_code", "date", "bench_prev_close",
                  "bench_return_1d", "trading_amount",
                  *[f"bench_return_nd_{n}" for n in PERIODS]]],
        on=["benchmark_code", "date"], how="inner",
    )
    m = m.sort_values(
        ["benchmark_code", "industry_id", "date"], kind="mergesort"
    ).reset_index(drop=True)

    weight = m["benchmark_shared_weight"]
    swf = weight / 100.0
    safe_prev = m["bench_prev_close"].where(m["bench_prev_close"] != 0.0)
    non_ind_1d = m["non_ind_price"] / safe_prev - 1.0
    guard = (
        weight.notna()
        & (weight != 0.0)
        & m["bench_prev_close"].notna()
        & (m["bench_prev_close"] != 0.0)
        & m["non_ind_price"].notna()
    )
    m["shared_return_1d"] = (
        (m["bench_return_1d"] - (1.0 - swf) * non_ind_1d) / swf
    ).where(guard)
    return m


def _compute_shared_nd(m: pd.DataFrame, n: int) -> pd.Series:
    """Compound the shared daily returns over the trailing n trading-day
    rows — the former shared_nd CTE, one window at a time.

    Window must be FULL: COUNT(shared_return_1d) OVER wnd = n (the
    ROWS-window clamps at the partition head, so partial heads fail the
    count too) — rolling(n, min_periods=n).count() == n is the same
    predicate. Daily returns outside (-0.5, 0.5] and NULLs contribute 0
    to the ln sum (the former inner CASE WHEN -> ELSE 0). np.log(1.0 + r)
    mirrors ln(1.0 + r) operation-for-operation.
    """
    r = m["shared_return_1d"]
    in_band = (r > -0.5) & (r <= 0.5)
    m["_ln_term"] = np.log(1.0 + r.where(in_band, 0.0))
    full_count = grouped_rolling_agg(
        m, _GROUP_KEYS, "shared_return_1d", n,
        min_periods=n, agg="count", sort=False,
    )
    ln_sum = grouped_rolling_agg(
        m, _GROUP_KEYS, "_ln_term", n,
        min_periods=n, agg="sum", sort=False,
    )
    nd = 100.0 * np.exp(ln_sum) - 100.0
    return nd.where(full_count == float(n))


def _rank_top5(part: pd.DataFrame, *, side: str) -> pd.DataFrame:
    """Top-5 rows of one (period, weighting) slice by metric_value —
    the former ranked CTE's ROW_NUMBER per date (DESC for HYPE, ASC for
    DRAIN), scoped here per (benchmark, date).

    Stable sort + groupby cumcount: equal metrics keep input row order
    (the SQL left equal-key order to the planner — see the docstring's
    tie note).
    """
    ascending = side == "DRAIN"
    ordered = part.sort_values(
        ["benchmark_code", "date", "metric_value"],
        ascending=[True, True, ascending], kind="mergesort",
    )
    rank = ordered.groupby(
        ["benchmark_code", "date"], sort=False
    ).cumcount() + 1
    top = ordered[rank <= 5].copy()
    top["rank_side"] = side
    top["rank"] = rank[rank <= 5].astype("int64")
    return top


def _compute_hypes_and_drains(
    bench_df: pd.DataFrame,
    attr_df: pd.DataFrame,
    label_df: pd.DataFrame,
) -> pd.DataFrame:
    """Full per-date ranking frame for ALL (benchmark, period, weighting).

    One vectorized pass replaces the former benchmarks x periods x
    weightings INSERT loop: the daily legs are computed once, the
    per-period compounding once per window, and each (period, weighting)
    slice is ranked + emitted with the same columns / rounding the
    former INSERT SELECT produced.
    """
    if attr_df.empty or bench_df.empty:
        return pd.DataFrame(columns=HD_COLUMNS)

    m = _compute_shared_daily(attr_df, _compute_bench_frame(bench_df))
    if m.empty:
        return pd.DataFrame(columns=HD_COLUMNS)

    # Per-period compounded shared returns (one grouped-rolling pass each).
    for n in PERIODS:
        m[f"shared_return_nd_{n}"] = _compute_shared_nd(m, n)
    m = m.drop(columns=["_ln_term"])

    bench_amt = m["trading_amount"]
    non_amt = m["non_ind_amt"]
    amt_diff = bench_amt - non_amt
    shared_trading_amt = amt_diff.where(
        bench_amt.notna() & non_amt.notna() & (amt_diff > 0.0)
    )

    parts: list[pd.DataFrame] = []
    for n in PERIODS:
        bench_nd = m[f"bench_return_nd_{n}"]
        industry_nd = m[f"shared_return_nd_{n}"] / 100.0
        hype = industry_nd - bench_nd
        base_ok = m[f"shared_return_nd_{n}"].notna() & bench_nd.notna()
        for weighting in ("equal", "amt"):
            metric = hype if weighting == "equal" \
                else hype * shared_trading_amt
            part = pd.DataFrame({
                "date": m["date"],
                "benchmark_code": m["benchmark_code"],
                "period_days": np.int64(n),
                "weighting": weighting,
                "industry_id": m["industry_id"],
                "metric_value": metric,
                "shared_trading_amt": shared_trading_amt,
                "benchmark_return_nd": bench_nd,
                "industry_return_nd": industry_nd,
                "benchmark_shared_weight": m["benchmark_shared_weight"],
            })
            part = part[base_ok & part["metric_value"].notna()]
            if part.empty:
                continue
            parts.append(_rank_top5(part, side="HYPE"))
            parts.append(_rank_top5(part, side="DRAIN"))

    if not parts:
        return pd.DataFrame(columns=HD_COLUMNS)

    out = pd.concat(parts, ignore_index=True)
    # COALESCE(il.industry_label, r.industry_id) — the former final-SELECT
    # join against the DISTINCT label pairs (a duplicated pair would fan
    # out identically there).
    if not label_df.empty:
        out = out.merge(label_df, on="industry_id", how="left")
        out["industry_label"] = out["industry_label"].fillna(
            out["industry_id"])
    else:
        out["industry_label"] = out["industry_id"]

    # Storage rounding — the former ROUND(...::numeric, k) per column.
    # Ranked on UNROUNDED metric_value above; rounding happens here (the
    # SQL rounded in the final SELECT, after ranked).
    for col, ndp in (
        ("metric_value", 6), ("shared_trading_amt", 4),
        ("benchmark_return_nd", 6), ("industry_return_nd", 6),
        ("benchmark_shared_weight", 4),
    ):
        out[col] = np.round(out[col].astype(float), ndp)
    out = out[HD_COLUMNS]
    out["rank"] = out["rank"].astype("int64")
    out["period_days"] = out["period_days"].astype("int64")
    return out


def _compute_seasonal(hd_df: pd.DataFrame) -> pd.DataFrame:
    """Monthly (seasonal) top-5 re-ranking from the stored per-date rows.

    The former _SEASONAL_INSERT_SQL: per (month, benchmark, period,
    weighting, side, industry) peak_metric_value = MAX (HYPE) / MIN (DRAIN)
    of the (already 6dp-rounded) metric_value, then ROW_NUMBER per
    (month, benchmark, period, weighting, side) — DESC for HYPE, ASC for
    DRAIN — keeping rank <= 5. season_year/month/qkey/start/end are pure
    functions of the month bucket (host numpy, no per-group aggregation).
    """
    if hd_df.empty:
        return pd.DataFrame(columns=SEASONAL_COLUMNS)

    df = hd_df
    months = host_array(df["date"].to_numpy()).astype("datetime64[M]")
    df = df.assign(season_qkey=months.astype(str))

    gk = ["season_qkey", "benchmark_code", "period_days", "weighting",
          "rank_side", "industry_id"]
    parts: list[pd.DataFrame] = []
    for side, agg in (("HYPE", "max"), ("DRAIN", "min")):
        sub = df[df["rank_side"] == side]
        if sub.empty:
            continue
        gb = sub.groupby(gk, sort=False)
        monthly = pd.concat([
            gb["metric_value"].agg(agg).rename("peak_metric_value"),
            gb["industry_label"].max().rename("industry_label"),
        ], axis=1).reset_index()

        ascending = side == "DRAIN"
        ordered = monthly.sort_values(
            ["season_qkey", "benchmark_code", "period_days", "weighting",
             "rank_side", "peak_metric_value"],
            ascending=[True] * 5 + [ascending], kind="mergesort",
        )
        rank = ordered.groupby(
            ["season_qkey", "benchmark_code", "period_days", "weighting",
             "rank_side"], sort=False
        ).cumcount() + 1
        top = ordered[rank <= 5].copy()
        top["rank"] = rank[rank <= 5].astype("int64")
        parts.append(top)

    if not parts:
        return pd.DataFrame(columns=SEASONAL_COLUMNS)

    out = pd.concat(parts, ignore_index=True)
    # Month parts from the qkey ('YYYY-MM', matching to_char(date,'YYYY-MM')).
    qm = host_array(out["season_qkey"].to_numpy()).astype("datetime64[M]")
    out["season_year"] = (
        qm.astype("datetime64[Y]").astype("int64") + 1970
    ).astype("int64")
    out["season_month"] = ((qm.astype("int64") % 12) + 1).astype("int64")
    out["season_start"] = qm.astype("datetime64[D]").astype("datetime64[us]")
    out["season_end"] = (
        (qm + np.timedelta64(1, "M")).astype("datetime64[D]")
        - np.timedelta64(1, "D")
    ).astype("datetime64[us]")
    out["peak_metric_value"] = np.round(
        out["peak_metric_value"].astype(float), 6)
    out = out[SEASONAL_COLUMNS]
    for col in ("season_year", "season_month", "period_days", "rank"):
        out[col] = out[col].astype("int64")
    return out


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_hypes_and_drains(
    conn,
    *,
    force: bool = True,
) -> None:
    """Run the industry hypes & drains ranking pipeline.

    Reuses the caller's DB connection. Fetches the raw frames, computes
    the full ranking vectorized, then truncates + COPY-writes (force is
    the default — the table is small and cheap to fully recompute).

    Pipeline
      1. Guard: bail out if industry_attributions has no broad-market rows
         with daily non-industry price data.
      2. Fetch all broad-market benchmark codes.
      3. Fetch + compute the per-date HYPE/DRAIN rankings (pandas).
      4. Truncate analysis.industry_hypes_and_drains, CSV-COPY the frame.
      5. Upsert analysis.analysis_identity.
      6. Sanity summary by (benchmark_code, period, weighting).
      7. Seasonal (monthly) aggregation.

    Args:
      force: when True (default), truncate + recompute.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  INDUSTRY HYPES & DRAINS (internal step of industry_sentiments)")
    logger.info("=" * 78)

    # ---- Step 1: guard -----------------------------------------------
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    if not n_src:
        logger.info("\n[hd1/6] industry_attributions has no broad-market rows "
              "with daily non-industry price data — nothing to rank. "
              "Skipping hypes_and_drains step.")
        return
    logger.info(f"\n[hd1/6] Source analysis.industry_attributions: "
          f"{n_src:,} broad-market rows with daily non-industry price data.")

    # ---- Step 2: fetch broad-market benchmark codes -----------------
    benchmark_codes = [r["benchmark_code"] for r in await conn.fetch(
        BROAD_MARKET_BENCHMARKS_SQL
    )]
    logger.info(f"\n[hd2/6] Found {len(benchmark_codes)} broad-market benchmarks: "
          f"{', '.join(benchmark_codes)}")

    # ---- Step 3: fetch raw frames + vectorized compute --------------
    bench_df, attr_df, label_df = await _fetch_frames(conn, benchmark_codes)
    logger.info(f"[hd3/6] Fetched bench={len(bench_df):,} rows, "
          f"attr={len(attr_df):,} rows, labels={len(label_df):,} — "
          f"computing rankings (periods={list(PERIODS)})...")
    hd_df = _compute_hypes_and_drains(bench_df, attr_df, label_df)
    del bench_df, attr_df
    gc.collect()
    logger.info(f"        -> {len(hd_df):,} ranked rows "
          f"({time.time() - t0:.1f}s)")

    # ---- Step 4: truncate + COPY ------------------------------------
    logger.info(f"\n[hd4/6] Truncating {TABLE} (full recompute)...")
    await truncate_table_async(conn, TABLE)
    if not hd_df.empty:
        n_inserted = await copy_frame_chunked_async(
            conn, TABLE, hd_df,
            columns=HD_COLUMNS,
            numeric_cols=HD_FLOAT_COLUMNS + ["period_days", "rank"],
            round_to=None,  # pre-rounded per column in _compute_*
            date_cols=["date"],
            label="[hd4/6]",
        )
        logger.info(f"        inserted {n_inserted:,} rows")
    else:
        logger.info("        no rows to insert.")

    # ---- Step 5: upsert analysis_identity ---------------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    # ---- Step 6: sanity summary -------------------------------------
    summary = await conn.fetch("""
        SELECT benchmark_code, period_days, weighting,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT industry_id) AS n_industries,
               MIN(date) AS first_date,
               MAX(date) AS last_date,
               ROUND(AVG(metric_value), 6) AS avg_metric
        FROM analysis.industry_hypes_and_drains
        GROUP BY benchmark_code, period_days, weighting
        ORDER BY benchmark_code, period_days, weighting
        LIMIT 60
    """)
    logger.info("\n      Summary by (benchmark, period, weighting) [first 60]:")
    for r in summary:
        logger.info(f"        {r['benchmark_code']} {r['period_days']:>3d}d "
              f"{r['weighting']:5s}: "
              f"{r['n_rows']:>7,} rows . "
              f"{r['n_industries']:>3} ind . "
              f"{r['first_date']} -> {r['last_date']} . "
              f"avg_metric={r['avg_metric']}")

    logger.info(f"\n  hypes_and_drains wall time: {time.time() - t0:.1f}s")

    # ---- Seasonal (monthly) aggregation -----------------------------
    await run_hypes_and_drains_seasonal(conn, hd_df)


# ---------------------------------------------------------------------------
#  Seasonal (monthly) aggregation pipeline
# ---------------------------------------------------------------------------

async def run_hypes_and_drains_seasonal(
    conn,
    hd_df: pd.DataFrame | None = None,
) -> None:
    """Aggregate per-date rankings into per-month (seasonal) rankings.

    Truncates analysis.industry_hypes_seasonal, then inserts one row per
    (season_qkey, benchmark_code, period_days, rank_side, rank) — 10 rows
    per (month, benchmark, period): 5 HYPE + 5 DRAIN.

    Ranking method:
      HYPE:  peak_metric_value = MAX(metric_value) in the month.
      DRAIN: peak_metric_value = MIN(metric_value) in the month.
    Industries are ranked by peak_metric_value (DESC for HYPE, ASC for
    DRAIN) and the top-5 per side are kept.

    Must be called AFTER run_hypes_and_drains has populated the per-date
    table. ``hd_df`` (the computed per-date frame) is passed by the
    pipeline to avoid re-reading the table; when omitted the stored rows
    are fetched instead (standalone re-runs).
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  INDUSTRY HYPES & DRAINS — SEASONAL (monthly) aggregation")
    logger.info("=" * 78)

    # ---- Source rows (the pipeline frame, or the stored table) -------
    if hd_df is None:
        rows = await conn.fetch(f"""
            SELECT extract(epoch from date)::float8 AS date,
                   benchmark_code, period_days, weighting, rank_side,
                   industry_id, industry_label, metric_value::float8
                   AS metric_value
            FROM {TABLE}
            WHERE metric_value IS NOT NULL
        """)
        hd_df = pd.DataFrame(rec_cols(rows), columns=[
            "date", "benchmark_code", "period_days", "weighting",
            "rank_side", "industry_id", "industry_label", "metric_value",
        ])
        hd_df["date"] = epoch_col_to_dt64(hd_df["date"], index=hd_df.index)
        hd_df["metric_value"] = hd_df["metric_value"].astype(float)

    # ---- Compute monthly re-ranking ----------------------------------
    seasonal_df = _compute_seasonal(hd_df)
    del hd_df
    gc.collect()

    # ---- Truncate + COPY ---------------------------------------------
    logger.info(f"\n[hd-s1/3] Truncating {SEASONAL_TABLE}...")
    await truncate_table_async(conn, SEASONAL_TABLE)
    if not seasonal_df.empty:
        logger.info("[hd-s2/3] Copying monthly rankings...")
        n_inserted = await copy_frame_chunked_async(
            conn, SEASONAL_TABLE, seasonal_df,
            columns=SEASONAL_COLUMNS,
            numeric_cols=SEASONAL_FLOAT_COLUMNS
                         + ["season_year", "season_month", "period_days",
                            "rank"],
            round_to=None,
            date_cols=["season_start", "season_end"],
            partition_key="season_qkey",
            label="[hd-s2/3]",
        )
        logger.info(f"  inserted {n_inserted:,} seasonal ranking rows "
              f"({time.time() - t0:.1f}s)")
    else:
        logger.info(f"[hd-s2/3] no seasonal rows to insert "
              f"({time.time() - t0:.1f}s)")

    # ---- Summary -----------------------------------------------------
    summary = await conn.fetch("""
        SELECT
            benchmark_code,
            period_days,
            COUNT(DISTINCT season_qkey)  AS n_seasons,
            COUNT(*)                     AS n_rows,
            COUNT(DISTINCT industry_id)  AS n_industries,
            MIN(season_qkey)             AS first_season,
            MAX(season_qkey)             AS last_season
        FROM analysis.industry_hypes_seasonal
        GROUP BY benchmark_code, period_days
        ORDER BY benchmark_code, period_days
        LIMIT 30
    """)
    logger.info("\n      Seasonal summary by (benchmark, period) [first 30]:")
    for r in summary:
        logger.info(f"        {r['benchmark_code']} {r['period_days']:>3d}d: "
              f"{r['n_seasons']:>2} seasons . "
              f"{r['n_rows']:>5,} rows . "
              f"{r['n_industries']:>3} ind . "
              f"{r['first_season']} -> {r['last_season']}")

    logger.info(f"\n  seasonal aggregation wall time: {time.time() - t0:.1f}s")
