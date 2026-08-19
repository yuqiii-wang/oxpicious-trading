"""Pipeline entry point for margin_changes.

Orchestrates the full margin trend detection pipeline:

  1. For each sec_type present in ``tech_stats_by_sec_type``, run
     ``detect_trend_episodes`` on the in-memory rz_balance + slope data
     (no DB round-trip for source data — reuses what the caller already
     fetched for the tech_stats step).
  2. Fetch price RSI(14) from ``analysis.mov_ave_rsi`` (index only) and
     compute ``ratio_rsi_margin_vs_price`` per episode.
  3. Fetch price OHLC from ``stats.{index,etf,stock}_basic_stats`` for
     ALL sec_types present in the episodes, and compute the 4 OHLC
     margin/price ratios per episode.
  4. Truncate ``analysis.margin_changes`` and COPY-insert the final
     episodes DataFrame.
  5. Compute forward price forcasts (5d/20d/60d highs, lows, days-to-
     extremes) and store in ``analysis.margin_hype_to_price_forcasts``.
  6. Upsert the ``margin_changes`` row in ``analysis.analysis_identity``.

Always truncates + recomputes when called — new dates shift trend
boundaries, so a partial upsert would leave stale trends in the table.
"""
from __future__ import annotations

import pandas as pd

from analyze._common import upsert_analysis_identity

from analyze.margins.changes.constants import DESCRIPTION, INSERT_COLUMNS
from analyze.margins.changes.db_io import truncate_and_insert
from analyze.margins.changes.detection import detect_trend_episodes
from analyze.margins.changes.forcasts import run_margin_forcasts
from analyze.margins.changes.price_ohlc import (
    compute_price_ohlc_ratio,
    fetch_price_ohlc,
)
from analyze.margins.changes.price_rsi import (
    compute_price_rsi_ratio,
    fetch_price_rsi,
)


async def run_margin_changes(
    conn,
    *,
    histories: dict[str, pd.DataFrame],
    tech_stats_by_sec_type: dict[str, pd.DataFrame],
    force: bool = True,
) -> None:
    """Detect margin balance trend episodes and populate margin_changes.

    Reuses the in-memory ``histories`` + ``tech_stats_by_sec_type``
    collected by the pipeline (no DB round-trip for source data). Always
    truncates + recomputes — new dates shift trend boundaries.

    Upserts its OWN analysis_identity row (margin_changes) internally.

    Args:
        conn: asyncpg connection.
        histories: {sec_type: DataFrame[code, date, rz_balance, rz_buy]}.
        tech_stats_by_sec_type: {sec_type: DataFrame with
            margin_balance_slope_ma5 per (code, date)}.
        force: ignored — always truncates + recomputes (trend boundaries
            shift when new dates arrive). Kept for API compatibility
            with the pipeline.
    """
    print("\n  Detecting margin balance trend episodes...", flush=True)

    all_episodes: list[pd.DataFrame] = []
    for sec_type, tech_stats in tech_stats_by_sec_type.items():
        history = histories.get(sec_type)
        if history is None or history.empty or tech_stats.empty:
            print(f"    [{sec_type}] no data; skipping.", flush=True)
            continue

        episodes = detect_trend_episodes(history, tech_stats, sec_type)
        n_up = int((episodes["is_trend_up_not_down"] == True).sum()) if not episodes.empty else 0  # noqa: E712
        n_down = int((episodes["is_trend_up_not_down"] == False).sum()) if not episodes.empty else 0  # noqa: E712
        print(f"    [{sec_type}] {len(episodes):,} trend episodes "
              f"({n_up:,} UP, {n_down:,} DOWN)", flush=True)
        if not episodes.empty:
            all_episodes.append(episodes)

    if not all_episodes:
        print("    -> no trend episodes detected; writing empty table.",
              flush=True)
        await truncate_and_insert(conn, pd.DataFrame(columns=INSERT_COLUMNS))
        # Also truncate forcasts and write empty.
        await run_margin_forcasts(
            conn, pd.DataFrame(columns=["code", "sec_type", "start_date", "end_date"])
        )
    else:
        episodes_df = pd.concat(all_episodes, ignore_index=True)

        # Fetch price RSI from mov_ave_rsi for the ratio computation.
        print("    Fetching price RSI from mov_ave_rsi (index only)...",
              flush=True)
        price_rsi = await fetch_price_rsi(conn)
        print(f"        -> {len(price_rsi):,} price RSI rows", flush=True)

        episodes_df = compute_price_rsi_ratio(episodes_df, price_rsi)

        # Fetch price OHLC from basic_stats tables for the OHLC ratio
        # computation. Works for ALL sec_types (index / etf / stock).
        sec_types_present = list(episodes_df["sec_type"].unique())
        print(f"    Fetching price OHLC from basic_stats "
              f"({', '.join(sec_types_present)})...", flush=True)
        price_ohlc = await fetch_price_ohlc(conn, sec_types_present)
        print(f"        -> {len(price_ohlc):,} price OHLC rows", flush=True)

        episodes_df = compute_price_ohlc_ratio(episodes_df, price_ohlc)

        n = await truncate_and_insert(conn, episodes_df)
        print(f"    -> COPY-inserted {n:,} rows into margin_changes",
              flush=True)

        # Compute forward price forcasts (5d/20d/60d).
        # Reuses the episodes_df (with code, sec_type, start_date, end_date)
        # to fetch forward closes and compute highs/lows/days-to-extremes.
        await run_margin_forcasts(conn, episodes_df)

    # Upsert the analysis_identity row.
    await upsert_analysis_identity(
        conn,
        name="margin_changes",
        detail_name="margin_changes",
        description=DESCRIPTION,
    )
    print("    -> upserted margin_changes identity row", flush=True)
