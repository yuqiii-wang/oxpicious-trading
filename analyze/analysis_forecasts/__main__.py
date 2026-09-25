"""Entry point for analyze.analysis_forecasts.

Run via ``python -m analyze.analysis_forecasts``.

Annual per-security forecast analysis in the ``analysis_forecasts``
schema (see database/sql/analysis/analysis_forecasts/):

  - mov_rsi: per (sec_type, code, stat_date, rsi_window, side, pct,
    regime_state) RSI extreme-percentile bucket definitions (RSI
    values join from analysis.mov_ave_rsi).

  - mov_std: per (sec_type, code, stat_date, ma_window, k, side,
    regime_state) Bollinger-breach bucket definitions (band inputs
    join from analysis.mov_ave_spreads_detail / stats.*_tech_stats).

  - mov_pairs: per (sec_type, code, stat_date, fast_leg, pair_window,
    side, regime_state) MA-pair CROSS (golden / death cross)
    bucket definitions built on the EXISTING relative-MA-spread columns
    of analysis.mov_ave_spreads_detail — fast_leg 'ma5' reads
    ma5_vs_ma{W} = (ma5 - ma_{W}) / ma_{W}, fast_leg 'price' the
    close-price spread price_vs_ma{W}, W ∈ {60, 120, 255}: side=top a
    CROSS UP (the spread turns > 0 from <= 0 — the fast leg rises
    through the slow MA), side=bottom a CROSS DOWN — one-day signals
    (each cross day is its own forecast signal).

  - mov_pairs_ema: the EMA sibling of mov_pairs — identical cross
    machinery on the EXISTING relative-EMA-spread columns of
    analysis.mov_ave_spreads_detail_ema — fast_leg 'ema6' reads
    ema6_vs_ema{W} = (ema6 - ema_{W}) / ema_{W}, fast_leg 'price' the
    close-price spread price_vs_ema{W}, W ∈ {60, 120, 255}.


  - high_low_streaks: MA-Spread High/Low streak MEAN-MID anchor buckets
    built on the EXISTING band-break excursion streaks of
    analysis.mov_ave_high_low_pct_streaks — every streak period is
    audited at its mean-mid anchor day (the ((day_count-1)//2 + 1)-th
    trading day of the span — an 8-day streak anchors its 4th day; an
    EX-POST audit anchor, not a live trigger): side=top an ABOVE-band
    excursion (the unrounded close on end_date above the end month's
    high_val band), side=bottom a BELOW-band excursion. Result rows
    (mean forward changes from the ANCHOR close) live in
    forecast_results via forecast_id; the config JSONB
    records the bucket's streak-length context (mean/min/max
    day_count).

  - base_rates: per (sec_type, code, stat_date, period) the
    UNCONDITIONAL same-window base rates (mean n-day forward change
    over ALL of the code's window trading days) — the reference the
    bucket results are read against (lift).

  - forecast_results: the result data (mean forward changes at
    next/5d/20d horizons; close-based max/min ENDPOINT forward
    changes at the 5d/20d horizons),
    keyed by forecast_id; every bucket carries 4 period rows — the three
    horizons plus the weight-blended 'mixed' row (config's
    MIXED_HORIZON_WEIGHTS: 5d 0.65 / next 0.25 / 20d 0.10 —
    the row the analysis_signals confirmation gate reads, so every
    forecast horizon of the same signal trigger contributes to the
    signal); every mov_rsi / mov_std row links
    1:1 to its result rows via forecast_id.

Pipeline per sec_type (index / etf / stock):
  1. Fetch active-universe codes (recent-data pre-filter).
  2. Incremental: stat_dates missing from each target table are
     computed, plus the MUTABLE SCOPE (config.mutable_dates) — the
     ROLLING LATEST snapshot (keyed at the sec_type's latest available
     data date, NOT the year-end; always refreshed — its forward data
     grows daily) and the newest completed year-end while its
     20-trading-day forward windows are still unrealized. Stale keys
     (yesterday's rolling-latest date; legacy pre-rolling keys) are
     SWEPT before the target resolution.
     ``--force`` deletes the sec_type's mov_* rows AND their linked
     forecast_results rows (plus base_rates), then recomputes every
     target stat_date.
  3. Fetch the joined long input frame (price / ma / rsi / std /
     ma5-vs-MA + ema6-vs-EMA spread columns; date >= earliest needed
     window start), the daily market-regime states, and compute
     per-code forward changes (1/5/20 trading days).
  4. Scatter to (date × code) wide matrices + the market-regime label
     matrix and run the vectorized aggregation engines
     (compute_rsi / compute_std / compute_pairs /
     compute_high_low_streaks / compute_base), writing
     snapshot-major batches: forecast_id allocated from the identity
     sequence, then COPY into forecast_results +
     forecast_identities (the shared-PK registry: sec_type / code /
     stat_date / bucket family per forecast_id — the search-by-id
     table) + the mov_* table in
     ONE transaction per snapshot (no pre-clear DELETEs — snapshots are
     only written when missing or after force/refresh deletion;
     atomicity keeps the 1:1 link crash-safe).
  5. Upsert analysis.analysis_identity.

Search by forecast_id: ``python -m analyze.analysis_forecasts
--search-forecast-id <id>`` resolves the id against
analysis_forecasts.forecast_identities and prints the identity + the
motivation row (no computation).

Row emission contract: buckets with day_count = 0 emit NO row (a code
without valid RSI / MA+std in a window simply has no buckets that
snapshot); base-rate rows are emitted only where base_count > 0.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

# cudf.pandas activation — must run before pandas first import. The
# _common helpers imported below (build_commons → db_commons →
# _batched_copy) transitively import pandas at module level, so activating
# any later would silently skip the cudf proxy ("pandas already imported").
from _common.df_utils._activate import activate  # noqa: E402
activate()

import argparse
import asyncio
import os
import sys
import time
from datetime import date

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.analysis_forecasts`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)
from _common._holidays_and_weekdays import (  # noqa: E402
    last_business_day,
    recent_trading_day_cutoff,
)
from _common.db_commons import (  # noqa: E402
    copy_insert_async,
)

setup_utf8_stdout()

from analyze._common import upsert_analysis_identity  # noqa: E402
from analyze.analysis_forecasts.config import (  # noqa: E402
    TABLE_FORECAST,
    TABLE_IDENTITIES,
    TABLE_MOV_RSI,
    TABLE_MOV_STD,
    TABLE_MOV_PAIRS,
    TABLE_MOV_PAIRS_EMA,
    TABLE_HIGH_LOW_STREAKS,
    TABLE_MARGIN_RATIO,
    TABLE_PE,
    TABLE_DIVIDEND,
    TABLE_BASE_RATE,
    ANALYSIS_NAME_RSI,
    ANALYSIS_NAME_STD,
    ANALYSIS_NAME_MOV_PAIRS,
    ANALYSIS_NAME_MOV_PAIRS_EMA,
    ANALYSIS_NAME_HIGH_LOW_STREAKS,
    ANALYSIS_NAME_MARGIN_RATIO,
    ANALYSIS_NAME_PE,
    ANALYSIS_NAME_DIVIDEND,
    ANALYSIS_NAME_BASE_RATE,
    DESCRIPTION_RSI,
    DESCRIPTION_STD,
    DESCRIPTION_MOV_PAIRS,
    DESCRIPTION_MOV_PAIRS_EMA,
    DESCRIPTION_HIGH_LOW_STREAKS,
    DESCRIPTION_MARGIN_RATIO,
    DESCRIPTION_PE,
    DESCRIPTION_DIVIDEND,
    DESCRIPTION_BASE_RATE,
    SEC_TYPES,
    MOV_RSI_COLUMNS,
    MOV_STD_COLUMNS,
    MOV_PAIRS_COLUMNS,
    MOV_PAIRS_EMA_COLUMNS,
    HIGH_LOW_STREAKS_COLUMNS,
    MARGIN_RATIO_COLUMNS,
    PE_COLUMNS,
    DIVIDEND_COLUMNS,
    RSI_WINDOWS,
    MA_WINDOWS,
    MOV_PAIRS_WINDOWS,
    MOV_PAIRS_EMA_WINDOWS,
    N_YEARS,
    MARGIN_RATIO_Z_WINDOW,
    VAL_PCTS,
    WINDOW_YEARS,
    LOOKBACK_PERIOD,
)
from analyze.analysis_forecasts.fetch import (  # noqa: E402
    fetch_active_codes,
    fetch_analysis_inputs,
    fetch_first_dates,
    fetch_forecast_identity,
    fetch_latest_data_date,
    fetch_market_regimes,
    fetch_high_low_streaks,
    add_forward_changes,
    add_margin_ratio_features,
)
from analyze.analysis_forecasts.wide import (  # noqa: E402
    build_stat_specs,
    StatSpec,
)
from analyze.analysis_forecasts.writer import (  # noqa: E402
    _compute_dates,
    _delete_dates,
    _delete_sec_type,
    _sweep_stale_dates,
    _write_snapshot,
)
# Metric engines: one metric one file, imported LAZILY inside their
# stage bodies — a stage that is not selected (--forecast-metrics) or not yet
# implemented never pays its import.
from analyze.analysis_forecasts.compute_rsi import compute_rsi_results  # noqa: E402
from analyze.analysis_forecasts.compute_base import compute_base_rate_rows  # noqa: E402
from analyze.analysis_forecasts.compute_std import compute_std_results  # noqa: E402
from analyze.analysis_forecasts.compute_pairs import (  # noqa: E402
    compute_epairs_results,
    compute_pairs_results,
)
from analyze.analysis_forecasts.compute_margin_ratio import compute_margin_ratio_results  # noqa: E402
from analyze.analysis_forecasts.compute_pe import compute_pe_results  # noqa: E402
from analyze.analysis_forecasts.compute_dividend import compute_dividend_results  # noqa: E402
from analyze.analysis_forecasts.compute_high_low_streaks import compute_high_low_streaks_results  # noqa: E402

# The per-metric stage keys --forecast-metrics accepts.
STAGE_NAMES = ("rsi", "std", "pairs", "epairs", "hstreaks",
               "mratio", "pe", "div", "base")

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("analysis_forecasts")



async def _release_between_stages(label: str) -> None:
    """Drop retained cuDF/pandas references + return pooled device
    memory between family stages. The 2026-09 residual-VRAM study
    (temp_scripts/study_gpu_residual_mem.py): device usage grows
    monotonically to the card high-water ACROSS snapshots/families even
    with RMM CudaMemoryResource active (no pool) and stays there
    through long DB-write stretches — live engine references keep
    device frames alive; an explicit release pass flattens the curve
    (everything IS returned at process exit — no permanent leak)."""
    from _common.post_check import release_memory

    stats = release_memory()
    ctx = stats.get("ctx_used_after")
    rss = stats.get("rss_after")
    logger.info(f"    [release:{label}] RSS="
          f"{rss / 1024**3:.1f}GiB ctx={ctx / 1024**3:.1f}GiB"
          if rss is not None and ctx is not None else
          f"    [release:{label}] stats unavailable")


async def _process_sec_type(
    conn,
    sec_type: str,
    specs: list[StatSpec],
    *,
    force: bool,
    metrics: frozenset[str],
    codes_limit: int | None = None,
    codes_filter: list[str] | None = None,
) -> tuple[int, ...]:
    """Process one sec_type end-to-end for the --forecast-metrics-selected metric
    stages (all stages when ``metrics`` is empty).
    Returns (mov_rsi, mov_std, mov_pairs, mov_pairs_ema,
    high_low_streaks, margin_ratio, pe, dividend) bucket rows +
    base_rates + the run's snapshot scope (the stat_dates the selected
    stages target — the regime-weights step's fit scope)."""
    logger.info(f"\n  [{sec_type}] Fetching active codes...")
    codes = sorted(await fetch_active_codes(conn, sec_type))
    if codes_filter is not None:
        active = set(codes)
        unknown = [c for c in codes_filter if c not in active]
        if unknown:
            logger.warning(f"  [{sec_type}]   --codes not in the active "
                           f"universe (ignored): {unknown}")
        codes = sorted(active & set(codes_filter))
        logger.info(f"  [{sec_type}]   restricted to {len(codes)} codes "
                    f"(--codes): {codes}")
        if not codes:
            logger.info(f"  [{sec_type}]   no requested code is active; "
                        f"skipping.")
            return 0, 0, 0, 0, 0, 0, 0, 0, 0, []
    if codes_limit is not None:
        codes = codes[:codes_limit]
        logger.info(f"  [{sec_type}]   limited to the first {codes_limit} "
              f"codes (--limit): {codes}")
    logger.info(f"  [{sec_type}]   {len(codes):,} active codes")
    if not codes:
        logger.info(f"  [{sec_type}]   no active codes; skipping.")
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, []

    # ---- Emittable-snapshot bound ----------------------------------------
    # A snapshot emits over its ACTUAL window — min(actual history,
    # WINDOW_YEARS; the per-code live gate joins each code from its own
    # first-data date) — so the only unemittable snapshots are those at
    # or before the universe's EARLIEST first-data date (zero window
    # days). Without this bound those snapshots stay "missing" forever
    # (they can never emit a row), so every incremental run re-targets
    # them and drags the input fetch back years before any code needs
    # it.
    grid_dates = {s.stat_date for s in specs}
    first_dates = await fetch_first_dates(conn, sec_type, codes)
    if first_dates:
        f_min = min(first_dates.values())
        n_all = len(specs)
        specs = [s for s in specs if s.stat_date > f_min]
        logger.info(f"  [{sec_type}]   universe first data {f_min.isoformat()} "
              f"→ {len(specs)} of {n_all} snapshots can emit "
              f"(min(actual, {WINDOW_YEARS}y) window gate)")

    # ---- Stale-key sweep (the rolling latest key's predecessor) ----------
    # Retired keys (yesterday's rolling-latest date; legacy pre-rolling
    # keys) must neither be recomputed nor linger. --force purges its
    # whole scope instead, so the sweep is incremental-only. Scoped
    # exactly like the deletes (metrics / codes).
    if not force:
        await _sweep_stale_dates(conn, sec_type, grid_dates,
                                 metrics=metrics or None,
                                 codes=codes_filter)

    # ---- Determine target snapshots (incremental / force) ----------------
    # Per-stage gates: a stage not in ``metrics`` contributes no target
    # snapshots (its list stays empty → its windows/body/upserts skip).
    if force:
        logger.info(f"  [{sec_type}] FORCE mode: deleting existing {sec_type} "
              f"rows + linked forecast_results + base_rates for the "
              f"selected stages...")
        await _delete_sec_type(conn, sec_type, metrics=metrics or None,
                               codes=codes_filter)
        compute_rsi = list(specs) if "rsi" in metrics else []
        compute_std = list(specs) if "std" in metrics else []
        compute_pairs = list(specs) if "pairs" in metrics else []
        compute_epairs = list(specs) if "epairs" in metrics else []
        compute_hstreaks = list(specs) if "hstreaks" in metrics else []
        compute_mratio = list(specs) if "mratio" in metrics else []
        compute_pe = list(specs) if "pe" in metrics else []
        compute_div = list(specs) if "div" in metrics else []
        compute_base = list(specs) if "base" in metrics else []
    else:
        compute_rsi, refresh_rsi = (await _compute_dates(
            conn, TABLE_MOV_RSI, sec_type, specs)
            if "rsi" in metrics else ([], []))
        compute_std, refresh_std = (await _compute_dates(
            conn, TABLE_MOV_STD, sec_type, specs)
            if "std" in metrics else ([], []))
        compute_pairs, refresh_pairs = (await _compute_dates(
            conn, TABLE_MOV_PAIRS, sec_type, specs)
            if "pairs" in metrics else ([], []))
        compute_epairs, refresh_epairs = (await _compute_dates(
            conn, TABLE_MOV_PAIRS_EMA, sec_type, specs)
            if "epairs" in metrics else ([], []))
        compute_hstreaks, refresh_hstreaks = (await _compute_dates(
            conn, TABLE_HIGH_LOW_STREAKS, sec_type, specs)
            if "hstreaks" in metrics else ([], []))
        compute_mratio, refresh_mratio = (await _compute_dates(
            conn, TABLE_MARGIN_RATIO, sec_type, specs)
            if "mratio" in metrics else ([], []))
        compute_pe, refresh_pe = (await _compute_dates(
            conn, TABLE_PE, sec_type, specs)
            if "pe" in metrics else ([], []))
        compute_div, refresh_div = (await _compute_dates(
            conn, TABLE_DIVIDEND, sec_type, specs)
            if "div" in metrics else ([], []))
        compute_base, refresh_base = (await _compute_dates(
            conn, TABLE_BASE_RATE, sec_type, specs)
            if "base" in metrics else ([], []))
        # Refresh-window snapshots present in the DB: delete + recompute
        # (their long-horizon forward windows were not complete at
        # first write).
        if refresh_rsi:
            await _delete_dates(conn, TABLE_MOV_RSI, sec_type,
                                 refresh_rsi, linked_results=True, codes=codes_filter)
        if refresh_std:
            await _delete_dates(conn, TABLE_MOV_STD, sec_type,
                                 refresh_std, linked_results=True, codes=codes_filter)
        if refresh_pairs:
            await _delete_dates(conn, TABLE_MOV_PAIRS, sec_type,
                                 refresh_pairs, linked_results=True, codes=codes_filter)
        if refresh_epairs:
            await _delete_dates(conn, TABLE_MOV_PAIRS_EMA, sec_type,
                                 refresh_epairs, linked_results=True, codes=codes_filter)
        if refresh_hstreaks:
            await _delete_dates(conn, TABLE_HIGH_LOW_STREAKS, sec_type,
                                 refresh_hstreaks, linked_results=True, codes=codes_filter)
        if refresh_mratio:
            await _delete_dates(conn, TABLE_MARGIN_RATIO, sec_type,
                                 refresh_mratio, linked_results=True, codes=codes_filter)
        if refresh_pe:
            await _delete_dates(conn, TABLE_PE, sec_type,
                                 refresh_pe, linked_results=True, codes=codes_filter)
        if refresh_div:
            await _delete_dates(conn, TABLE_DIVIDEND, sec_type,
                                 refresh_div, linked_results=True, codes=codes_filter)
        if refresh_base:
            await _delete_dates(conn, TABLE_BASE_RATE, sec_type,
                                 refresh_base, linked_results=False, codes=codes_filter)
        logger.info(f"  [{sec_type}]   snapshots to compute: "
              f"rsi={len(compute_rsi)} std={len(compute_std)} "
              f"pairs={len(compute_pairs)} "
              f"epairs={len(compute_epairs)} "
              f"hstreaks={len(compute_hstreaks)} "
              f"mratio={len(compute_mratio)} "
              f"pe={len(compute_pe)} div={len(compute_div)} "
              f"base={len(compute_base)} "
              f"of {len(specs)} (+ the mutable scope: the rolling "
              f"latest snapshot + the newest year-end while its 20d "
              f"forward windows realize)")
    if not compute_rsi and not compute_std \
            and not compute_pairs and not compute_epairs \
\
            and not compute_hstreaks \
            and not compute_mratio and not compute_pe \
            and not compute_div and not compute_base:
        logger.info(f"  [{sec_type}]   up to date; skipping.")
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, []

    # ---- Fetch inputs (bounded to the earliest needed window start) ------
    todo = {
        s.stat_date: s
        for s in compute_rsi + compute_std + compute_pairs
        + compute_epairs + compute_hstreaks
        + compute_mratio + compute_pe + compute_div + compute_base
    }
    scope_dates = sorted(todo)
    since = min(s.lower for s in todo.values())
    if "mratio" in metrics:
        # The margin_ratio rolling z consumes each code's OWN
        # MARGIN_RATIO_Z_WINDOW-row history BEFORE a day has a defined
        # state — the shared fetch must reach back past the OLDEST
        # targeted window start, or the first ~5y of that window lose
        # every margin bucket (the incremental refresh of the last
        # completed year was hit hardest: its window start IS the
        # fetch bound). 1220 trading rows + a 1-year buffer, because
        # the z consumes each code's own row sequence (a sparsely
        # traded code needs more calendar span for 1220 rows).
        since = recent_trading_day_cutoff(
            MARGIN_RATIO_Z_WINDOW + 260, since)
    logger.info(f"  [{sec_type}] Fetching joined inputs (price / ma / rsi / "
          f"std) for {len(codes):,} codes since "
          f"{since.isoformat()}...")
    df = await fetch_analysis_inputs(conn, sec_type, codes, since,
                                     families=set(metrics))
    logger.info(f"  [{sec_type}]   {len(df):,} (code, date) rows")
    if df.empty:
        logger.info(f"  [{sec_type}]   no source data; skipping.")
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, []
    regimes = await fetch_market_regimes(conn, sec_type, since)
    logger.info(f"  [{sec_type}]   {len(regimes):,} daily market-regime rows")

    # ---- Long-frame feature adds (every family's shared input) -----------
    df = add_forward_changes(df)
    if "mratio" in metrics:
        df = add_margin_ratio_features(df)

    n_rsi = n_std = n_pairs = n_epairs = n_hstreaks = 0
    n_mratio = n_pe = n_div = 0
    n_base = 0

    # ---- Stage 1: RSI extreme buckets (cudf-native df engine) -------------
    if compute_rsi:
        logger.info(f"  [{sec_type}] Computing RSI extreme buckets "
              f"(windows={list(RSI_WINDOWS)}) for {len(compute_rsi)} "
              f"months...")
        for stat_date, rows in compute_rsi_results(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_rsi,
        ):
            n = await _write_snapshot(conn, TABLE_MOV_RSI, MOV_RSI_COLUMNS, rows)
            n_rsi += n
            logger.info(f"    [{stat_date}] mov_rsi + forecast_results: "
                  f"wrote {n:,} rows")

    # ---- Stage 2: Bollinger-breach buckets (cudf-native df engine) --------
    await _release_between_stages("std")

    if compute_std:
        logger.info(f"  [{sec_type}] Computing Bollinger-breach buckets "
              f"(ma_windows={list(MA_WINDOWS)}) for {len(compute_std)} "
              f"months...")
        for stat_date, rows in compute_std_results(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_std,
        ):
            n = await _write_snapshot(conn, TABLE_MOV_STD, MOV_STD_COLUMNS, rows)
            n_std += n
            logger.info(f"    [{stat_date}] mov_std + forecast_results: "
                  f"wrote {n:,} rows")

    # ---- Stage 3: MA-pair cross buckets (cudf-native df engine) -----------
    # CROSS-EVENT buckets on the EXISTING analysis.mov_ave_spreads_detail
    # ma5_vs_ma{W} spreads (fetched as pair_{W}): a trigger is the
    # spread's sign flip (golden / death cross); one-day signals.
    await _release_between_stages("pairs")

    if compute_pairs:
        logger.info(f"  [{sec_type}] Computing MA-pair cross buckets "
              f"(pair_windows={list(MOV_PAIRS_WINDOWS)}) for "
              f"{len(compute_pairs)} snapshots...")
        for stat_date, rows in compute_pairs_results(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_pairs,
        ):
            n = await _write_snapshot(conn, TABLE_MOV_PAIRS,
                                   MOV_PAIRS_COLUMNS, rows)
            n_pairs += n
            logger.info(f"    [{stat_date}] mov_pairs + forecast_results: "
                  f"wrote {n:,} rows")

    # ---- Stage 5: EMA-pair cross buckets (cudf-native df engine) ----------
    # The identical cross machinery on the EXISTING
    # analysis.mov_ave_spreads_detail_ema ema6_vs_ema{W} spreads (fast
    # leg fixed ema6).
    await _release_between_stages("epairs")

    if compute_epairs:
        logger.info(f"  [{sec_type}] Computing EMA-pair cross buckets "
              f"(pair_windows={list(MOV_PAIRS_EMA_WINDOWS)}) for "
              f"{len(compute_epairs)} snapshots...")
        for stat_date, rows in compute_epairs_results(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_epairs,
        ):
            n = await _write_snapshot(conn, TABLE_MOV_PAIRS_EMA,
                                   MOV_PAIRS_EMA_COLUMNS, rows)
            n_epairs += n
            logger.info(f"    [{stat_date}] mov_pairs_ema + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 6: High/Low streak mean-mid anchors (cudf-native) ----------
    # The streaks come from the EXISTING analysis.mov_ave_high_low_pct_
    # streaks table (run python -m analyze.mov_ave_spread first); every
    # streak is audited at its MEAN-MID anchor day, resolved against the
    # union trading-day calendar by the df engine.
    await _release_between_stages("hstreaks")

    if compute_hstreaks:
        streaks_df = await fetch_high_low_streaks(
            conn, sec_type, codes, since
        )
        if streaks_df.empty:
            logger.info(f"  [{sec_type}] high_low_streaks: no streak rows "
                  f"in analysis.mov_ave_high_low_pct_streaks (run "
                  f"python -m analyze.mov_ave_spread to build them); "
                  f"skipping.")
        else:
            logger.info(f"  [{sec_type}] Computing High/Low streak "
                  f"mean-mid anchor buckets from {len(streaks_df):,} "
                  f"streaks for {len(compute_hstreaks)} snapshots...")
            for stat_date, rows in compute_high_low_streaks_results(
                df=df, first_dates=first_dates, regimes=regimes,
                codes=codes, sec_type=sec_type, specs=compute_hstreaks,
                streaks_df=streaks_df,
            ):
                n = await _write_snapshot(
                    conn, TABLE_HIGH_LOW_STREAKS,
                    HIGH_LOW_STREAKS_COLUMNS, rows,
                )
                n_hstreaks += n
                logger.info(f"    [{stat_date}] high_low_streaks + "
                      f"forecast_results: wrote {n:,} rows")

    # ---- Stage 7: margin-buy intensity states (cudf-native df engine) -----
    await _release_between_stages("mratio")

    if compute_mratio:
        logger.info(f"  [{sec_type}] Computing margin_ratio state buckets "
              f"(融资买入额/成交额 z states) for {len(compute_mratio)} "
              f"months...")
        for stat_date, rows in compute_margin_ratio_results(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_mratio,
        ):
            n = await _write_snapshot(conn, TABLE_MARGIN_RATIO,
                                   MARGIN_RATIO_COLUMNS, rows)
            n_mratio += n
            logger.info(f"    [{stat_date}] margin_ratio_state + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 9: valuation PE extreme-percentile buckets (cudf-native) --
    await _release_between_stages("pe")

    if compute_pe:
        logger.info(f"  [{sec_type}] Computing pe extreme-percentile "
              f"buckets (pcts={list(VAL_PCTS)}) for {len(compute_pe)} "
              f"months...")
        for stat_date, rows in compute_pe_results(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_pe,
        ):
            n = await _write_snapshot(conn, TABLE_PE, PE_COLUMNS, rows)
            n_pe += n
            logger.info(f"    [{stat_date}] pe_state + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 10: dividend-yield extreme-percentile buckets (cudf) ------
    await _release_between_stages("div")

    if compute_div:
        logger.info(f"  [{sec_type}] Computing dividend extreme-percentile "
              f"buckets (pcts={list(VAL_PCTS)}) for {len(compute_div)} "
              f"months...")
        for stat_date, rows in compute_dividend_results(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_div,
        ):
            n = await _write_snapshot(conn, TABLE_DIVIDEND, DIVIDEND_COLUMNS,
                                   rows)
            n_div += n
            logger.info(f"    [{stat_date}] dividend_state + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 11: unconditional base rates (cudf-native df engine) -------
    await _release_between_stages("base")

    if compute_base:
        logger.info(f"  [{sec_type}] Computing base rates for "
              f"{len(compute_base)} snapshots...")
        for stat_date, rows in compute_base_rate_rows(
            df=df, first_dates=first_dates, regimes=regimes,
            codes=codes, sec_type=sec_type, specs=compute_base,
        ):
            await copy_insert_async(conn, TABLE_BASE_RATE, rows)
            n_base += len(rows)
        logger.info(f"    base_rates: wrote {n_base:,} rows")

    await _release_between_stages("sec_type-done")

    return (n_rsi, n_std, n_pairs, n_epairs, n_hstreaks,
            n_mratio, n_pe, n_div, n_base, scope_dates)


# ---------------------------------------------------------------------------
#  Search by forecast_id (analysis_forecasts.forecast_identities registry)
# ---------------------------------------------------------------------------

async def _search_forecast_identity(conn, forecast_id: int) -> None:
    """Print one forecast bucket's registry identity — the shared PK
    (sec_type, code, stat_date) + bucket family from
    forecast_identities, plus the full motivation row joined from the
    family's table."""
    ident = await fetch_forecast_identity(conn, forecast_id)
    if ident is None:
        logger.info(f"forecast_id {forecast_id}: not found in "
                    f"{TABLE_IDENTITIES}")
        return
    logger.info(f"forecast_id {forecast_id}:")
    for key in ("sec_type", "code", "stat_date", "bucket",
                "streak_signal_days", "delayed_signal_days",
                "lookback_period"):
        logger.info(f"  {key:15s} {ident[key]}")
    motivation = ident.get("motivation")
    if motivation is not None:
        logger.info("  motivation row:")
        for key, value in motivation.items():
            logger.info(f"    {key:18s} {value}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analysis Forecasts (ETF + Index + Stock). Monthly "
                    "per-security forecast analysis over a trailing "
                    "5-year window: analysis_forecasts.mov_rsi (RSI "
                    "extreme-percentile buckets), mov_std (Bollinger-"
                    "breach buckets), "
                    "mov_pairs (MA5-vs-MA cross / golden-death-cross "
                    "buckets on the existing ma5_vs_ma{W} / "
                    "price_vs_ma{W} spreads — fast legs ma5 + the "
                    "close price), mov_pairs_ema (the EMA sibling on "
                    "the existing ema6_vs_ema{W} / price_vs_ema{W} "
                    "spreads — fast legs ema6 + the close price) and "
                    "high_low_streaks "
                    "(MA-Spread High/Low streak buckets audited at each "
                    "streak's MEAN-MID anchor day — the "
                    "((day_count-1)//2 + 1)-th trading day of the span, "
                    "e.g. an 8-day streak anchors its 4th day — from "
                    "the existing analysis.mov_ave_high_low_pct_streaks) "
                    "and pe_state / dividend_state (valuation PE / "
                    "dividend-yield extreme-percentile buckets over "
                    "analysis.pe / analysis.dividends — the mov_rsi pct "
                    "convention, OPPOSITE side mappings: PE "
                    "lower-the-better → top-pct% (expensive) PE days "
                    "bearish/top; dividend yield higher-the-better → "
                    "top-pct% (high-yield) days bullish/bottom) hold "
                    "the motivation cols; "
                    "forecast_results (linked "
                    "1:1 via forecast_id) holds the result data — mean "
                    "forward changes at next/5d/20d horizons; "
                    "close-based max/min ENDPOINT forward changes at "
                    "the 5d/20d horizons; base_rates "
                    "holds the unconditional same-window reference "
                    "(mean change over all window days)."
    )
    ap.add_argument(
        "--sec-type", choices=SEC_TYPES, default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    ap.add_argument(
        "--years", type=int, default=N_YEARS,
        help=f"Number of completed YEAR-END snapshots to target on the "
             f"annual grid (default {N_YEARS} = a decade of annual "
             f"snapshots); the ROLLING LATEST snapshot (keyed at the "
             f"sec_type's latest available data date) is always "
             f"appended.",
    )
    ap.add_argument(
        "--search-forecast-id", type=int, default=None, metavar="ID",
        help="Print the forecast bucket's registry identity "
             "(analysis_forecasts.forecast_identities: sec_type / code / "
             "stat_date / bucket family + the motivation row) and exit "
             "— no computation.",
    )
    ap.add_argument(
        "--forecast-metrics", type=str, default=None,
        help="Comma-separated metric stages to run (choices: "
             f"{', '.join(STAGE_NAMES)}). Default: all. Unimplemented "
             "stages fail at their lazy import when selected.",
    )
    ap.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Cap the active-code universe to the first N sorted codes "
             "(smoke-testing aid; default: no cap). CAUTION: the delete "
             "stays sec_type-scoped — with --force or the incremental "
             "refresh window the deleted rows of the OTHER codes are "
             "NOT recomputed. Use --codes for a safe code-scoped run.",
    )
    ap.add_argument(
        "--codes", type=str, default=None, metavar="CODE[,CODE...]",
        help="Comma-separated codes to restrict the run to — BOTH the "
             "deletes and the recomputation stay code-scoped, so a "
             "--force run only rewrites these codes' rows and every "
             "other code's data is untouched (perf smoke-testing; "
             "default: the whole active universe).",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force

    if args.forecast_metrics:
        requested = {s.strip() for s in args.forecast_metrics.split(",") if s.strip()}
        unknown = requested - set(STAGE_NAMES)
        if unknown:
            ap.error(f"--forecast-metrics: unknown stage(s) {sorted(unknown)} "
                     f"(choices: {', '.join(STAGE_NAMES)})")
        metrics = frozenset(requested)
    else:
        metrics = frozenset(STAGE_NAMES)
    codes_limit = args.limit
    codes_filter = ([c.strip() for c in args.codes.split(",") if c.strip()]
                    if args.codes else None)

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES
    # Display-only grid for the build header — each sec_type's ROLLING
    # LATEST key (its own latest available data date) is resolved per
    # sec_type inside the loop below.
    header_specs = build_stat_specs(n_years=args.years,
                                    latest_date=last_business_day())

    t0 = time.time()
    print_build_header(
        "ANALYZE FORECASTS (annual RSI-extreme + Bollinger-breach + "
        "MA/EMA-pair-cross + High/Low-streak + valuation "
        "PE/dividend-state forecasts)",
        tables=f"{TABLE_FORECAST}, {TABLE_IDENTITIES}, "
               f"{TABLE_MOV_RSI}, {TABLE_MOV_STD}, "
               f"{TABLE_MOV_PAIRS}, {TABLE_MOV_PAIRS_EMA}, "
               f"{TABLE_HIGH_LOW_STREAKS}, "
               f"{TABLE_MARGIN_RATIO}, "
               f"{TABLE_PE}, {TABLE_DIVIDEND}, {TABLE_BASE_RATE}",
        sec_types=", ".join(sec_types),
        months=f"{args.years} annual snapshots + the rolling latest "
               f"(window {header_specs[0].lower} .. "
               f"{header_specs[-1].stat_date})",
        mode="FORCE (delete + recompute all target snapshots)" if force
        else "incremental (missing snapshots + the mutable scope: the "
             "rolling latest snapshot and the newest year-end while "
             "its 20d forward windows realize)",
    )

    conn = await get_db_connection_async()
    try:
        # Search-only early exit: resolve one forecast_id against the
        # identities registry and print, without running the pipeline.
        if args.search_forecast_id is not None:
            await _search_forecast_identity(conn, args.search_forecast_id)
            return

        total_rsi = total_std = 0
        total_pairs = total_epairs = 0
        total_hstreaks = 0
        total_mratio = total_pe = total_div = total_base = 0
        for st in sec_types:
            latest = await fetch_latest_data_date(conn, st)
            if latest is None:
                logger.info(f"  [{st}] no source data; skipping.")
                continue
            specs = build_stat_specs(n_years=args.years, latest_date=latest)
            logger.info(f"  [{st}] rolling latest snapshot keyed at "
                        f"{latest.isoformat()} (the sec_type's latest "
                        f"available data date)")
            r, s, pr, ep, hs, mr, pe_n, div_n, b, scope_dates = \
                await _process_sec_type(conn, st, specs, force=force,
                                        metrics=metrics,
                                        codes_limit=codes_limit,
                                        codes_filter=codes_filter)
            # Per-code self-adaptive regime weights (evidence/display
            # tier — the study rejected them as the ordering key).
            from analyze.analysis_forecasts.regime_weights import (
                run_regime_weights,
            )
            await run_regime_weights(conn, st, force=force,
                                     stat_dates=scope_dates)
            total_rsi += r
            total_std += s
            total_pairs += pr
            total_epairs += ep
            total_hstreaks += hs
            total_mratio += mr
            total_pe += pe_n
            total_div += div_n
            total_base += b

            if r or (force and "rsi" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_RSI,
                    detail_name=ANALYSIS_NAME_RSI, description=DESCRIPTION_RSI,
                )
            if s or (force and "std" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_STD,
                    detail_name=ANALYSIS_NAME_STD, description=DESCRIPTION_STD,
                )
            if pr or (force and "pairs" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_MOV_PAIRS,
                    detail_name=ANALYSIS_NAME_MOV_PAIRS,
                    description=DESCRIPTION_MOV_PAIRS,
                )
            if ep or (force and "epairs" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_MOV_PAIRS_EMA,
                    detail_name=ANALYSIS_NAME_MOV_PAIRS_EMA,
                    description=DESCRIPTION_MOV_PAIRS_EMA,
                )
            if hs or (force and "hstreaks" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_HIGH_LOW_STREAKS,
                    detail_name=ANALYSIS_NAME_HIGH_LOW_STREAKS,
                    description=DESCRIPTION_HIGH_LOW_STREAKS,
                )
            if mr or (force and "mratio" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_MARGIN_RATIO,
                    detail_name=ANALYSIS_NAME_MARGIN_RATIO,
                    description=DESCRIPTION_MARGIN_RATIO,
                )
            if pe_n or (force and "pe" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_PE,
                    detail_name=ANALYSIS_NAME_PE,
                    description=DESCRIPTION_PE,
                )
            if div_n or (force and "div" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_DIVIDEND,
                    detail_name=ANALYSIS_NAME_DIVIDEND,
                    description=DESCRIPTION_DIVIDEND,
                )
            if b or (force and "base" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_BASE_RATE,
                    detail_name=ANALYSIS_NAME_BASE_RATE,
                    description=DESCRIPTION_BASE_RATE,
                )

        # ---- opp_pair stage retired 2026-09 (empty family; the table,
        #      its SQL file and the numpy wide pipeline it alone kept
        #      alive were removed with it) -------------------------------

        if total_rsi == 0 and total_std == 0 \
                and total_pairs == 0 and total_epairs == 0 \
\
                and total_hstreaks == 0 \
                and total_mratio == 0 \
                and total_pe == 0 and total_div == 0 \
                and total_base == 0 and not force:
            logger.info("\n  DB is up to date; nothing to do.")
            print_wall_time(t0)
            return

        logger.info(f"\n  TOTAL: {total_rsi:,} mov_rsi + {total_std:,} mov_std "
              f"+ {total_pairs:,} mov_pairs + "
              f"{total_epairs:,} mov_pairs_ema + "
              f"{total_hstreaks:,} high_low_streaks + "
              f"{total_mratio:,} margin_ratio + {total_pe:,} pe + "
              f"{total_div:,} dividend "
              f"written (with linked forecast_results rows) + "
              f"{total_base:,} base_rates rows")
        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
