"""Entry point for analysis.market_crowdedness.

Run via ``python -m analysis.market_crowdedness``.

Market Crowdedness — per-(sec_type, code, date) crowdedness curve for
indices and composite industries (analysis.market_crowdedness; see
database/sql/analysis/29_market_crowdedness.sql). Serves the Commons
"Market Crowdedness" page (/analysis/commons/market-crowdedness) via
/api/analysis/market-crowdedness/*.

METRIC v2 (2026-10-31 — percentile-normalized multi-pillar score; v1
blended RAW concentration/flow levels, conflating a pool's structural
top-heaviness with speculative crowding and carrying no valuation /
co-movement information)
  Pool per code (LATEST-SNAPSHOT house convention):
    index    — latest stats.sec_composition snapshot of the index
               (source_type='index', weight_pct > 0);
    industry — DISTINCT union of member stocks across the industry's
               member indices (stats.sec_classification type='index',
               is_active, is_industry_not_strategy).
  On each date the ACTIVE pool = member stocks with trading_amount > 0
  that day (stats.stock_liquidity_margin, yuan); the HOT set = the top
  HOT_POOL_FRACTION (10%) of the active pool by the stock's
  HOT_MA_WINDOW (60) ACTIVE-DAY MEAN trading amount as of the date,
  floored at MIN_HOT_N (5) names, requiring HOT_MA_MIN_OBS (20) active
  days to rank (the STABLE hot list: the naive day-amount list churned
  17-29% of its names daily vs 1-2% here — study 2026-11-01; re-ranked
  and re-sized every date, different per index/industry).

  Turnover-concentration EVIDENCE (not score inputs — the study showed
  gini / effective-N duplicate the top-k shares, |rho| ~ 0.92-0.94):
    top1_share / top5_share / top10_share = the top 1%/5%/10% of the
        active pool by DAY amount (min 1 name) and their share of the
        day's pool amount (the classic 换手集中度 curve family)
    effective_n = (Σ amt)² / Σ amt² — the effective traded-name COUNT
        (inverse Herfindahl; robust to head-list churn)

  The turnover pillars read the PILLAR_MA_WINDOW (5) active-day
  MEAN amount — the smoothing study (2026-11-01) showed single-day
  Δamt drove 63% of day-over-day score movement while hot-list
  variants moved noise < 2%; the ma5 basis cuts score noise 43% and
  keeps 88% of event peaks.

  Four RAW pillars per (pool, date), each stored as evidence:
    concentration = Σ hot ma5-amt / Σ pool ma5-amt (the STABLE hot
                    set's share of the pool's 5-day-mean trading)
    flow_change   = Σ hot max(Δma5,0) / Σ pool max(Δma5,0)
                    (the gross INCREASE of the 5-day mean amount —
                    sustained multi-day inflow, not one-day spikes)
    comovement    = mean over the hot set of the trailing-CORR_WINDOW
                    (120 active-day, ~6-month) Pearson correlation between the
                    stock's daily return (pct_change/100, falling
                    back to close/prev_close − 1 where the source
                    leaves pct_change NULL — the SSE archive) and the
                    pool's leave-one-out equal-weight return of the
                    day's active members (≥ MIN_CORR_OBS (30)
                    joint-valid obs, positive variance) — the
                    co-movement pillar: crowded pools trade as one bloc
    valuation     = mean over the hot set of the stock's pe
                    cross-sectional percentile within the day's active
                    pool (stock_basic_stats.pe; NULL pe excluded) —
                    the valuation-spread pillar: 0.5 = hot names at
                    the pool median

  PERCENTILE NORMALIZATION (the v2 core): each pillar is read against
  the pool's OWN history — x_pct(t) = share of non-null pillar values
  in the trailing PCT_WINDOW (250) pool-date rows that are ≤ x(t),
  requiring MIN_PCT_OBS (60) non-null observations. A structurally
  top-heavy pool no longer scores crowded on quiet days; only moves
  toward the pool's own extremes do.

  crowdedness_score = renormalized blend of the four percentile
  pillars (weights W_CONCENTRATION/W_FLOW/W_COMOVEMENT/W_VALUATION =
  0.30/0.30/0.25/0.15) over the pillars defined on the date — the two
  turnover pillars are required, comovement/valuation drop out with
  their weights when their raw pillar is undefined.

  Rows exist only where defined (active pool >= MIN_ACTIVE_POOL —
  below it the floored hot set would cover half the pool or more;
  concentration_pct/flow_pct additionally need the percentile
  history) — the analysis.pe NULL rule. The percentile warm-up drops
  each pool's first ~quarter-year of defined days.

SCOPE: both sides cover the FULL universe — index side = all active
classified indexes (stats.sec_classification type='index', is_active,
composition-backed; explicitly authorized 2026-10-31, was the 10 study
indices during the initial implementation); industry side = the full
composite-industry universe.

HOST-BOUNDARY NOTE (the sanctioned per-group cores): (1) the
per-(pool, date) hot-set NAME join (top_stocks) is built host-side —
hot rows are unwrapped once via host_array and re-materialized through
assign (the _compute_seasonal season_qkey pattern); (2) the rolling
PILLAR PERCENTILES run host-side on the small per-(pool, date) agg
frame — rolling rank is a gather/index algorithm with no cuDF-native
form (sliding_window_view per pool-pillar); every other numeric
aggregation (including the correlation algebra via grouped rolling
sums over the big frame) stays whole-frame on GPU.

COMPUTE CHUNKING: the driver processes pools in WHOLE-POOL batches
under CHUNK_PAIR_BUDGET membership pairs (an unchunked full-universe
membership x amounts frame exhausts a 32 GB GPU). Every pillar is
per-(sec_type, pool) and chunks never split a pool, so the output is
identical to the unchunked pass. The DB write keeps the bulk-write
discipline: force mode COPYs through copy_frame_chunked_async
(partition-key-aligned chunks, one commit per chunk, the default
0.5 s inter-chunk cool-down — escalating to 10 s on 20+-chunk writes —
plus the replica-lag wait; never disabled), incremental mode upserts
via bulk_upsert_async.

The :class:`MarketCrowdednessAnalysis` entry class (a
:class:`DataAnalysis` subclass) owns the runtime lifecycle + DB
connection lifecycle (opened by amain() before run(), timed-closed
after). Bootstrap-first layout: the runtime setup runs before this
module's imports.
"""
from __future__ import annotations

import datetime
import gc
import time

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import numpy as np
import pandas as pd

from _common.log_setup import setup_logging

from _common.build_commons import rec_cols
from _common.data_analysis import DataAnalysis
from _common.db_commons import (
    analyze_tables_async,
    bulk_upsert_async,
    copy_frame_chunked_async,
)
from _common.df_utils import (
    epoch_col_to_dt64,
    grouped_rolling_agg,
    host_array,
    host_unique,
)
from _common.labels import SEC_TYPE_CODE
from analysis._common import sanitize_for_db_insert

logger = setup_logging("market_crowdedness")


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.market_crowdedness"
ANALYSIS_NAME = "market_crowdedness"
ANALYSIS_DESCRIPTION = (
    "Per-(code, sec_type, date) market crowdedness curve. Both sides "
    "cover the FULL universe: index side = all active classified indexes "
    "(stats.sec_classification type='index', is_active, composition-"
    "backed); industry side = the full composite-industry universe. "
    "v2 percentile-normalized multi-pillar score: crowdedness_score = "
    "renormalized blend 0.30*concentration_pct + 0.30*flow_pct + "
    "0.25*comovement_pct + 0.15*valuation_pct over the pillars defined "
    "on the date, where each *_pct is its pillar's percentile vs the "
    "pool's own trailing-250-pool-date-row history (>= 60 non-null "
    "obs). HOT SET = top 10% of the day's active pool by the "
    "60-active-day MEAN trading amount (floored at 5 names; >= 20 "
"active days to rank — the naive day-amount ranking churned 17-29% "
    "of its names daily vs 1-2%, study 2026-11-01). The turnover "
    "pillars read the 5-active-day MEAN amount (score noise -43%, "
    "event peaks 88% preserved — smoothing study 2026-11-01). RAW "
    "pillars kept as evidence: concentration = the stable hot set's "
    "share of the pool's 5-day-mean amount; flow_change = the hot "
    "set's share of the pool's gross Δ of the 5-day mean amount "
    "(sustained multi-day inflow); comovement = mean corr120(hot stock return, leave-one-out "
    "pool return) over the hot set; valuation = mean hot pe "
    "cross-sectional percentile within the pool. Turnover-"
    "concentration evidence (not score inputs): top1_share/top5_share/"
    "top10_share = the top 1%/5%/10% by DAY amount and their share of "
    "the pool amount; effective_n = the effective traded-name count "
    "(Σamt)²/Σamt² (inverse Herfindahl). All score inputs 0..1, higher "
    "= trading more crowded into the hot names vs the pool's own "
    "history; top_stocks carries the hot-set names as tooltip "
    "evidence. Built by analysis.market_crowdedness (incremental by "
    "date; --force deletes the scoped sec_types and COPY-rebuilds)."
)

# The mandatory study scope (analysis/strategy house rule) — kept only as
# documentation: the index side covers the FULL active classified index
# universe (stats.sec_classification type='index', is_active) since the
# 2026-10-31 explicit full-universe authorization; the industry side
# covers the full composite-industry universe.
STUDY_INDEX_CODES: tuple[str, ...] = (
    "000001", "931743", "931160", "931071", "000015", "930708",
    "931994", "931594", "000683", "000991",
)

HOT_POOL_FRACTION = 0.10  # hot set = top 10% of the day's active pool ...
MIN_HOT_N = 5             # ... floored at 5 names
MIN_ACTIVE_POOL = 10  # below this the floored hot set (5) would cover
                      # half the pool or more — no defined score row

# STABLE hot-set ranking: the 60-active-day MEAN amount, not the day's
# amount (study 2026-11-01, 10 study indices: the day-amount top-10%
# replaced 17-29% of its names EVERY DAY vs 1.0-1.9% for the ma60 list —
# the day ranking was noise). A stock needs HOT_MA_MIN_OBS active days
# to be rankable (flash-listed names cannot enter the hot set).
HOT_MA_WINDOW = 60    # trailing active-day rows of the ranking basis
HOT_MA_MIN_OBS = 20

# PILLAR AMOUNT BASIS: the turnover pillars (concentration / flow) read
# the 5-ACTIVE-DAY MEAN amount, not the day amount — the smoothing study
# (2026-11-01, 10 indices) showed flow_change on single-day Δamt drove
# 63% of day-over-day score movement while the hot-list variants
# (hysteresis, ma120 basis) moved noise by <2%; the ma5 basis cuts score
# noise 43% and keeps 88% of event peaks (flow-only: -23% noise, 99%
# peaks). flow = the hot share of gross Δma5 = sustained multi-day
# inflow, not one-day spikes.
PILLAR_MA_WINDOW = 5

# Turnover-concentration evidence family (not score inputs — the study
# showed gini / effective-N carry the same signal as the top-k shares,
# |rho| ~ 0.92-0.94): day-amount top cuts + effective traded-name count.
TOP_CUTS: tuple[tuple[float, str], ...] = (
    (0.01, "top1_share"), (0.05, "top5_share"), (0.10, "top10_share"),
)

# COMPUTE CHUNKING: every pillar is strictly per-(sec_type, pool) —
# concentration/flow per pool-date, pool return, pe cross-section, corr
# group keys, percentile history — so the compute runs in WHOLE-POOL
# chunks under this membership-pair budget. One unchunked pass over the
# full-universe membership x amounts frame (index side alone ~60M+ rows)
# exhausts a 32 GB GPU; a chunk of <= 8,000 pairs x ~1,300 trading days
# (~10M rows peak incl. the corr-phase product columns) fits with
# headroom. Chunks never split a pool, so results are identical to the
# unchunked pass.
CHUNK_PAIR_BUDGET = 8_000

# v2 pillars ----------------------------------------------------------------
# 120 active rows (~6 months) not 60: stats.stock_basic_stats.pct_change
# covers only ~60-75% of member-days, so a 60-row window holds ~36 valid
# obs — right at the MIN_CORR_OBS gate, leaving the pillar NULL on ~40%
# of index-side rows. 120 rows yield ~70 valid obs at that coverage.
CORR_WINDOW = 120      # trailing active-day rows for the co-movement corr
MIN_CORR_OBS = 30      # joint-valid return obs for a defined correlation
PCT_WINDOW = 250       # trailing pool-date rows of the percentile window
MIN_PCT_OBS = 60       # non-null pillar obs required in that window

# Score weights over the percentile pillars (renormalized over the
# pillars defined on the date; the two turnover pillars are required).
# Rationale: the turnover pillars keep 60% combined (the v1 core),
# co-movement gets the largest add-on (direct unwind-risk proxy), pe
# the smallest (weakest source data — estimated PEs, loss-maker NULLs).
W_CONCENTRATION = 0.30
W_FLOW = 0.30
W_COMOVEMENT = 0.25
W_VALUATION = 0.15

# Calendar-day buffer so an incremental window re-derives everything a
# target date needs: 250 trading rows ≈ 363 calendar days for the
# percentile history + 60 active rows ≈ 87 days for the corr warm-up +
# the Δamt previous-traded-day + slack.
INC_LOOKBACK_DAYS = 440

OUT_COLUMNS: list[str] = [
    "sec_type", "code", "date", "crowdedness_score",
    "concentration", "flow_change", "comovement", "valuation",
    "top1_share", "top5_share", "top10_share", "effective_n",
    "concentration_pct", "flow_pct", "comovement_pct", "valuation_pct",
    "top_stocks",
]
OUT_FLOAT_COLUMNS: list[str] = [
    "crowdedness_score", "concentration", "flow_change", "comovement",
    "valuation", "top1_share", "top5_share", "top10_share",
    "effective_n", "concentration_pct", "flow_pct", "comovement_pct",
    "valuation_pct",
]

IDENTITY_TABLE = {
    "index": "stats.index_identity",
    "industry": "stats.industry_identity",
}


# ---------------------------------------------------------------------------
#  Raw-input SQL (fetch only — every computation lives in pandas below)
# ---------------------------------------------------------------------------

# Full active classified index universe (the UI nav's index side:
# stats.sec_classification type='index', is_active) — latest composition
# snapshot per index. Explicitly authorized full-universe scope (2026-10-31;
# was the 10 study indices during the initial implementation).
INDEX_MEMBERSHIP_SQL = """
    WITH latest AS (
        SELECT code, MAX(snapshot_date) AS max_date
        FROM stats.sec_composition
        WHERE source_type = 'index'
          AND stock_code IS NOT NULL
        GROUP BY code
    )
    SELECT sc.code AS pool_code, sc.stock_code
    FROM stats.sec_composition sc
    JOIN latest ld
        ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
      AND sc.weight_pct > 0
      AND sc.code IN (
          SELECT cls.code FROM stats.sec_classification cls
          WHERE cls.type = 'index' AND cls.is_active = TRUE
      )
"""

# Industry pools: the builds.cross_stats latest/holdings/industry_stocks
# CTE chain (the single source of truth for industry-union membership).
INDUSTRY_MEMBERSHIP_SQL = """
    WITH latest AS (
        SELECT code, MAX(snapshot_date) AS max_date
        FROM stats.sec_composition
        WHERE source_type = 'index'
          AND stock_code IS NOT NULL
        GROUP BY code
    ),
    holdings AS (
        SELECT sc.code, sc.stock_code
        FROM stats.sec_composition sc
        JOIN latest ld
            ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
        WHERE sc.source_type = 'index'
          AND sc.stock_code IS NOT NULL
    ),
    industry_stocks AS (
        SELECT DISTINCT cls.industry_id, h.stock_code
        FROM holdings h
        JOIN stats.sec_classification cls
            ON cls.code = h.code AND cls.type = 'index'
        WHERE cls.industry_id IS NOT NULL
          AND cls.industry_id <> ''
          AND cls.is_active = TRUE
          AND cls.is_industry_not_strategy = TRUE
    )
    SELECT industry_id AS pool_code, stock_code FROM industry_stocks
"""

# Per-stock daily trading amount (yuan). trading_amount > 0 drops the
# streaming placeholder rows and suspended stocks — they are outside the
# day's active pool by definition.
AMOUNT_SQL = """
    SELECT code AS stock_code,
           extract(epoch from date)::float8 AS date,
           trading_amount::float8 AS amt
    FROM stats.stock_liquidity_margin
    WHERE code = ANY($1::text[])
      AND trading_amount > 0
      AND date >= $2::date
      AND date <= $3::date
    ORDER BY code, date ASC
"""

# Per-stock daily return + pe (the v2 co-movement / valuation pillars).
# pct_change is NULL on the SSE archive (streaming-era rows only
# partially populate it), while close is fully populated — the return is
# derived as close/prev_close − 1 per stock where pct_change is missing
# (the same change/prev_close identity the SSE source itself uses for
# pct_change). pe is NULL for loss-making / no-data names.
RETURN_SQL = """
    SELECT code AS stock_code,
           extract(epoch from date)::float8 AS date,
           pct_change::float8 AS pct,
           close::float8 AS close,
           pe::float8 AS pe
    FROM stats.stock_basic_stats
    WHERE code = ANY($1::text[])
      AND (pct_change IS NOT NULL OR close IS NOT NULL OR pe IS NOT NULL)
      AND date >= $2::date
      AND date <= $3::date
    ORDER BY code, date ASC
"""

# Latest display name per member stock (top_stocks evidence).
STOCK_NAME_SQL = """
    SELECT DISTINCT ON (code) code AS stock_code, name
    FROM stats.stock_identity
    WHERE code = ANY($1::text[])
    ORDER BY code, date DESC
"""


# ---------------------------------------------------------------------------
#  Fetch
# ---------------------------------------------------------------------------

async def _fetch_membership(conn, sec_types: tuple[str, ...]) -> pd.DataFrame:
    """Fetch the per-(pool, stock) membership frames for the scoped
    sec_types, concatenated into ONE frame [sec_type, pool_code,
    stock_code] (int16 sec_type code rides the frame — the frame-passenger
    rule)."""
    parts: list[pd.DataFrame] = []
    if "index" in sec_types:
        rows = await conn.fetch(INDEX_MEMBERSHIP_SQL)
        if rows:
            df = pd.DataFrame(rec_cols(rows),
                              columns=["pool_code", "stock_code"])
            df = df.assign(sec_type=np.int16(SEC_TYPE_CODE["index"]))
            parts.append(df)
    if "industry" in sec_types:
        rows = await conn.fetch(INDUSTRY_MEMBERSHIP_SQL)
        if rows:
            df = pd.DataFrame(rec_cols(rows),
                              columns=["pool_code", "stock_code"])
            df = df.assign(sec_type=np.int16(SEC_TYPE_CODE["industry"]))
            parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["sec_type", "pool_code", "stock_code"])
    members = pd.concat(parts, ignore_index=True)
    # Defensive dedup (a stock listed twice in one snapshot / via several
    # member indices must not double-count in the pool).
    members = members.drop_duplicates(subset=["sec_type", "pool_code",
                                              "stock_code"])
    return members


async def _fetch_amounts(
    conn,
    stock_codes: list[str],
    lo: datetime.date,
    hi: datetime.date,
) -> pd.DataFrame:
    """Fetch per-(stock, date) trading amount for the window —
    [stock_code, date(datetime64[us]), amt(float64)]."""
    if not stock_codes:
        return pd.DataFrame(columns=["stock_code", "date", "amt"])
    rows = await conn.fetch(AMOUNT_SQL, sorted(stock_codes), lo, hi)
    if not rows:
        return pd.DataFrame(columns=["stock_code", "date", "amt"])
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=["stock_code", "date", "amt"])


async def _fetch_returns(
    conn,
    stock_codes: list[str],
    lo: datetime.date,
    hi: datetime.date,
) -> pd.DataFrame:
    """Fetch per-(stock, date) daily return + pe for the window —
    [stock_code, date(datetime64[us]), ret(float64), pe(float64)].

    ret = pct_change/100 where published, else close/prev_close − 1 over
    the stock's own calendar rows (the SSE-archive gap route; the SQL
    arrives sorted by (stock_code, date) so the grouped shift sees
    correct order)."""
    empty_cols = ["stock_code", "date", "ret", "pe"]
    if not stock_codes:
        return pd.DataFrame(columns=empty_cols)
    rows = await conn.fetch(RETURN_SQL, sorted(stock_codes), lo, hi)
    if not rows:
        return pd.DataFrame(columns=empty_cols)
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    df = pd.DataFrame(
        cols, columns=["stock_code", "date", "pct", "close", "pe"])
    prev_close = df.groupby("stock_code", sort=False)["close"].shift(1)
    ret_from_close = df["close"] / prev_close - 1.0
    df["ret"] = np.where(
        df["pct"].notna(), df["pct"] / 100.0, ret_from_close)
    return df.drop(columns=["pct", "close"])


async def _fetch_stock_names(conn, stock_codes: list[str]) -> dict[str, str]:
    """Latest display name per stock code (name falls back to the code)."""
    if not stock_codes:
        return {}
    rows = await conn.fetch(STOCK_NAME_SQL, sorted(stock_codes))
    return {
        r["stock_code"]: (r["name"] if r["name"] else r["stock_code"])
        for r in rows
    }


# ---------------------------------------------------------------------------
#  Compute (pure pandas / cuDF — vectorized, whole-frame passes + the
#  two sanctioned host cores)
# ---------------------------------------------------------------------------

def _clip01(s: pd.Series) -> pd.Series:
    """Clamp to [0, 1] with the cuDF-native scalar-bounds form."""
    return np.minimum(np.maximum(s, 0.0), 1.0)


def _build_top_stocks(
    hot: pd.DataFrame,
    agg: pd.DataFrame,
    names: dict[str, str],
) -> np.ndarray:
    """Host-boundary name join: per (sec_type, pool, date) comma-join the
    hot set's display names in ma60-amount order.

    The rows arrive sorted by (sec_type, pool_code, date, hot_rank) —
    hot_rank was assigned over an amt_ma60-desc sort — so one linear pass
    fills rank-indexed slots per group (bucket size = the group's hot_n);
    the result is a host object ndarray re-attached via assign (the
    _compute_seasonal season_qkey pattern — strings never ride the frame
    through sort/merge/concat).
    """
    sec_h = host_array(hot["sec_type"].to_numpy()).astype("int64")
    pool_h = host_array(hot["pool_code"].to_numpy())
    date_h = (host_array(hot["date"].to_numpy())
              .astype("datetime64[ns]").astype("int64"))
    rank_h = host_array(hot["hot_rank"].to_numpy()).astype("int64")
    hotn_h = host_array(hot["hot_n"].to_numpy()).astype("int64")
    stock_h = host_array(hot["stock_code"].to_numpy())

    buckets: dict[tuple[int, str, int], list] = {}
    for s, p, d, r, hn, sc in zip(sec_h, pool_h, date_h, rank_h, hotn_h,
                                  stock_h):
        key = (int(s), str(p), int(d))
        lst = buckets.get(key)
        if lst is None:
            lst = [None] * int(hn)
            buckets[key] = lst
        if r <= len(lst):
            lst[r - 1] = names.get(sc, sc)

    sec_a = host_array(agg["sec_type"].to_numpy()).astype("int64")
    pool_a = host_array(agg["pool_code"].to_numpy())
    date_a = (host_array(agg["date"].to_numpy())
              .astype("datetime64[ns]").astype("int64"))
    top_arr = np.empty(len(agg), dtype=object)
    for i in range(len(agg)):
        lst = buckets.get((int(sec_a[i]), str(pool_a[i]), int(date_a[i])))
        top_arr[i] = (
            ", ".join(x for x in lst if x is not None) if lst else ""
        )
    return top_arr


def _rolling_pillar_pcts(
    agg: pd.DataFrame,
    pillars: list[tuple[str, str]],
) -> pd.DataFrame:
    """Host-boundary trailing percentiles: per (sec_type, pool) group,
    each pillar's value read against its own trailing PCT_WINDOW
    pool-date rows — x_pct(t) = share of non-null window values ≤ x(t),
    requiring MIN_PCT_OBS non-null observations.

    ``pillars`` maps pillar column -> output pct column (concentration
    -> concentration_pct, flow_change -> flow_pct, ...).

    Rolling rank is a gather/index algorithm with no cuDF-native form,
    so this is the sanctioned host core (agg is the SMALL per-(pool,
    date) frame; every pool-pillar is one sliding_window_view compare —
    microseconds each). agg must be sorted by (sec_type, pool_code,
    date); the pct columns are re-attached via assign.
    """
    n = len(agg)
    sec_h = host_array(agg["sec_type"].to_numpy()).astype("int64")
    pool_h = host_array(agg["pool_code"].to_numpy())
    val_h = {
        src: np.asarray(host_array(agg[src].to_numpy()), dtype="float64")
        for src, _dst in pillars
    }
    out: dict[str, np.ndarray] = {
        dst: np.full(n, np.nan) for _src, dst in pillars
    }

    # Group boundaries over the contiguous (sec_type, pool_code) blocks.
    starts: list[int] = [0]
    for i in range(1, n):
        if sec_h[i] != sec_h[i - 1] or pool_h[i] != pool_h[i - 1]:
            starts.append(i)
    starts.append(n)

    pad = np.full(PCT_WINDOW - 1, np.nan)
    for k in range(len(starts) - 1):
        i0, i1 = starts[k], starts[k + 1]
        for src, dst in pillars:
            v = val_h[src][i0:i1]
            t_len = v.shape[0]
            if t_len == 0:
                continue
            # (t_len, PCT_WINDOW) trailing windows (NaN-padded head).
            windows = np.lib.stride_tricks.sliding_window_view(
                np.concatenate([pad, v]), PCT_WINDOW)
            valid = ~np.isnan(windows)
            cur = v[:, None]
            n_valid = valid.sum(axis=1)
            n_le = ((windows <= cur) & valid).sum(axis=1)
            ok = (~np.isnan(v)) & (n_valid >= MIN_PCT_OBS)
            res = n_le / np.maximum(n_valid, 1)
            out[dst][i0:i1] = np.where(ok, res, np.nan)

    return agg.assign(**out)


def _corr_via_rolling_sums(slim: pd.DataFrame) -> pd.DataFrame:
    """Trailing-CORR_WINDOW Pearson correlation per (sec_type, pool,
    stock) between the stock's return (x) and the pool's leave-one-out
    equal-weight return (y) — whole-frame GPU algebra: five grouped
    rolling sums + one rolling count (cuDF-native), then the
    Pearson identity per row.

    x/y are pre-masked to their JOINT validity, so all six rolling
    results share one NaN pattern and the pair counts only days where
    both legs are defined. Returns slim with a `corr` column.
    """
    keys3 = ["sec_type", "pool_code", "stock_code"]
    joint = slim["x"].notna() & slim["y"].notna()
    slim["x"] = slim["x"].where(joint)
    slim["y"] = slim["y"].where(joint)
    slim["xy"] = slim["x"] * slim["y"]
    slim["x2"] = slim["x"] * slim["x"]
    slim["y2"] = slim["y"] * slim["y"]

    def _roll(col: str, agg: str) -> pd.Series:
        return grouped_rolling_agg(
            slim, keys3, col, CORR_WINDOW,
            min_periods=1, agg=agg, sort=False,
        )

    n = _roll("x", "count")
    sx = _roll("x", "sum")
    sy = _roll("y", "sum")
    sxy = _roll("xy", "sum")
    sxx = _roll("x2", "sum")
    syy = _roll("y2", "sum")

    num = n * sxy - sx * sy
    d1 = np.maximum(n * sxx - sx * sx, 0.0)
    d2 = np.maximum(n * syy - sy * sy, 0.0)
    den = np.sqrt(d1 * d2)
    # Undefined (zero-variance) windows -> NaN via the where-mask; a
    # np.maximum(den, 1.0) guard here would clamp EVERY small-variance
    # denominator (returns are ~1e-2 scale) and return the raw numerator.
    corr = num / den.where(den > 0.0)
    ok = (n >= MIN_CORR_OBS) & (den > 0.0)
    # |corr| > 1 + eps is num/den rounding garbage on a near-degenerate
    # window (the rolling_corr kernel's guard) — NaN, not a value.
    ok = ok & (np.abs(corr) <= 1.0 + 1e-9)
    slim["corr"] = np.where(ok, corr, np.nan)
    return slim.drop(columns=["xy", "x2", "y2"])


def _compute_crowdedness(
    members: pd.DataFrame,
    amt: pd.DataFrame,
    ret: pd.DataFrame,
    names: dict[str, str],
) -> pd.DataFrame:
    """Chunked driver: membership x amounts x returns -> crowdedness
    rows, processed in WHOLE-POOL batches under CHUNK_PAIR_BUDGET
    membership pairs (an unchunked full-universe pass exhausts GPU
    VRAM). Every pillar is per-(sec_type, pool), and a chunk never
    splits a pool, so the concatenated result is identical to the
    unchunked pass.

    Returns the output frame [sec_type, code, date, crowdedness_score,
    concentration, flow_change, comovement, valuation, *_pct,
    top_stocks] with storage rounding applied.
    """
    empty = pd.DataFrame(columns=OUT_COLUMNS)
    if members.empty or amt.empty:
        return empty

    members = members.sort_values(
        ["sec_type", "pool_code"], kind="mergesort").reset_index(drop=True)
    # Per-pool pair counts over the sorted membership — the cumulative
    # pair count IS the row offset of each pool's block end, so chunk
    # boundaries in pair-count space map to contiguous .iloc row ranges
    # (the per-pool counts frame is tiny — the host walk is trivial).
    sizes = (
        members.groupby(["sec_type", "pool_code"], sort=False)
        .size().rename("n_pairs").reset_index()
    )
    n_h = np.asarray(
        host_array(sizes["n_pairs"].to_numpy()), dtype="int64")
    n_total = int(n_h.sum())

    cuts: list[tuple[int, int]] = []
    lo = 0
    acc = 0
    for i in range(len(n_h)):
        acc += int(n_h[i])
        if acc >= CHUNK_PAIR_BUDGET and lo + acc < n_total:
            cuts.append((lo, lo + acc))
            lo += acc
            acc = 0
    if lo < n_total:
        cuts.append((lo, n_total))

    logger.info("        %d pool(s) / %d pair(s) in %d chunk(s) "
                "(<= %d pairs each).",
                len(n_h), n_total, len(cuts), CHUNK_PAIR_BUDGET)
    parts: list[pd.DataFrame] = []
    for k, (a, b) in enumerate(cuts, start=1):
        batch = members.iloc[a:b]
        part = _compute_pool_batch(batch, amt, ret, names)
        logger.info("        chunk %d/%d: pairs %d..%d (%d pairs) "
                    "-> %s row(s).",
                    k, len(cuts), a, b, b - a, f"{len(part):,}")
        if not part.empty:
            parts.append(part)
        del batch, part
        gc.collect()
    if not parts:
        return empty
    return pd.concat(parts, ignore_index=True)


def _compute_pool_batch(
    members: pd.DataFrame,
    amt: pd.DataFrame,
    ret: pd.DataFrame,
    names: dict[str, str],
) -> pd.DataFrame:
    """ONE whole-pool chunk: membership x amounts x returns -> the
    crowdedness rows for those pools only (called exclusively by
    _compute_crowdedness — never on the unchunked universe).
    """
    empty = pd.DataFrame(columns=OUT_COLUMNS)
    if members.empty or amt.empty:
        return empty

    m = members.merge(amt, on="stock_code", how="inner")
    if m.empty:
        return empty
    m = m.sort_values(
        ["sec_type", "pool_code", "stock_code", "date"], kind="mergesort"
    ).reset_index(drop=True)

    # PILLAR bases: the stable ranking mean (60 active days) and the
    # turnover-pillar mean (5 active days) — both computed on the
    # (pool, stock, date)-sorted frame so the grouped rolling sees
    # correct temporal order.
    diff_keys = ["sec_type", "pool_code", "stock_code"]
    m["amt_ma60"] = grouped_rolling_agg(
        m, diff_keys, "amt", HOT_MA_WINDOW,
        min_periods=HOT_MA_MIN_OBS, agg="mean", sort=False,
    )
    m["amt_ma5"] = grouped_rolling_agg(
        m, diff_keys, "amt", PILLAR_MA_WINDOW,
        min_periods=1, agg="mean", sort=False,
    )
    # FLOW basis: the gross INCREASE of the 5-day mean amount (sustained
    # multi-day inflow). First observation contributes 0.
    delta_ma5 = m.groupby(diff_keys, sort=False)["amt_ma5"].diff()
    m["pos_delta"] = np.maximum(delta_ma5.fillna(0.0), 0.0)

    sizes = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)["amt"]
        .size().rename("active_n").reset_index()
    )
    sizes["hot_n"] = np.maximum(
        np.ceil(sizes["active_n"].astype("float64") * HOT_POOL_FRACTION),
        float(MIN_HOT_N),
    ).astype("int64")
    m = m.merge(sizes, on=["sec_type", "pool_code", "date"], how="left")

    # Turnover-concentration evidence: day-amount top cuts. cumcount
    # over an amount-desc stable sort = the amount rank (hypes
    # _rank_top5 idiom).
    m = m.sort_values(
        ["sec_type", "pool_code", "date", "amt"],
        ascending=[True, True, True, False], kind="mergesort",
    ).reset_index(drop=True)
    m["day_rank"] = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        .cumcount() + 1
    ).astype("int64")
    for frac, col in TOP_CUTS:
        k = np.maximum(
            np.ceil(m["active_n"].astype("float64") * frac), 1.0,
        ).astype("int64")
        m[col.replace("_share", "_amt")] = m["amt"].where(
            m["day_rank"] <= k)
    m = m.drop(columns=["day_rank"])
    m["amt2"] = m["amt"] * m["amt"]

    # STABLE hot set: top HOT_POOL_FRACTION (10%) of the day's active
    # pool by amt_ma60, floored at MIN_HOT_N (5). NaN ma60 (< 20 active
    # days) sorts last AND is masked out — a young pool-day with no
    # rankable member has an EMPTY hot set (hot_n_defined == 0) instead
    # of a meaningless NaN-tail "hot" list. The size is per (sec_type,
    # pool, date): ceil(10% of the active count), min 5.
    m = m.sort_values(
        ["sec_type", "pool_code", "date", "amt_ma60"],
        ascending=[True, True, True, False], na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)
    m["hot_rank"] = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        .cumcount() + 1
    ).astype("int64")
    hot_mask = (m["hot_rank"] <= m["hot_n"]) & m["amt_ma60"].notna()

    # The stable hot set's share of the pool's 5-DAY-MEAN amount (the
    # PILLAR basis) — when money piles into the established hot names
    # this spikes; one-day blips are averaged away.
    m["hot_amt"] = m["amt_ma5"].where(hot_mask)
    m["hot_pos_delta"] = m["pos_delta"].where(hot_mask)

    g = m.groupby(["sec_type", "pool_code", "date"], sort=False)
    agg = pd.concat([
        g["amt_ma5"].sum().rename("pool_amt"),
        g["amt"].sum().rename("pool_day_amt"),
        g["amt2"].sum().rename("pool_amt2"),
        g["hot_amt"].sum().rename("hot_amt"),
        g["hot_amt"].count().rename("hot_n_defined"),
        g["pos_delta"].sum().rename("pool_pos_delta"),
        g["hot_pos_delta"].sum().rename("hot_pos_delta"),
        g["amt"].size().rename("active_n"),
    ] + [
        g[col.replace("_share", "_amt")].sum().rename(col)
        for _frac, col in TOP_CUTS
    ], axis=1).reset_index()

    pool_amt = agg["pool_amt"]
    agg["concentration"] = agg["hot_amt"] / pool_amt.where(pool_amt > 0.0)
    agg["effective_n"] = (
        agg["pool_day_amt"] * agg["pool_day_amt"]
        / agg["pool_amt2"].where(agg["pool_amt2"] > 0.0)
    )
    for _frac, col in TOP_CUTS:
        agg[col] = (agg[col]
                    / agg["pool_day_amt"].where(agg["pool_day_amt"] > 0.0))
    pos_denom = agg["pool_pos_delta"].where(agg["pool_pos_delta"] > 0.0)
    agg["flow_change"] = agg["hot_pos_delta"] / pos_denom

    # Defined-row gate for the RAW turnover pillars (small pools, zero
    # gross-increase days, and days where NO member has a defined ma60 —
    # an empty hot set would make concentration degenerate) — the later
    # pillars only join rows that survive here.
    agg = agg[
        (agg["active_n"] >= MIN_ACTIVE_POOL)
        & (agg["hot_n_defined"] > 0)
        & agg["concentration"].notna()
        & agg["flow_change"].notna()
        & agg["effective_n"].notna()
    ].reset_index(drop=True)
    if agg.empty:
        return empty

    # Hot-set rows (evidence + the co-movement join) — extracted BEFORE
    # the pe re-sort scrambles the frame order; rows arrive sorted by
    # (sec_type, pool, date, hot_rank).
    hot_rows = m.loc[
        hot_mask,
        ["sec_type", "pool_code", "date", "hot_rank", "hot_n",
         "stock_code"],
    ].reset_index(drop=True)

    # ---- v2 pillar inputs: returns + pe onto the row frame ----------
    if ret.empty:
        m["ret"] = np.nan
        m["pe"] = np.nan
    else:
        m = m.merge(
            ret, on=["stock_code", "date"], how="left")

    # Pool equal-weight return + valid count per (pool, date); the
    # leave-one-out return removes the stock's own day from its
    # correlation leg (self-correlation would otherwise inflate the
    # co-movement of small pools).
    m["ret_valid_f"] = m["ret"].notna().astype("float64")
    g2 = m.groupby(["sec_type", "pool_code", "date"], sort=False)
    rstats = pd.concat([
        g2["ret"].sum().rename("ret_sum"),
        g2["ret_valid_f"].sum().rename("ret_n"),
    ], axis=1).reset_index()
    m = m.drop(columns=["ret_valid_f"])
    m = m.merge(rstats, on=["sec_type", "pool_code", "date"], how="left")
    r_n = m["ret_n"]
    r_mean = m["ret_sum"] / r_n.where(r_n > 0.0)
    m["pool_ret_loo"] = np.where(
        r_n > 1.0,
        (r_n * r_mean - m["ret"].fillna(0.0)) / np.maximum(r_n - 1.0, 1.0),
        np.nan,
    )
    # A stock with no return of its own has no defined LOO leg either.
    m["pool_ret_loo"] = m["pool_ret_loo"].where(m["ret"].notna())

    # ---- valuation: pe cross-sectional percentile within the day's ---
    # active pool, averaged over the hot set. Sort-based rank (cuDF
    # groupby rank has no fast path): NaN pe sorts last inside each
    # (pool, date) block, so valid rows occupy positions 1..n_pe. The
    # position rides the frame as a COLUMN through the pe_n merge — a
    # bare Series assigned after the merge would rely on index
    # realignment.
    m = m.sort_values(
        ["sec_type", "pool_code", "date", "pe"],
        na_position="last", kind="mergesort",
    ).reset_index(drop=True)
    m["pe_pos"] = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        .cumcount() + 1
    ).astype("float64")
    m["pe_valid_f"] = m["pe"].notna().astype("float64")
    n_pe = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        ["pe_valid_f"].sum().reset_index().rename(
            columns={"pe_valid_f": "pe_n"})
    )
    m = m.merge(
        n_pe, on=["sec_type", "pool_code", "date"], how="left")
    m = m.drop(columns=["pe_valid_f"])
    m["pe_rank"] = m["pe_pos"] / m["pe_n"].where(m["pe_n"] > 0.0)
    m["pe_rank"] = m["pe_rank"].where(m["pe"].notna())
    m["hot_pe_rank"] = m["pe_rank"].where(m["hot_rank"] <= m["hot_n"])
    valuation = (
        m.groupby(["sec_type", "pool_code", "date"], sort=False)
        ["hot_pe_rank"].mean().rename("valuation").reset_index()
    )
    m = m.drop(
        columns=["pe_rank", "hot_pe_rank", "pe", "pe_n", "pe_pos"])

    # ---- co-movement: corr(stock return, LOO pool return) over the --
    # trailing CORR_WINDOW active days, only for EVER-HOT (pool, stock)
    # pairs (any other pair never enters a hot-set mean) — then the
    # per-(pool, date) mean over that date's hot members.
    pair_keys = ["sec_type", "pool_code", "stock_code"]
    ever_hot = hot_rows[pair_keys].drop_duplicates()
    slim = m[pair_keys + ["date", "ret", "pool_ret_loo"]].merge(
        ever_hot, on=pair_keys, how="inner")
    m = m.drop(columns=["ret", "ret_sum", "ret_n", "pool_ret_loo"])
    if slim.empty:
        agg["comovement"] = np.nan
    else:
        slim = slim.rename(
            columns={"ret": "x", "pool_ret_loo": "y"})
        slim = slim.sort_values(
            pair_keys + ["date"], kind="mergesort").reset_index(drop=True)
        slim = _corr_via_rolling_sums(slim)
        hot_corr = hot_rows.merge(
            slim[pair_keys + ["date", "corr"]],
            on=pair_keys + ["date"], how="inner")
        comovement = (
            hot_corr.groupby(
                ["sec_type", "pool_code", "date"], sort=False)
            ["corr"].mean().rename("comovement").reset_index()
        )
        agg = agg.merge(
            comovement, on=["sec_type", "pool_code", "date"],
            how="left")
    del slim
    agg = agg.merge(
        valuation, on=["sec_type", "pool_code", "date"], how="left")

    # ---- percentile normalization + score blend ----------------------
    agg = agg.sort_values(
        ["sec_type", "pool_code", "date"], kind="mergesort"
    ).reset_index(drop=True)
    agg = _rolling_pillar_pcts(
        agg,
        [("concentration", "concentration_pct"),
         ("flow_change", "flow_pct"),
         ("comovement", "comovement_pct"),
         ("valuation", "valuation_pct")])

    ok = (
        agg["active_n"].ge(MIN_ACTIVE_POOL)
        & agg["concentration"].notna()
        & agg["flow_change"].notna()
        & agg["concentration_pct"].notna()
        & agg["flow_pct"].notna()
    )
    agg = agg[ok].reset_index(drop=True)
    if agg.empty:
        return empty

    # Blend from unrounded percentiles over the DEFINED pillars (the
    # two turnover pillars are required; comovement/valuation drop out
    # with their weights); clip guards the 0..1 contract against fp
    # noise; round is the storage convention (NUMERIC(10,4)).
    pillars = [
        ("concentration_pct", W_CONCENTRATION),
        ("flow_pct", W_FLOW),
        ("comovement_pct", W_COMOVEMENT),
        ("valuation_pct", W_VALUATION),
    ]
    num = agg["concentration_pct"] * 0.0
    den = agg["concentration_pct"] * 0.0
    for col, w in pillars:
        v = agg[col]
        num = num + v.fillna(0.0) * w
        den = den + v.notna().astype("float64") * w
    score = num / den.where(den > 0.0)

    agg["concentration"] = np.round(_clip01(agg["concentration"]), 4)
    agg["flow_change"] = np.round(_clip01(agg["flow_change"]), 4)
    agg["comovement"] = np.round(agg["comovement"], 4)
    agg["valuation"] = np.round(_clip01(agg["valuation"]), 4)
    for _frac, col in TOP_CUTS:
        agg[col] = np.round(_clip01(agg[col]), 4)
    # effective_n is a COUNT (1..active_n) — no clip.
    agg["effective_n"] = np.round(agg["effective_n"], 4)
    for col, _w in pillars:
        agg[col] = np.round(_clip01(agg[col]), 4)
    agg["crowdedness_score"] = np.round(_clip01(score), 4)

    # Hot-set evidence (host boundary — see _build_top_stocks).
    agg = agg.assign(
        top_stocks=_build_top_stocks(hot_rows, agg, names))

    agg = agg.rename(columns={"pool_code": "code"})
    agg["sec_type"] = agg["sec_type"].astype("int64")
    return agg[OUT_COLUMNS]


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

class MarketCrowdednessAnalysis(DataAnalysis):
    """``python -m analysis.market_crowdedness`` — market crowdedness."""

    title = ("ANALYZE MARKET CROWDEDNESS — per-index/industry "
             "crowdedness curve")
    component = "market_crowdedness"
    # The page toggle exposes index + industry only; the pipeline covers
    # the same two sides (full active classified universe on both).
    sec_types = ("index", "industry")

    def add_arguments(self, parser) -> None:
        self.add_sec_type_arg(parser)
        self.add_force_arg(parser)

    def apply_args(self) -> None:
        pass

    def header_fields(self) -> dict:
        return {
            "tables": "analysis.market_crowdedness",
            "sec_types": ", ".join(self.resolve_sec_types()),
            "mode": "FORCE (delete scoped sec_types + full recompute)"
                    if self.args.force
                    else "incremental (missing dates only)",
        }

    async def run(self) -> None:
        t0 = time.time()
        scoped = self.resolve_sec_types()
        sec_type_codes = [SEC_TYPE_CODE[st] for st in scoped]
        force = bool(self.args.force)

        logger.info("\n" + "=" * 78)
        logger.info("  MARKET CROWDEDNESS v2  (%s mode, sec_types: %s)",
                    "FORCE" if force else "incremental", ", ".join(scoped))
        logger.info("=" * 78)

        # ---- [mc1/5] incremental scope: which dates are missing -------
        lo = datetime.date(1990, 1, 1)
        hi = datetime.date.today()
        target_dates: set | None = None
        if not force:
            target_dates = set()
            for st in scoped:
                missing = await self.find_missing_dates(
                    TABLE, ["stats.stock_identity"],
                    sec_type=SEC_TYPE_CODE[st],
                )
                target_dates |= missing
            if not target_dates:
                logger.info("[mc1/5] no missing dates — nothing to do.")
                await self.upsert_identity(
                    ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
                return
            # Clamp to the amounts table's actual range: stock_identity
            # dates back to 2003 but stock_liquidity_margin starts ~2020,
            # and dates with no amount data can NEVER gain a row — without
            # the clamp every incremental run would recompute the full
            # window chasing unfillable pre-history dates. The FIRST
            # amounts date is excluded too: it has no previous traded day,
            # so its gross Δamt is zero by construction and no row can be
            # defined on it.
            amt_range = await self.conn.fetchrow(
                "SELECT MIN(date) AS lo, MAX(date) AS hi "
                "FROM stats.stock_liquidity_margin WHERE trading_amount > 0"
            )
            if amt_range and amt_range["lo"] is not None:
                target_dates = {
                    d for d in target_dates
                    if amt_range["lo"] < d <= amt_range["hi"]
                }
                # Percentile warm-up: a row needs >= MIN_PCT_OBS (60)
                # defined pillar days of trailing history, so dates
                # before the 60th trading day after the first amounts
                # date can NEVER gain a row (same rationale as the
                # range clamp — without this edge every incremental run
                # chases the unfillable warm-up window). 120 calendar
                # days safely bounds 60 trading days incl. holiday
                # clustering; fillable dates later than a pool's own
                # inception are all past this edge.
                warmup_edge = (amt_range["lo"]
                               + datetime.timedelta(days=120))
                target_dates = {
                    d for d in target_dates if d >= warmup_edge
                }
            if not target_dates:
                logger.info(
                    "[mc1/5] missing dates all outside the amounts range "
                    "— nothing to do.")
                await self.upsert_identity(
                    ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
                return
            lo = min(target_dates) - datetime.timedelta(
                days=INC_LOOKBACK_DAYS)
            hi = max(target_dates)
            logger.info("[mc1/5] %d missing date(s): %s .. %s "
                        "(+%d-day lookback)",
                        len(target_dates), min(target_dates), hi,
                        INC_LOOKBACK_DAYS)
        else:
            logger.info("[mc1/5] FORCE mode — full history recompute.")

        # ---- [mc2/5] membership + names ------------------------------
        members = await _fetch_membership(self.conn, scoped)
        if members.empty:
            logger.info("[mc2/5] no membership rows — nothing to compute.")
            return
        stock_codes = host_unique(members["stock_code"])
        names = await _fetch_stock_names(self.conn, stock_codes)
        n_pools = len(host_unique(members["pool_code"]))
        logger.info("[mc2/5] %d pool(s), %d member stock(s).",
                    n_pools, len(stock_codes))

        # ---- [mc3/5] amounts + returns + compute ---------------------
        amt = await _fetch_amounts(self.conn, stock_codes, lo, hi)
        ret = await _fetch_returns(self.conn, stock_codes, lo, hi)
        logger.info("[mc3/5] %s amount row(s), %s return row(s) in "
                    "window — computing.",
                    f"{len(amt):,}", f"{len(ret):,}")
        out = _compute_crowdedness(members, amt, ret, names)
        del members, amt, ret
        gc.collect()
        logger.info("        -> %s crowdedness row(s) (%.1fs)",
                    f"{len(out):,}", time.time() - t0)
        if out.empty:
            logger.info("        no defined rows — nothing to write.")
            return

        # ---- [mc4/5] write --------------------------------------------
        if force:
            await self.delete_sec_type_rows([TABLE], sec_type_codes)
            # copy_frame_chunked_async defaults keep the bulk-write
            # discipline engaged: partition-key-aligned chunks, one
            # COMMIT per chunk, the 0.5 s inter-chunk cool-down (10 s
            # once the write reaches 20+ chunks) and the replica-lag
            # wait (chunk_pause_s=None would disable them — never pass
            # it here).
            n = await copy_frame_chunked_async(
                self.conn, TABLE, out,
                columns=OUT_COLUMNS,
                numeric_cols=OUT_FLOAT_COLUMNS,
                round_to=None,  # pre-rounded in _compute_crowdedness
                date_cols=["date"],
                label="[mc4/5]",
            )
            await analyze_tables_async(self.conn, [TABLE])
        else:
            kept: list[pd.DataFrame] = []
            for code in sec_type_codes:
                part = out[out["sec_type"] == code]
                if part.empty:
                    continue
                # Post-compute safety filter: keep only rows whose date
                # is still missing for this sec_type.
                rows_seen = await self.conn.fetch(
                    f"SELECT DISTINCT date FROM {TABLE} WHERE sec_type = $1",
                    code,
                )
                present = {r["date"] for r in rows_seen if r["date"]}
                if present:
                    miss_arr = np.array(
                        sorted(present), dtype="datetime64[D]"
                    ).astype("datetime64[us]")
                    part = part[~part["date"].isin(miss_arr)]
                if not part.empty:
                    kept.append(part)
            to_write = (pd.concat(kept, ignore_index=True)
                        if kept else out.iloc[0:0])
            rows = sanitize_for_db_insert(
                to_write, numeric_cols=OUT_FLOAT_COLUMNS,
                round_to=None, date_cols=["date"],
            )
            n = 0
            if rows:
                n = await bulk_upsert_async(
                    self.conn, TABLE, rows,
                    key_columns=["code", "sec_type", "date"],
                )
        logger.info("[mc4/5] wrote %s row(s).", f"{n:,}")

        # ---- [mc5/5] identity + sanity summary ------------------------
        await self.upsert_identity(
            ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
        summary = await self.conn.fetch("""
            SELECT sec_type, COUNT(*) AS n_rows,
                   COUNT(DISTINCT code) AS n_codes,
                   MIN(date) AS first_date, MAX(date) AS last_date,
                   ROUND(AVG(crowdedness_score), 4) AS avg_score,
                   ROUND(MAX(crowdedness_score), 4) AS max_score,
                   COUNT(comovement) AS n_comovement,
                   COUNT(valuation) AS n_valuation,
                   ROUND(AVG(comovement), 4) AS avg_comovement,
                   ROUND(AVG(valuation), 4) AS avg_valuation,
                   ROUND(AVG(top1_share), 4) AS avg_top1,
                   ROUND(AVG(top10_share), 4) AS avg_top10,
                   ROUND(AVG(effective_n), 2) AS avg_effective_n
            FROM analysis.market_crowdedness
            GROUP BY sec_type ORDER BY sec_type
        """)
        logger.info("\n      Summary by sec_type:")
        for r in summary:
            logger.info("        sec_type=%s: %s rows . %s codes . "
                        "%s -> %s . avg=%s max=%s . comovement(%s rows) "
                        "avg=%s . valuation(%s rows) avg=%s . "
                        "top1=%s top10=%s effN=%s",
                        r["sec_type"], f"{r['n_rows']:,}", r["n_codes"],
                        r["first_date"], r["last_date"],
                        r["avg_score"], r["max_score"],
                        f"{r['n_comovement']:,}", r["avg_comovement"],
                        f"{r['n_valuation']:,}", r["avg_valuation"],
                        r["avg_top1"], r["avg_top10"], r["avg_effective_n"])
        sample = await self.conn.fetch("""
            SELECT code, sec_type, date, crowdedness_score,
                   concentration, flow_change, comovement, valuation,
                   top1_share, top5_share, top10_share, effective_n,
                   concentration_pct, flow_pct, comovement_pct,
                   valuation_pct, top_stocks
            FROM analysis.market_crowdedness
            WHERE date = (SELECT MAX(date) FROM analysis.market_crowdedness)
            ORDER BY crowdedness_score DESC
            LIMIT 5
        """)
        logger.info("      Latest-date top-5 crowdedness sample:")
        for r in sample:
            logger.info("        %s (sec_type=%s) %s: score=%s "
                        "conc=%s/%s flow=%s/%s cmv=%s/%s val=%s/%s "
                        "top1/5/10=%s/%s/%s effN=%s | %s",
                        r["code"], r["sec_type"], r["date"],
                        r["crowdedness_score"],
                        r["concentration"], r["concentration_pct"],
                        r["flow_change"], r["flow_pct"],
                        r["comovement"], r["comovement_pct"],
                        r["valuation"], r["valuation_pct"],
                        r["top1_share"], r["top5_share"],
                        r["top10_share"], r["effective_n"],
                        (r["top_stocks"][:50] + "…")
                        if r["top_stocks"] and len(r["top_stocks"]) > 50
                        else r["top_stocks"])

        logger.info("\n  market_crowdedness wall time: %.1fs",
                    time.time() - t0)


if __name__ == "__main__":
    MarketCrowdednessAnalysis().execute()
