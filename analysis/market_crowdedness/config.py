"""analysis.market_crowdedness.config — module constants.

The four output tables, the metric knobs (windows / gates / weights /
chunking), the output column contracts and the hot-tech metric column
derivation. Pure literals — nothing here imports pandas, so any module
may import config before the cudf.pandas hook installs. The metric
definition itself lives in the ``__main__.py`` docstring.
"""

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.market_crowdedness"
# Year-pinned variants + the frozen annual rosters (2026-11-14).
YEAR_TABLE = "analysis.market_crowdedness_year"
SETS_TABLE = "analysis.market_crowdedness_hot_sets"
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
    "obs). crowdedness_score_ma5 = the 5-pool-date-row moving average "
    "of the score — the ONLINE smoothing layer (the raw score's "
    "day-over-day movement is dominated by single-day pillar noise, "
    "fluctuation study 2026-11-14); the raw score stays as evidence. "
    "HOT SET = top 10% of the day's active pool by the "
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
    "evidence. YEAR-PINNED VARIANTS (2026-11-14, "
    "analysis.market_crowdedness_year + _hot_sets): per year "
    "(hot_year = the calendar year), the year-average hot set (top "
    "max(ceil(0.1*mean active_n over the year), 5) members by mean "
    "60-active-day amount over the year's own trading days) is held "
    "FIXED across the pool's entire date span — a member contributes "
    "only on dates it actually traded (not-yet-listed / "
    "already-exited naturally absent). The open year re-derives "
    "year-to-date nightly; closed rosters freeze into hot_sets and "
    "are read back on incremental runs. Same pillars / gates / "
    "percentile normalization / blend as the rolling pass. "
    "HOT-TECH AGGREGATES (2026-11-14, "
    "analysis.market_crowdedness_hot_tech): per (code, sec_type, "
    "date), the typical technical profile of the day's rolling hot "
    "set — equal-weight mean + median over the hot members of "
    "ma5_ratio, px_vs_ma20, px_vs_ma60, macd_hist, rsi14 (SMA-"
    "smoothed Cutler form), roc20, amt_vs_ma20 (the per-stock 放量 "
    "gauge) and the day's return, each aggregate also stored as its "
    "percentile vs the pool's own trailing-250-day history (>= 60 "
    "obs; NULL during warm-up — percentiles are NOT required for a "
    "row, this is a raw-evidence table). Rows exist only where the "
    "active pool >= 10 and the hot set is non-empty. "
    "Built by analysis.market_crowdedness (incremental by "
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

# ONLINE smoothing layer (2026-11-14): the score's day-over-day
# movement is dominated by single-day pillar noise (the fluctuation
# study) — the ma5 of the score is the online read; the raw score
# stays as evidence.
SCORE_MA_WINDOW = 5

# Calendar-day buffer so an incremental window re-derives everything a
# target date needs: 250 trading rows ≈ 363 calendar days for the
# percentile history + 60 active rows ≈ 87 days for the corr warm-up +
# the Δamt previous-traded-day + slack.
INC_LOOKBACK_DAYS = 440

OUT_COLUMNS: list[str] = [
    "sec_type", "code", "date", "crowdedness_score",
    "crowdedness_score_ma5",
    "concentration", "flow_change", "comovement", "valuation",
    "top1_share", "top5_share", "top10_share", "effective_n",
    "concentration_pct", "flow_pct", "comovement_pct", "valuation_pct",
    "top_stocks",
]
OUT_FLOAT_COLUMNS: list[str] = [
    "crowdedness_score", "crowdedness_score_ma5", "concentration",
    "flow_change", "comovement",
    "valuation", "top1_share", "top5_share", "top10_share",
    "effective_n", "concentration_pct", "flow_pct", "comovement_pct",
    "valuation_pct",
]

# Year-pinned outputs (hot_year replaces the rolling table's place in
# the key; the metric columns are the same contract).
PINNED_OUT_COLUMNS: list[str] = [
    "sec_type", "code", "hot_year", "date", *OUT_COLUMNS[3:],
]
SETS_OUT_COLUMNS: list[str] = [
    "sec_type", "code", "hot_year", "hot_rank", "stock_code",
    "stock_name",
]

# Hot-stock tech aggregates (2026-11-14 — analysis.market_crowdedness_
# hot_tech): per-(pool, date) typical technical data over the day's
# ROLLING hot set — the same mask as the crowdedness score, aggregated
# on a slim column-subset frame (m is never touched, so the rolling/
# pinned outputs stay bit-identical, A/B gate).
HOT_TECH_TABLE = "analysis.market_crowdedness_hot_tech"

# The aggregated per-stock metrics (decimal fractions unless stated):
# trend stretch (ma5_ratio precomputed + px_vs_ma20/60), momentum
# (macd_hist precomputed, rsi14, roc20), the per-stock 放量 gauge
# (amt_vs_ma20) and the day's return. The return series everywhere =
# pct_change/100, fallback close/prev_close - 1 — the comovement
# pillar's series.
HOT_TECH_METRICS: tuple[str, ...] = (
    "ma5_ratio", "px_vs_ma20", "px_vs_ma60", "macd_hist",
    "rsi14", "roc20", "amt_vs_ma20", "ret",
)


def _hot_tech_metric_columns() -> list[str]:
    """Per metric: mean, its own-history percentile, median, its
    percentile — the raw+pct adjacency the DDL groups by."""
    cols: list[str] = []
    for m in HOT_TECH_METRICS:
        for stat in ("mean", "median"):
            cols.append(f"{stat}_{m}")
            cols.append(f"{stat}_{m}_pct")
    return cols


HOT_TECH_OUT_COLUMNS: list[str] = [
    "sec_type", "code", "date", "hot_n", *_hot_tech_metric_columns(),
]
# Batch frames carry pool_code — the rename to code happens in the
# concat driver (same pattern as the pinned variants).
HOT_TECH_BATCH_COLUMNS: list[str] = [
    "sec_type", "pool_code", "date", "hot_n", *_hot_tech_metric_columns(),
]
HOT_TECH_FLOAT_COLUMNS: list[str] = HOT_TECH_OUT_COLUMNS[4:]
# (stat column -> its own-history pct column) pairs for the shared
# _rolling_pillar_pcts host core (PCT_WINDOW / MIN_PCT_OBS constants).
HOT_TECH_PCT_PAIRS: list[tuple[str, str]] = [
    (f"{stat}_{m}", f"{stat}_{m}_pct")
    for m in HOT_TECH_METRICS
    for stat in ("mean", "median")
]

# Hot-tech gauge windows (active-day rows): the amount-expansion MA
# (full window required — a partial MA is not a participation
# baseline), the ROC lookback and the RSI smoothing window.
HOT_TECH_AMT_MA_WINDOW = 20
HOT_TECH_ROC_WINDOW = 20
HOT_TECH_RSI_WINDOW = 14

IDENTITY_TABLE = {
    "index": "stats.index_identity",
    "industry": "stats.industry_identity",
}
