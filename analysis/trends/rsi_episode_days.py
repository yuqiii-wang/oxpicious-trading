"""The RSI tail-episode STORE step (analysis.trends.rsi_episode_days).

One row per (sec_type, code, date) with at least one in-band window —
written to analysis.trend_episode_days (the trends component's own
table, keyed by its PK (code, sec_type, date)). REPLACES the retired
is_typical_{W}days / typical_ep_size_{W}days columns that rode
analysis.mov_ave_rsi (removed 2026-10-15): the same semantics, owned
by the consolidated trends component instead of the RSI analysis
table, with the episode anatomy imported from analysis.trends.episodes
— never from analysis_signals.

=======================================================================
  SEMANTICS — see analysis.trends.episodes (the shared anatomy)
=======================================================================

Per (sec_type, code, window W): band membership = the day's rank-ratio
position in the code's own full-history RSI rank distribution (25%
tails); every in-band day carries its episode's SIDE-MIRRORED pass
flag (BOTTOM: 1.0 the isolated first-touch episodes — the capitulation;
TOP: 1.0 the re-test episodes — the double top; 0.0 the noisy
population; gap-1 continuation days inherit their pass's flag) and its
episode's total trading-day count (both signal tiers' population
marker + the curve tier's 1/size episode weight).

=======================================================================
  REBUILD SEMANTICS (band membership SHIFTS — wholesale per sec_type)
=======================================================================

Band membership is the day's position in the code's FULL-history rank
distribution, so every new day's RSI can move any day's ratio across
the band edge — the same wholesale-rebuild rationale as
mov_ave_rsi_ranks itself. The store is purged per sec_type (or per
code in --code mode) and CSV-COPY reinserted on every ranks rebuild;
the run hook fires only when the RSI step actually computed.

=======================================================================
  GPU LAYOUT (cudf.pandas)
=======================================================================

Whole-frame vectorized (the shared classifier's layout): one per-code
trading-day position (sorted cumcount), then the episodes module's
per-window band masks + grouped pass classification. Flags stay
float64 (1.0 / 0.0 / NaN) inside the compute frame — the boolean/NULL
split happens ONCE at the emit boundary in raw host numpy (the
sanctioned DB-literal boundary; the CSV COPY renders True/"" into the
BOOLEAN columns, "" = NULL).
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from _common.db_commons import chunked_purge_async, csv_copy_frame_chunked_async
from _common.df_utils import host_array
from analysis._common import upsert_analysis_identity
from analysis.mov_ave_spread._common.steps import sec_type_code
from analysis.mov_ave_spread.steps.rsi import RSI_WINDOWS
from analysis.trends.episodes import rsi_tail_columns

import logging
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TREND_EPISODE_DAYS_TABLE = "analysis.trend_episode_days"

TREND_EPISODE_DAYS_ANALYSIS_NAME = "trend_episode_days"

TREND_EPISODE_DAYS_COLUMNS = [
    "sec_type", "code", "date",
    *[f"is_typical_{w}days" for w in RSI_WINDOWS],
    *[f"typical_ep_size_{w}days" for w in RSI_WINDOWS],
]

FLAG_COLUMNS = [f"is_typical_{w}days" for w in RSI_WINDOWS]
SIZE_COLUMNS = [f"typical_ep_size_{w}days" for w in RSI_WINDOWS]

TREND_EPISODE_DAYS_DESCRIPTION = (
    "RSI tail-episode days (the trends component's store): one row per "
    "(sec_type, code, date) with at least one window in the 25% tails "
    "of the code's own full-history RSI rank distribution. Per window, "
    "every in-band day carries its episode's SIDE-MIRRORED pass flag "
    "(is_typical_{W}days: BOTTOM 1.0 the isolated first-touch episodes "
    "— the capitulation; TOP 1.0 the re-test episodes — the double "
    "top; 0.0 the noisy population; gap-1 continuations inherit their "
    "pass's flag; NULL out-of-band) and its episode's total "
    "trading-day count (typical_ep_size_{W}days — both signal tiers' "
    "population marker + the curve tier's 1/size episode weight). "
    "Owned by analysis.trends (the consolidated episode anatomy — "
    "analysis.mov_ave_spread and analysis.analysis_signals both "
    "import from it, never the reverse); replaces the retired "
    "is_typical columns on analysis.mov_ave_rsi. Rebuilt WHOLESALE per "
    "sec_type on every ranks rebuild (band membership shifts with the "
    "full-history rank distribution)."
)


# ---------------------------------------------------------------------------
#  Compute (the trends episodes classifier on the ranks frame)
# ---------------------------------------------------------------------------

def compute_trend_episode_days(ranks: pd.DataFrame) -> pd.DataFrame:
    """Add the is_typical_{W}days + typical_ep_size_{W}days columns for
    the 6 Wilder windows.

    Args:
      ranks: frame carrying (sec_type, code, date, rank_rsi_{W}days —
          float64, NaN = unranked).

    Returns:
      A NEW frame with only (sec_type, code, date + the 12 columns),
      restricted to rows with at least one non-null flag. Flags stay
      float64 (1.0/0.0, NaN = out-of-band) and sizes float64 — the
      boolean/NULL split happens at the emit boundary only.
    """
    if ranks.empty:
        return ranks
    grp = ["sec_type", "code"]
    rank_cols = [f"rank_rsi_{w}days" for w in RSI_WINDOWS]
    base = ranks[["sec_type", "code", "date", *rank_cols]].copy()
    # the code's own trading-day position (the gap's unit — calendar
    # gaps are not rows, same convention as the signal tiers' _t)
    base = base.sort_values([*grp, "date"]).reset_index(drop=True)
    base["_p"] = base.groupby(grp, sort=False).cumcount()
    for w, rcol in zip(RSI_WINDOWS, rank_cols):
        base[f"is_typical_{w}days"], base[f"typical_ep_size_{w}days"] = \
            rsi_tail_columns(base, rcol)
    # in-band for ANY window — a bool Series OR-chain (cudf's
    # DataFrame.any(axis=1) falls back; elementwise | is GPU-native).
    in_band = base[FLAG_COLUMNS[0]].notna()
    for col in FLAG_COLUMNS[1:]:
        in_band = in_band | base[col].notna()
    out = base.loc[in_band, TREND_EPISODE_DAYS_COLUMNS].reset_index(
        drop=True)
    return out


# ---------------------------------------------------------------------------
#  Emit boundary (float64 flags -> bool/int rows for the CSV COPY)
# ---------------------------------------------------------------------------

def build_episode_days_copy_frame(
    days: pd.DataFrame,
) -> pd.DataFrame:
    """COPY frame: (sec_type, code, date + the 6 flag columns as python
    bool / None + the 6 episode-size columns as python int / None).
    Built via the REAL pandas ctor from host numpy object arrays — the
    sanctioned DB-literal boundary (to_csv renders True/"" and 12/"" —
    PostgreSQL COPY reads them into BOOLEAN / SMALLINT with "" =
    NULL)."""
    real_pd = getattr(pd, "_fsproxy_slow", pd)
    data: dict = {
        "sec_type": host_array(days["sec_type"].to_numpy()),
        "code": host_array(days["code"].to_numpy()),
        "date": host_array(days["date"].to_numpy()),
    }
    for col in FLAG_COLUMNS:
        arr = host_array(
            days[col].to_numpy(dtype="float64", na_value=np.nan)
        )
        obj = np.empty(arr.size, dtype=object)
        valid = np.isfinite(arr)
        obj[valid] = arr[valid] >= 0.5
        obj[~valid] = None
        data[col] = obj
    for col in SIZE_COLUMNS:
        arr = host_array(
            days[col].to_numpy(dtype="float64", na_value=np.nan)
        )
        obj = np.empty(arr.size, dtype=object)
        valid = np.isfinite(arr) & (arr > 0.0)
        obj[valid] = arr[valid].astype("int64").tolist()
        obj[~valid] = None
        data[col] = obj
    return real_pd.DataFrame(data, columns=TREND_EPISODE_DAYS_COLUMNS)


# ---------------------------------------------------------------------------
#  Pipeline (called from run_rsi AFTER the ranks rebuild — the episode
#  days need the ranks)
# ---------------------------------------------------------------------------

async def rebuild_trend_episode_days(
    conn,
    *,
    sec_type: str,
    code_filter: str | None = None,
) -> None:
    """Recompute + rewrite analysis.trend_episode_days for the run's
    scope FROM analysis.mov_ave_rsi_ranks (just rebuilt by the ranks
    hook). Wholesale: purge the (sec_type | code) scope, CSV-COPY the
    full recompute, upsert the analysis_identity registry."""
    t0 = time.time()
    logger.info("\n[ted] Rebuilding trend episode days (wholesale: band "
          "membership shifts with the full-history ranks)...")
    st_code = sec_type_code(sec_type)
    where = "WHERE sec_type = $1"
    args: list = [st_code]
    if code_filter is not None:
        where += " AND code = $2"
        args.append(code_filter)
    rows = await conn.fetch(
        f"SELECT sec_type, code, date, "
        + ", ".join(f"rank_rsi_{w}days" for w in RSI_WINDOWS)
        + f" FROM analysis.mov_ave_rsi_ranks {where}",
        *args,
    )
    if not rows:
        logger.info("    -> no ranks rows; skipping trend-episode-days "
              "write.")
        return
    real_pd = getattr(pd, "_fsproxy_slow", pd)
    ranks = real_pd.DataFrame(
        [dict(r) for r in rows],
        columns=["sec_type", "code", "date"]
        + [f"rank_rsi_{w}days" for w in RSI_WINDOWS],
    )
    # ranks arrive as int/None — float64 NaN for the compute frame
    for w in RSI_WINDOWS:
        col = f"rank_rsi_{w}days"
        ranks[col] = ranks[col].astype("float64")
    days = compute_trend_episode_days(ranks)
    logger.info(f"    -> computed {len(days):,} in-band episode days "
          f"from {len(ranks):,} rank rows")

    # ---- Purge the scope (conflict-free COPY afterwards) -------------
    if code_filter is not None:
        status = await conn.execute(
            f"DELETE FROM {TREND_EPISODE_DAYS_TABLE} "
            f"WHERE sec_type = $1 AND code = $2",
            st_code, code_filter,
        )
        n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
        logger.info(f"    -> single-code mode: deleted {n_del:,} episode "
              f"day rows for ({sec_type}, {code_filter})")
    else:
        n_del = await chunked_purge_async(
            conn, TREND_EPISODE_DAYS_TABLE,
            where_sql="sec_type = $1", params=(st_code,),
        )
        logger.info(f"    -> deleted {n_del:,} {sec_type} episode day "
              f"rows (wholesale rebuild)")

    total = await csv_copy_frame_chunked_async(
        conn, TREND_EPISODE_DAYS_TABLE,
        build_episode_days_copy_frame(days),
        partition_key="code",
        label="trend_episode_days",
    )
    logger.info(f"    -> inserted {total:,} episode day rows "
          f"({time.time() - t0:.1f}s)")

    # ---- Registry ------------------------------------------------------
    await upsert_analysis_identity(
        conn,
        name=TREND_EPISODE_DAYS_ANALYSIS_NAME,
        detail_name="trend_episode_days",
        description=TREND_EPISODE_DAYS_DESCRIPTION,
    )
