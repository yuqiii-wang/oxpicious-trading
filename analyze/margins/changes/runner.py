"""Pipeline entry point for margin_changes.

Orchestrates the margin trend detection pipeline:

  1. For each sec_type present in ``tech_stats_by_sec_type``, run
     ``detect_trend_episodes`` on the in-memory rz_balance + slope data
     (no DB round-trip for source data — reuses what the caller already
     fetched for the tech_stats step).
  2. Fetch trading_amount from ``stats.{stock,etf}_liquidity_margin``
     + ``stats.index_basic_stats`` and compute
     ``rz_buy_vs_trading_amt_ratio`` per episode.
  3. Truncate ``analysis.margin_changes`` and COPY-insert the final
     episodes DataFrame.
  4. Upsert the ``margin_changes`` row in ``analysis.analysis_identity``.

Always truncates + recomputes when called — new dates shift trend
boundaries, so a partial upsert would leave stale trends in the table.
"""
from __future__ import annotations

import pandas as pd

from analyze._common import upsert_analysis_identity

from analyze.margins.changes.constants import DESCRIPTION, INSERT_COLUMNS
from analyze.margins.changes.db_io import truncate_and_insert
from analyze.margins.changes.detection import detect_trend_episodes
from analyze.margins.changes.trading_amt import (
    compute_trading_amt_ratio,
    fetch_trading_amt,
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

    Pipeline steps:
      1. Detect trend episodes per sec_type (slope_ma5 sign + bridging).
      2. Fetch trading_amount and compute rz_buy_vs_trading_amt_ratio.
      3. Truncate + COPY-insert episodes into margin_changes.
      4. Upsert analysis_identity row.

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
    else:
        episodes_df = pd.concat(all_episodes, ignore_index=True)

        # Fetch trading_amount for all sec_types and compute the
        # rz_buy_vs_trading_amt_ratio ratio (Σ rz_buy / Σ trading_amount).
        sec_types_present = list(episodes_df["sec_type"].unique())
        print("    Fetching trading_amount for ratio computation "
              f"({', '.join(sec_types_present)})...", flush=True)
        trading_amt = await fetch_trading_amt(conn, sec_types_present)
        print(f"        -> {len(trading_amt):,} trading_amount rows",
              flush=True)

        episodes_df = compute_trading_amt_ratio(episodes_df, trading_amt)

        n = await truncate_and_insert(conn, episodes_df)
        print(f"    -> COPY-inserted {n:,} rows into margin_changes",
              flush=True)

    # Upsert the analysis_identity row.
    await upsert_analysis_identity(
        conn,
        name="margin_changes",
        detail_name="margin_changes",
        description=DESCRIPTION,
    )
    print("    -> upserted margin_changes identity row", flush=True)
