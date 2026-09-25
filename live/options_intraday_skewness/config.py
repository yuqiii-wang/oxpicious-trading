"""Configuration constants for live.options_intraday_skewness.

Intraday OI-weighted moneyness skewness — the 5-minute sibling of
analysis.options_skewness_stats (skew_type = 'oi_moneyness').

  live.options_intraday_skewness (COMPUTED SERIES — this pipeline writes)
    PK: (underlying_code, date, time, expiry_date, skew_type)
    One row per underlying per 5-min bar per expiry group, plus a MEAN
    row blending all active groups under the sentinel expiry
    MEAN_EXPIRY (DATE '9999-12-31'). skew_price = S(t) * E[M] with
    E[M] = SUM(oi * K / S(t)) / SUM(oi) over IV-valid active contracts
    (OI floored at 1 — the exact semantics of the daily pipeline and of
    the browser chart this series mirrors).

  live.options_intraday_oi (RAW STORE — written by the SSE options
  streamer; read opportunistically by this pipeline via the per-bar OI
  derivation, which reproduces its GENERATED open_interest_est from
  base tables and therefore stays consistent whether or not the date is
  covered by the rolling store).

OI ESTIMATOR (user-decided semantics):
  OI_est(t, contract) = OI_daily(prev trading day) + cum_volume(t, day)
  — the position base carried into the session plus the day's traded
  volume. Upper bound of true OI (closes / rolls are not netted).
  SSE contracts carry oi_base = 0 (no per-contract daily OI source —
  tstyle position returns null); SZSE / CFFEX contracts have no intraday
  bars and use the flat prev-day daily OI.

Storage model (retention + on-demand):
  ROLLING CACHE of the newest RETENTION_DATES trading dates on BOTH
  tables (the pipeline prunes them together, once per new trading day,
  guarded by the PRUNE_IDENTITY_NAME live_identity row). Older dates:
  the OI store stays empty (accepted nulls) while the skewness series
  is recomputed ON DEMAND (``--date D``, API-invoked — the trading
  signals pattern) from the ~90d intraday base tables.

Sources:
  stats.v_options_quote          — prev-day contract snapshot (terms,
                                   strikes, IV validity, OI base)
  stats.options_intraday_5min    — per-contract 5-min volume → cumulative
  stats.etf_intraday_5min        — underlying spot (ETF targets)
  stats.index_intraday_5min      — underlying spot (INDEX targets)
  live.options_intraday_oi       — rolling intraday OI store (informational)
"""
from typing import Final

import datetime as _dt

# Computed series table (this pipeline's output) + the raw OI store
# (streamer-owned; pruned together with the series).
SKEW_TABLE: Final[str] = "live.options_intraday_skewness"
OI_TABLE: Final[str] = "live.options_intraday_oi"

# PK of the series table (ON CONFLICT arbiter of the idempotent upsert).
SKEW_PK: Final[tuple[str, ...]] = (
    "underlying_code", "date", "time", "expiry_date", "skew_type",
)

# Metric vocab written by this pipeline (analysis.options_skewness_stats).
SKEW_TYPE: Final[str] = "oi_moneyness"

# Sentinel expiry of the MEAN row (all active expiry groups blended).
# Deliberately 9998-12-31, NOT date.max: asyncpg encodes 9999-12-31 as
# PG 'infinity', which psycopg-side readers cannot load back.
MEAN_EXPIRY: Final[_dt.date] = _dt.date(9998, 12, 31)

# Strike unit: strikes are stored in 厘 (1/1000 yuan) — same PRICE_SCALE
# as every options consumer in the repo.
PRICE_SCALE: Final[float] = 1000.0

# A (time, expiry) group needs at least this many active contracts before
# its OI-weighted mean moneyness is written (mirrors the browser chart's
# `valid.length < 3` guard).
MIN_GROUP_ROWS: Final[int] = 3

# Retention window in TRADING DATES for BOTH tables (rolling cache).
RETENTION_DATES: Final[int] = 20

# live.live_identity bookkeeping row guarding the once-per-new-trading-day
# prune (plain PK probe — the DELETE date scan must not run per invocation).
PRUNE_IDENTITY_NAME: Final[str] = "options_intraday_skewness_prune"

PIPELINE_NAME: Final[str] = "options_intraday_skewness"

PIPELINE_DESCRIPTION: Final[str] = (
    "Intraday OI-weighted moneyness skewness under the live schema: one "
    "series row per underlying per 5-min bar per expiry group (+ a mean "
    "row under the 9999-12-31 sentinel expiry) in "
    "live.options_intraday_skewness — skew_price = S(t) * E[M], E[M] = "
    "OI-weighted mean moneyness over IV-valid active contracts, OI per "
    "bar estimated as prev-trading-day daily OI + day-cumulative traded "
    "volume (upper bound; SSE base = 0, SZSE/CFFEX flat daily OI). Spot "
    "from the underlying's 5-min table by underlying_target_type. "
    "Rolling cache: newest 20 trading dates on both this table and "
    "live.options_intraday_oi; older dates recomputed on demand via "
    "--date D (API-invoked). Sources: stats.v_options_quote (prev-day "
    "snapshot), stats.options_intraday_5min (volumes), "
    "stats.etf_intraday_5min / stats.index_intraday_5min (spot)."
)

# PG advisory-lock key for single-instance coordination — one process per
# invocation (API on-demand spawns are already deduped by process-id tag;
# the lock catches CLI runs racing a spawn). Arbitrary stable constant,
# distinct from the sec_alloc_live_attribution range (48231100X).
ADVISORY_LOCK_KEY: Final[int] = 482311101
