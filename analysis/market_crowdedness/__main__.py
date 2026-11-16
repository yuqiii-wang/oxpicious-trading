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

YEAR-PINNED VARIANTS (2026-11-14 — analysis.market_crowdedness_year +
analysis.market_crowdedness_hot_sets; the Commons page dropdown pins
the hot set to one YEAR — annual, not quarterly, per the 2026-11-14
follow-up): even the stable ma60 list churns 1-2% of its names daily,
and the score inherits that membership noise. The pinned pass freezes
the basket: for each year y (hot_year = the calendar year, e.g. 2025)
the year's hot set = the year-average ranking — the top
max(ceil(10% of the mean active_n over the year's dates), 5) members
by each member's MEAN amt_ma60 over the year's own trading days
(PinnedHotSetPlanner). That set is then hot for the pool's ENTIRE date
span: a member contributes only on dates it actually traded
(not-yet-listed / already-exited names have no amount rows — the
lifecycle masking falls out of the active-day frame). The open year
re-derives year-to-date nightly; on close its roster freezes into
hot_sets, and closed rosters are read BACK from there on incremental
runs (their year data lies outside the load window). Pillars / gates /
percentile normalization / blend are IDENTICAL to the rolling pass —
every hot-set-independent ingredient (amt_ma5/amt_ma60/pos_delta, the
corr rolling sums, the pe cross-sectional ranks, the pool-level sums)
is computed ONCE per chunk and shared (PinnedCrowdednessEvaluator); a
variant is one re-mask + aggregation. A pool missing a frozen year
(new pool, year roll, a pipeline-down gap) can only be built from full
history, so the run widens to a FULL-window pinned backfill pass
whenever that happens; otherwise incremental runs append window dates
to every frozen variant and fully re-upsert the open year's variant
(its set may have changed under it). The rolling pass's output is
bit-identical with the pinned pass bolted on (A/B gate).

HOT-TECH AGGREGATES (2026-11-14 — analysis.market_crowdedness_hot_
tech): per (pool, date), the typical technical profile of the day's
rolling hot set — equal-weight mean + median over the hot members of
eight per-stock metrics (trend stretch: ma5_ratio precomputed,
px_vs_ma20, px_vs_ma60; momentum: macd_hist precomputed, rsi14
SMA-smoothed Cutler form, roc20; volume: amt_vs_ma20, the per-stock
放量 gauge; and the day's return), each aggregate also read as a
percentile vs the pool's own trailing PCT_WINDOW pool-date history
(the shared _rolling_pillar_pcts host core). The aggregation runs in
the same chunk pass on a SLIM column-subset frame — m is never
touched, so the rolling/pinned outputs stay bit-identical (A/B gate).
Rows exist only where the active pool >= MIN_ACTIVE_POOL and the hot
set is non-empty; the percentiles are NOT required for a row (raw
evidence — *_pct NULL during warm-up).

MODULE LAYOUT (the 2026-11-14 split of the former monolithic
__main__.py — pure code motion, every name unchanged):
  config.py   — the four output tables, the metric knobs (windows /
                gates / weights / chunking) and the output column
                contracts (pure literals, no pandas import)
  fetch.py    — the raw-input SQL reads (sql_views tree:
                analysis/market_crowdedness/fetch/)
  compute/    — metrics (the shared metric cores incl. the sanctioned
                host boundaries) / finalize (the defined-row gate +
                percentile/score blend) / pinned (the year hot-set
                planner + evaluator) / pool_batch (one whole-pool
                chunk) / driver (the CHUNK_PAIR_BUDGET chunked entry)
  pipeline.py — the MarketCrowdednessAnalysis class: the run()
                orchestration + incremental writes (sql_views tree:
                analysis/market_crowdedness/pipeline/)

The :class:`MarketCrowdednessAnalysis` entry class (a
:class:`DataAnalysis` subclass, in pipeline.py) owns the runtime
lifecycle + DB connection lifecycle (opened by amain() before run(),
timed-closed after). Bootstrap-first layout: the runtime setup runs
before this module's imports (and before the pipeline import pulls in
pandas).
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

setup_logging("market_crowdedness")

from analysis.market_crowdedness.pipeline import MarketCrowdednessAnalysis

if __name__ == "__main__":
    MarketCrowdednessAnalysis().execute()
