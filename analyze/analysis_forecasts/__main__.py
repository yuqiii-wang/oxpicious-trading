"""Entry point for analyze.analysis_forecasts.

Run via ``python -m analyze.analysis_forecasts``.

Monthly per-security forecast analysis in the ``analysis_forecasts``
schema (see database/sql/analysis/analysis_forecasts/):

  - mov_rsi: per (sec_type, code, stat_month, rsi_window, side, pct,
    is_market_hyped) RSI extreme-percentile bucket definitions (RSI
    values join from analysis.mov_ave_rsi).

  - mov_std: per (sec_type, code, stat_month, ma_window, k, side,
    is_market_hyped) Bollinger-breach bucket definitions (band inputs
    join from analysis.mov_ave_spreads_detail / stats.*_tech_stats).

  - mov_pairs: per (sec_type, code, stat_month, fast_leg, pair_window,
    side, is_market_hyped) MA-pair CROSS (golden / death cross)
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
    (mean forward changes / reversal probabilities from the ANCHOR
    close) live in forecast_results via forecast_id; the config JSONB
    records the bucket's streak-length context (mean/min/max
    day_count).

  - base_rates: per (sec_type, code, stat_month, period) the
    UNCONDITIONAL same-window base rates (mean n-day forward change +
    the swing-aware reversal probability — P(the n-day forward window's
    adverse path extreme beyond ±reverse_threshold, i.e. the window
    SWUNG past the bar against the side at some close) over ALL of the
    code's window trading days, at the same adaptive bar as the
    buckets) — the reference the bucket results are read against
    (lift).

  - opp_pair_state: per (industry_id, pair_industry_id, stat_month,
    trend_window) industry opposite-PAIR buckets from
    analysis_composites.industry_corr_benchmark_offsets — when ONE
    industry's benchmark-offset trend is dropping (its W-day relative
    MA return below the benchmark's), the forecast RESULT is the OTHER
    side industry's forward offset trend (the linked forecast_results
    rows carry B's forward changes; side='bottom' → reverse_prob = the
    pair forecast's CONFIRMATION probability).

  - forecast_results: the result data (mean forward changes at
    next/5d/20d/60d horizons; close-based max/min ENDPOINT forward
    changes at the 5d/20d/60d horizons; per-horizon swing-aware reversal
    probabilities at each row's threshold — the period's
    adverse path extreme beyond ±thr, not merely the period-end close),
    keyed by forecast_id; every bucket carries 5 period rows — the four
    horizons plus the weight-blended 'mixed' row (config's
    MIXED_HORIZON_WEIGHTS: 5d 0.50 / next 0.30 / 20d 0.15 / 60d 0.05 —
    the row the analysis_signals confirmation gate reads, so every
    forecast horizon of the same signal trigger contributes to the
    signal); every mov_rsi / mov_std row links
    1:1 to its result rows via forecast_id.

Pipeline per sec_type (index / etf / stock):
  1. Fetch active-universe codes (recent-data pre-filter).
  2. Incremental: stat_months missing from each target table are
     computed, and the RUNNING month (the partial current-month
     snapshot, keyed at its month-end but computed only up to the
     latest available data date) plus the most recent REFRESH_MONTHS
     completed months are REFRESHED each run (deleted + recomputed —
     the running month's forward data grows daily, and a month written
     right after month-end carries permanently truncated 20d/60d
     occurrence counts because its forward windows were not complete
     yet).
     ``--force`` deletes the sec_type's mov_* rows AND their linked
     forecast_results rows (plus base_rates), then recomputes every
     target month.
  3. Fetch the joined long input frame (price / ma / rsi / std /
     ma5-vs-MA + ema6-vs-EMA spread columns; date >= earliest needed
     window start), the compact market-hype EPISODES list, and compute
     per-code forward changes (1/5/20/60 trading days).
  4. Scatter to (date × code) wide matrices + the market-hype flag
     matrix and run the vectorized monthly aggregation engines
     (compute_rsi / compute_std / compute_pairs /
     compute_high_low_streaks / compute_base), writing
     month-major batches: forecast_id allocated from the identity
     sequence, then COPY into forecast_results +
     forecast_identities (the shared-PK registry: sec_type / code /
     stat_month / bucket family per forecast_id — the search-by-id
     table) + the mov_* table in
     ONE transaction per month (no pre-clear DELETEs — months are only
     written when missing or after force/refresh deletion; atomicity
     keeps the 1:1 link crash-safe).
  5. Upsert analysis.analysis_identity.

Search by forecast_id: ``python -m analyze.analysis_forecasts
--search-forecast-id <id>`` resolves the id against
analysis_forecasts.forecast_identities and prints the identity + the
motivation row (no computation).

Row emission contract: buckets with day_count = 0 emit NO row (a code
without valid RSI / MA+std in a window simply has no buckets that
month); base-rate rows are emitted only where base_count > 0.
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
from datetime import date, timedelta

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
    TABLE_PX_VOL,
    TABLE_MARGIN_RATIO,
    TABLE_OPP_PAIR,
    TABLE_PE,
    TABLE_DIVIDEND,
    TABLE_BASE_RATE,
    ANALYSIS_NAME_RSI,
    ANALYSIS_NAME_STD,
    ANALYSIS_NAME_MOV_PAIRS,
    ANALYSIS_NAME_MOV_PAIRS_EMA,
    ANALYSIS_NAME_HIGH_LOW_STREAKS,
    ANALYSIS_NAME_PX_VOL,
    ANALYSIS_NAME_MARGIN_RATIO,
    ANALYSIS_NAME_OPP_PAIR,
    ANALYSIS_NAME_PE,
    ANALYSIS_NAME_DIVIDEND,
    ANALYSIS_NAME_BASE_RATE,
    DESCRIPTION_RSI,
    DESCRIPTION_STD,
    DESCRIPTION_MOV_PAIRS,
    DESCRIPTION_MOV_PAIRS_EMA,
    DESCRIPTION_HIGH_LOW_STREAKS,
    DESCRIPTION_PX_VOL,
    DESCRIPTION_MARGIN_RATIO,
    DESCRIPTION_OPP_PAIR,
    DESCRIPTION_PE,
    DESCRIPTION_DIVIDEND,
    DESCRIPTION_BASE_RATE,
    SEC_TYPES,
    MOV_RSI_COLUMNS,
    MOV_STD_COLUMNS,
    MOV_PAIRS_COLUMNS,
    MOV_PAIRS_EMA_COLUMNS,
    HIGH_LOW_STREAKS_COLUMNS,
    PX_VOL_COLUMNS,
    MARGIN_RATIO_COLUMNS,
    OPP_PAIR_COLUMNS,
    PE_COLUMNS,
    DIVIDEND_COLUMNS,
    RSI_WINDOWS,
    MA_WINDOWS,
    MOV_PAIRS_WINDOWS,
    MOV_PAIRS_EMA_WINDOWS,
    N_MONTHS,
    OPP_PAIR_BENCHMARK,
    OPP_PAIR_POOL_SIZE,
    OPP_PAIR_SEC_TYPE,
    OPP_PAIR_TREND_WINDOWS,
    REFRESH_MONTHS,
    WINDOW_YEARS,
    LOOKBACK_PERIOD,
)
from analyze.analysis_forecasts.fetch import (  # noqa: E402
    fetch_active_codes,
    fetch_analysis_inputs,
    fetch_first_dates,
    fetch_forecast_identity,
    fetch_hyped_episodes,
    fetch_high_low_streaks,
    fetch_price_vs_amt_states,
    assert_price_vs_amt_params,
    add_forward_changes,
    add_path_extremes,
    add_margin_ratio_features,
    add_valuation_features,
    fetch_benchmark_closes,
    fetch_industry_closes,
    fetch_industry_first_dates,
    fetch_opp_pair_industries,
    fetch_opp_pair_pairs,
)
from analyze.analysis_forecasts.wide import (  # noqa: E402
    build_month_specs,
    first_ords_from_dates,
    month_row_windows,
    split_forecast_rows,
    MonthSpec,
    _shift_years,
)
from analyze.analysis_forecasts.writer import (  # noqa: E402
    _compute_months,
    _delete_months,
    _delete_sec_type,
    _write_month,
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
from analyze.analysis_forecasts.compute_px_vol import compute_px_vol_results  # noqa: E402
from analyze.analysis_forecasts.compute_margin_ratio import compute_margin_ratio_results  # noqa: E402
from analyze.analysis_forecasts.compute_pe import compute_pe_results  # noqa: E402
from analyze.analysis_forecasts.compute_dividend import compute_dividend_results  # noqa: E402
from analyze.analysis_forecasts.compute_high_low_streaks import compute_high_low_streaks_results  # noqa: E402

# The per-metric stage keys --forecast-metrics accepts (opp_pair runs once outside
# the per-sec_type loop).
STAGE_NAMES = ("rsi", "std", "pairs", "epairs", "hstreaks",
               "pxvol", "mratio", "pe", "div", "base", "opp_pair")

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("analysis_forecasts")



async def _process_sec_type(
    conn,
    sec_type: str,
    specs: list[MonthSpec],
    *,
    force: bool,
    metrics: frozenset[str],
    codes_limit: int | None = None,
) -> tuple[int, ...]:
    """Process one sec_type end-to-end for the --forecast-metrics-selected metric
    stages (all stages when ``metrics`` is empty).
    Returns (mov_rsi, mov_std, mov_pairs, mov_pairs_ema,
    high_low_streaks, px_vol, margin_ratio, pe, dividend) bucket rows +
    base_rates."""
    logger.info(f"\n  [{sec_type}] Fetching active codes...")
    codes = sorted(await fetch_active_codes(conn, sec_type))
    if codes_limit is not None:
        codes = codes[:codes_limit]
        logger.info(f"  [{sec_type}]   limited to the first {codes_limit} "
              f"codes (--limit): {codes}")
    logger.info(f"  [{sec_type}]   {len(codes):,} active codes")
    if not codes:
        logger.info(f"  [{sec_type}]   no active codes; skipping.")
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, 0

    # ---- Emittable-month bound -------------------------------------------
    # The full-window gate (below) means a snapshot month can emit rows
    # only once the universe's EARLIEST first-data date precedes the
    # month's window start (≈ first data + WINDOW_YEARS). Without this
    # bound the earlier months are "missing" FOREVER (they can never
    # emit a row), so every incremental run re-targets them and drags
    # the input fetch back years before any code needs it.
    first_dates = await fetch_first_dates(conn, sec_type, codes)
    if first_dates:
        f_min = min(first_dates.values())
        n_all = len(specs)
        specs = [
            s for s in specs
            if _shift_years(s.stat_month, -WINDOW_YEARS)
            + timedelta(days=1) > f_min
        ]
        logger.info(f"  [{sec_type}]   universe first data {f_min.isoformat()} "
              f"→ {len(specs)} of {n_all} months can emit "
              f"(first data + {WINDOW_YEARS}y full-window gate)")

    # ---- Determine target months (incremental / force) -------------------
    # Per-stage gates: a stage not in ``metrics`` contributes no target
    # months (its list stays empty → its windows/body/upserts skip).
    if force:
        logger.info(f"  [{sec_type}] FORCE mode: deleting existing {sec_type} "
              f"rows + linked forecast_results + base_rates for the "
              f"selected stages...")
        await _delete_sec_type(conn, sec_type, metrics=metrics or None)
        compute_rsi = list(specs) if "rsi" in metrics else []
        compute_std = list(specs) if "std" in metrics else []
        compute_pairs = list(specs) if "pairs" in metrics else []
        compute_epairs = list(specs) if "epairs" in metrics else []
        compute_hstreaks = list(specs) if "hstreaks" in metrics else []
        compute_pxvol = list(specs) if "pxvol" in metrics else []
        compute_mratio = list(specs) if "mratio" in metrics else []
        compute_pe = list(specs) if "pe" in metrics else []
        compute_div = list(specs) if "div" in metrics else []
        compute_base = list(specs) if "base" in metrics else []
    else:
        compute_rsi, refresh_rsi = (await _compute_months(
            conn, TABLE_MOV_RSI, sec_type, specs)
            if "rsi" in metrics else ([], []))
        compute_std, refresh_std = (await _compute_months(
            conn, TABLE_MOV_STD, sec_type, specs)
            if "std" in metrics else ([], []))
        compute_pairs, refresh_pairs = (await _compute_months(
            conn, TABLE_MOV_PAIRS, sec_type, specs)
            if "pairs" in metrics else ([], []))
        compute_epairs, refresh_epairs = (await _compute_months(
            conn, TABLE_MOV_PAIRS_EMA, sec_type, specs)
            if "epairs" in metrics else ([], []))
        compute_hstreaks, refresh_hstreaks = (await _compute_months(
            conn, TABLE_HIGH_LOW_STREAKS, sec_type, specs)
            if "hstreaks" in metrics else ([], []))
        compute_pxvol, refresh_pxvol = (await _compute_months(
            conn, TABLE_PX_VOL, sec_type, specs)
            if "pxvol" in metrics else ([], []))
        compute_mratio, refresh_mratio = (await _compute_months(
            conn, TABLE_MARGIN_RATIO, sec_type, specs)
            if "mratio" in metrics else ([], []))
        compute_pe, refresh_pe = (await _compute_months(
            conn, TABLE_PE, sec_type, specs)
            if "pe" in metrics else ([], []))
        compute_div, refresh_div = (await _compute_months(
            conn, TABLE_DIVIDEND, sec_type, specs)
            if "div" in metrics else ([], []))
        compute_base, refresh_base = (await _compute_months(
            conn, TABLE_BASE_RATE, sec_type, specs)
            if "base" in metrics else ([], []))
        # Refresh-window months present in the DB: delete + recompute
        # (their long-horizon forward windows were not complete at
        # first write).
        if refresh_rsi:
            await _delete_months(conn, TABLE_MOV_RSI, sec_type,
                                 refresh_rsi, linked_results=True)
        if refresh_std:
            await _delete_months(conn, TABLE_MOV_STD, sec_type,
                                 refresh_std, linked_results=True)
        if refresh_pairs:
            await _delete_months(conn, TABLE_MOV_PAIRS, sec_type,
                                 refresh_pairs, linked_results=True)
        if refresh_epairs:
            await _delete_months(conn, TABLE_MOV_PAIRS_EMA, sec_type,
                                 refresh_epairs, linked_results=True)
        if refresh_hstreaks:
            await _delete_months(conn, TABLE_HIGH_LOW_STREAKS, sec_type,
                                 refresh_hstreaks, linked_results=True)
        if refresh_pxvol:
            await _delete_months(conn, TABLE_PX_VOL, sec_type,
                                 refresh_pxvol, linked_results=True)
        if refresh_mratio:
            await _delete_months(conn, TABLE_MARGIN_RATIO, sec_type,
                                 refresh_mratio, linked_results=True)
        if refresh_pe:
            await _delete_months(conn, TABLE_PE, sec_type,
                                 refresh_pe, linked_results=True)
        if refresh_div:
            await _delete_months(conn, TABLE_DIVIDEND, sec_type,
                                 refresh_div, linked_results=True)
        if refresh_base:
            await _delete_months(conn, TABLE_BASE_RATE, sec_type,
                                 refresh_base, linked_results=False)
        logger.info(f"  [{sec_type}]   months to compute: "
              f"rsi={len(compute_rsi)} std={len(compute_std)} "
              f"pairs={len(compute_pairs)} "
              f"epairs={len(compute_epairs)} "
              f"hstreaks={len(compute_hstreaks)} "
              f"pxvol={len(compute_pxvol)} "
              f"mratio={len(compute_mratio)} "
              f"pe={len(compute_pe)} div={len(compute_div)} "
              f"base={len(compute_base)} "
              f"of {len(specs)} (+ refresh of the last "
              f"{REFRESH_MONTHS})")
    if not compute_rsi and not compute_std \
            and not compute_pairs and not compute_epairs \
\
            and not compute_hstreaks and not compute_pxvol \
            and not compute_mratio and not compute_pe \
            and not compute_div and not compute_base:
        logger.info(f"  [{sec_type}]   up to date; skipping.")
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, 0

    # ---- Fetch inputs (bounded to the earliest needed window start) ------
    todo = {
        s.stat_month: s
        for s in compute_rsi + compute_std + compute_pairs
        + compute_epairs + compute_hstreaks + compute_pxvol
        + compute_mratio + compute_pe + compute_div + compute_base
    }
    since = min(s.lower for s in todo.values())
    logger.info(f"  [{sec_type}] Fetching joined inputs (price / ma / rsi / "
          f"std) for {len(codes):,} codes since "
          f"{since.isoformat()}...")
    df = await fetch_analysis_inputs(conn, sec_type, codes, since,
                                     families=set(metrics))
    logger.info(f"  [{sec_type}]   {len(df):,} (code, date) rows")
    if df.empty:
        logger.info(f"  [{sec_type}]   no source data; skipping.")
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
    episodes = await fetch_hyped_episodes(conn, sec_type, since)
    logger.info(f"  [{sec_type}]   {len(episodes):,} market-hype episodes")

    # ---- Long-frame feature adds (every family's shared input) -----------
    df = add_forward_changes(df)
    df = add_path_extremes(df)
    if "mratio" in metrics:
        df = add_margin_ratio_features(df)
    if "pe" in metrics or "div" in metrics:
        df = add_valuation_features(df)

    n_rsi = n_std = n_pairs = n_epairs = n_hstreaks = 0
    n_pxvol = n_mratio = n_pe = n_div = 0
    n_base = 0

    # ---- Stage 1: RSI extreme buckets (cudf-native df engine) -------------
    if compute_rsi:
        logger.info(f"  [{sec_type}] Computing RSI extreme buckets "
              f"(windows={list(RSI_WINDOWS)}) for {len(compute_rsi)} "
              f"months...")
        for stat_month, rows in compute_rsi_results(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_rsi,
        ):
            n = await _write_month(conn, TABLE_MOV_RSI, MOV_RSI_COLUMNS, rows)
            n_rsi += n
            logger.info(f"    [{stat_month}] mov_rsi + forecast_results: "
                  f"wrote {n:,} rows")

    # ---- Stage 2: Bollinger-breach buckets (cudf-native df engine) --------
    if compute_std:
        logger.info(f"  [{sec_type}] Computing Bollinger-breach buckets "
              f"(ma_windows={list(MA_WINDOWS)}) for {len(compute_std)} "
              f"months...")
        for stat_month, rows in compute_std_results(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_std,
        ):
            n = await _write_month(conn, TABLE_MOV_STD, MOV_STD_COLUMNS, rows)
            n_std += n
            logger.info(f"    [{stat_month}] mov_std + forecast_results: "
                  f"wrote {n:,} rows")

    # ---- Stage 3: MA-pair cross buckets (cudf-native df engine) -----------
    # CROSS-EVENT buckets on the EXISTING analysis.mov_ave_spreads_detail
    # ma5_vs_ma{W} spreads (fetched as pair_{W}): a trigger is the
    # spread's sign flip (golden / death cross); one-day signals.
    if compute_pairs:
        logger.info(f"  [{sec_type}] Computing MA-pair cross buckets "
              f"(pair_windows={list(MOV_PAIRS_WINDOWS)}) for "
              f"{len(compute_pairs)} months...")
        for stat_month, rows in compute_pairs_results(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_pairs,
        ):
            n = await _write_month(conn, TABLE_MOV_PAIRS,
                                   MOV_PAIRS_COLUMNS, rows)
            n_pairs += n
            logger.info(f"    [{stat_month}] mov_pairs + forecast_results: "
                  f"wrote {n:,} rows")

    # ---- Stage 5: EMA-pair cross buckets (cudf-native df engine) ----------
    # The identical cross machinery on the EXISTING
    # analysis.mov_ave_spreads_detail_ema ema6_vs_ema{W} spreads (fast
    # leg fixed ema6).
    if compute_epairs:
        logger.info(f"  [{sec_type}] Computing EMA-pair cross buckets "
              f"(pair_windows={list(MOV_PAIRS_EMA_WINDOWS)}) for "
              f"{len(compute_epairs)} months...")
        for stat_month, rows in compute_epairs_results(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_epairs,
        ):
            n = await _write_month(conn, TABLE_MOV_PAIRS_EMA,
                                   MOV_PAIRS_EMA_COLUMNS, rows)
            n_epairs += n
            logger.info(f"    [{stat_month}] mov_pairs_ema + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 6: High/Low streak mean-mid anchors (cudf-native) ----------
    # The streaks come from the EXISTING analysis.mov_ave_high_low_pct_
    # streaks table (run python -m analyze.mov_ave_spread first); every
    # streak is audited at its MEAN-MID anchor day, resolved against the
    # union trading-day calendar by the df engine.
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
                  f"streaks for {len(compute_hstreaks)} months...")
            for stat_month, rows in compute_high_low_streaks_results(
                df=df, first_dates=first_dates, episodes=episodes,
                codes=codes, sec_type=sec_type, specs=compute_hstreaks,
                streaks_df=streaks_df,
            ):
                n = await _write_month(
                    conn, TABLE_HIGH_LOW_STREAKS,
                    HIGH_LOW_STREAKS_COLUMNS, rows,
                )
                n_hstreaks += n
                logger.info(f"    [{stat_month}] high_low_streaks + "
                      f"forecast_results: wrote {n:,} rows")

    # ---- Stage 7: price × volume state buckets (cudf-native df engine) ----
    # The categories come from the analysis.mov_ave_price_vs_amt
    # REGISTRY (the px_vol family's date-level source of truth, built
    # by analyze.mov_ave_spread); the engines audit against the
    # recorded states instead of re-deriving them, and the registry's
    # recorded build parameters are verified against the engine
    # constants before consuming.
    if compute_pxvol:
        await assert_price_vs_amt_params(conn, sec_type)
        states_df = await fetch_price_vs_amt_states(
            conn, sec_type, codes, since
        )
        if states_df.empty:
            logger.info(f"  [{sec_type}] px_vol: no price_vs_amt registry "
                  f"rows (run python -m analyze.mov_ave_spread to build "
                  f"analysis.mov_ave_price_vs_amt); skipping.")
        else:
            logger.info(f"  [{sec_type}] Computing px_vol state buckets "
                  f"from {len(states_df):,} price_vs_amt registry rows "
                  f"(speeds×volumes, adaptive σ/z bars) for "
                  f"{len(compute_pxvol)} months...")
            for stat_month, rows in compute_px_vol_results(
                df=df, first_dates=first_dates, episodes=episodes,
                codes=codes, sec_type=sec_type, specs=compute_pxvol,
                states_df=states_df,
            ):
                n = await _write_month(conn, TABLE_PX_VOL,
                                       PX_VOL_COLUMNS, rows)
                n_pxvol += n
                logger.info(f"    [{stat_month}] px_vol_state + "
                      f"forecast_results: wrote {n:,} rows")

    # ---- Stage 8: margin-buy intensity states (cudf-native df engine) -----
    if compute_mratio:
        logger.info(f"  [{sec_type}] Computing margin_ratio state buckets "
              f"(融资买入额/成交额 z states) for {len(compute_mratio)} "
              f"months...")
        for stat_month, rows in compute_margin_ratio_results(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_mratio,
        ):
            n = await _write_month(conn, TABLE_MARGIN_RATIO,
                                   MARGIN_RATIO_COLUMNS, rows)
            n_mratio += n
            logger.info(f"    [{stat_month}] margin_ratio_state + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 9: valuation PE state buckets (cudf-native df engine) ------
    if compute_pe:
        logger.info(f"  [{sec_type}] Computing pe state buckets "
              f"(raw PE z states) for {len(compute_pe)} months...")
        for stat_month, rows in compute_pe_results(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_pe,
        ):
            n = await _write_month(conn, TABLE_PE, PE_COLUMNS, rows)
            n_pe += n
            logger.info(f"    [{stat_month}] pe_state + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 10: valuation dividend-yield states (cudf-native) ----------
    if compute_div:
        logger.info(f"  [{sec_type}] Computing dividend state buckets "
              f"(dividend_yield z states) for {len(compute_div)} "
              f"months...")
        for stat_month, rows in compute_dividend_results(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_div,
        ):
            n = await _write_month(conn, TABLE_DIVIDEND, DIVIDEND_COLUMNS,
                                   rows)
            n_div += n
            logger.info(f"    [{stat_month}] dividend_state + "
                  f"forecast_results: wrote {n:,} rows")

    # ---- Stage 11: unconditional base rates (cudf-native df engine) -------
    if compute_base:
        logger.info(f"  [{sec_type}] Computing base rates for "
              f"{len(compute_base)} months...")
        for stat_month, rows in compute_base_rate_rows(
            df=df, first_dates=first_dates, episodes=episodes,
            codes=codes, sec_type=sec_type, specs=compute_base,
        ):
            await copy_insert_async(conn, TABLE_BASE_RATE, rows)
            n_base += len(rows)
        logger.info(f"    base_rates: wrote {n_base:,} rows")

    return (n_rsi, n_std, n_pairs, n_epairs, n_hstreaks,
            n_pxvol, n_mratio, n_pe, n_div, n_base)


# ---------------------------------------------------------------------------
#  opp_pair pipeline (industry opposite-pair buckets — sec_type space is
#  the constant OPP_PAIR_SEC_TYPE, industries are NOT an active-code
#  universe; runs ONCE, outside the per-sec_type loop)
# ---------------------------------------------------------------------------

async def _process_opp_pairs(
    conn,
    specs: list[MonthSpec],
    *,
    force: bool,
) -> int:
    """Industry opposite-pair trend buckets (opp_pair_state + linked
    forecast_results). Returns bucket rows written."""
    if force:
        logger.info(f"\n  [opp_pair] FORCE mode: deleting existing opp_pair "
              f"rows + linked forecast_results...")
        await _delete_months(conn, TABLE_OPP_PAIR, OPP_PAIR_SEC_TYPE,
                             [s.stat_month for s in specs],
                             linked_results=True)
        compute_pair = list(specs)
    else:
        compute_pair, refresh_pair = await _compute_months(
            conn, TABLE_OPP_PAIR, OPP_PAIR_SEC_TYPE, specs)
        await _delete_months(conn, TABLE_OPP_PAIR, OPP_PAIR_SEC_TYPE,
                             refresh_pair, linked_results=True)
    logger.info(f"  [opp_pair]   months to compute: {len(compute_pair)} "
          f"of {len(specs)} (+ refresh of the last {REFRESH_MONTHS})")
    if not compute_pair:
        logger.info(f"  [opp_pair]   up to date; skipping.")
        return 0

    from analyze.analysis_forecasts.compute_opp_pair import (  # noqa: E402
        build_opp_pair_matrices,
        compute_opp_pair_results,
    )

    # ---- Pair set + industry universe -------------------------------------
    industries = await fetch_opp_pair_industries(conn)
    pairs = await fetch_opp_pair_pairs(conn)
    logger.info(f"  [opp_pair]   {len(industries)} industries, "
          f"{len(pairs):,} pairs (pool={OPP_PAIR_POOL_SIZE}, benchmark="
          f"{OPP_PAIR_BENCHMARK})")
    if len(industries) < 2 or pairs.empty:
        logger.info(f"  [opp_pair]   no offsets-table pairs; skipping.")
        return 0

    # ---- Industry composite + benchmark trend inputs ----------------------
    since = min(s.lower for s in compute_pair)
    df = await fetch_industry_closes(conn, industries, since)
    bench = await fetch_benchmark_closes(conn, OPP_PAIR_BENCHMARK, since)
    logger.info(f"  [opp_pair]   {len(df):,} industry (date, close) rows, "
          f"{len(bench):,} benchmark closes since {since.isoformat()}")
    if df.empty or bench.empty:
        logger.info(f"  [opp_pair]   no source data; skipping.")
        return 0

    grid_ord, grid_inds, didx, cidx, mats = build_opp_pair_matrices(
        df, bench, OPP_PAIR_TREND_WINDOWS,
    )
    first_dates = await fetch_industry_first_dates(conn, industries)
    first_ord = first_ords_from_dates(first_dates, grid_inds)
    windows = [
        w for w in month_row_windows(grid_ord, compute_pair)
        if w.lo < w.hi
    ]

    n_pair = 0
    logger.info(f"  [opp_pair] Computing opposite-pair buckets "
          f"(trend_windows={list(OPP_PAIR_TREND_WINDOWS)}) for "
          f"{len(windows)} months...")
    for stat_month, rows in compute_opp_pair_results(
        mats, windows, grid_inds, OPP_PAIR_SEC_TYPE, first_ord, pairs,
        benchmark_code=OPP_PAIR_BENCHMARK, pool_size=OPP_PAIR_POOL_SIZE,
        grid_ord=grid_ord,
    ):
        n = await _write_month(conn, TABLE_OPP_PAIR, OPP_PAIR_COLUMNS, rows)
        n_pair += n
        logger.info(f"    [{stat_month}] opp_pair_state + forecast_results: "
              f"wrote {n:,} rows")
    return n_pair


# ---------------------------------------------------------------------------
#  Search by forecast_id (analysis_forecasts.forecast_identities registry)
# ---------------------------------------------------------------------------

async def _search_forecast_identity(conn, forecast_id: int) -> None:
    """Print one forecast bucket's registry identity — the shared PK
    (sec_type, code, stat_month) + bucket family from
    forecast_identities, plus the full motivation row joined from the
    family's table."""
    ident = await fetch_forecast_identity(conn, forecast_id)
    if ident is None:
        logger.info(f"forecast_id {forecast_id}: not found in "
                    f"{TABLE_IDENTITIES}")
        return
    logger.info(f"forecast_id {forecast_id}:")
    for key in ("sec_type", "code", "stat_month", "bucket",
                "streak_signal_days", "lookback_period"):
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
                    "dividend-yield z-state buckets over analysis.pe / "
                    "analysis.dividends — OPPOSITE side mappings: PE "
                    "lower-the-better → high-PE states bearish/top; "
                    "dividend yield higher-the-better → high-yield "
                    "states bullish/bottom) hold the motivation cols; "
                    "forecast_results (linked "
                    "1:1 via forecast_id) holds the result data — mean "
                    "forward changes at next/5d/20d/60d horizons; "
                    "close-based max/min ENDPOINT forward changes at "
                    "the 5d/20d/60d horizons; per-horizon swing-aware "
                    "reversal probabilities (the period's adverse path "
                    "extreme beyond the reverse_threshold) at each "
                    "row's bar; base_rates "
                    "holds the unconditional same-window reference "
                    "(mean change + P(path extreme beyond "
                    "±reverse_threshold) over "
                    "all window days); opp_pair_state holds the "
                    "industry opposite-pair buckets (one industry "
                    "dropping → the OTHER side industry's forward "
                    "offset trend as the forecast result)."
    )
    ap.add_argument(
        "--sec-type", choices=SEC_TYPES, default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    ap.add_argument(
        "--months", type=int, default=N_MONTHS,
        help=f"Number of completed month-end snapshots to target "
             f"(default {N_MONTHS} = 5 years, monthly); the RUNNING "
             f"month (computed up to today) is always appended.",
    )
    ap.add_argument(
        "--search-forecast-id", type=int, default=None, metavar="ID",
        help="Print the forecast bucket's registry identity "
             "(analysis_forecasts.forecast_identities: sec_type / code / "
             "stat_month / bucket family + the motivation row) and exit "
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
             "(smoke-testing aid; default: no cap).",
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

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES
    specs = build_month_specs(n_months=args.months)

    t0 = time.time()
    print_build_header(
        "ANALYZE FORECASTS (monthly RSI-extreme + Bollinger-breach + "
        "MA/EMA-pair-cross + High/Low-streak + valuation "
        "PE/dividend-state + industry opposite-pair forecasts)",
        tables=f"{TABLE_FORECAST}, {TABLE_IDENTITIES}, "
               f"{TABLE_MOV_RSI}, {TABLE_MOV_STD}, "
               f"{TABLE_MOV_PAIRS}, {TABLE_MOV_PAIRS_EMA}, "
               f"{TABLE_HIGH_LOW_STREAKS}, "
               f"{TABLE_PX_VOL}, {TABLE_MARGIN_RATIO}, {TABLE_OPP_PAIR}, "
               f"{TABLE_PE}, {TABLE_DIVIDEND}, {TABLE_BASE_RATE}",
        sec_types=", ".join(sec_types),
        months=f"{args.months} (window {specs[0].lower} .. "
               f"{specs[-1].stat_month})",
        mode="FORCE (delete + recompute all target months)" if force
        else f"incremental (missing stat_months + refresh of the "
             f"running month and the last {REFRESH_MONTHS - 1} "
             f"completed months)",
    )

    conn = await get_db_connection_async()
    try:
        # Search-only early exit: resolve one forecast_id against the
        # identities registry and print, without running the pipeline.
        if args.search_forecast_id is not None:
            await _search_forecast_identity(conn, args.search_forecast_id)
            return

        total_rsi = total_std = total_pxvol = 0
        total_pairs = total_epairs = 0
        total_hstreaks = 0
        total_mratio = total_pe = total_div = total_base = 0
        for st in sec_types:
            r, s, pr, ep, hs, p, mr, pe_n, div_n, b = \
                await _process_sec_type(conn, st, specs, force=force,
                                        metrics=metrics, codes_limit=codes_limit)
            total_rsi += r
            total_std += s
            total_pairs += pr
            total_epairs += ep
            total_hstreaks += hs
            total_pxvol += p
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
            if p or (force and "pxvol" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_PX_VOL,
                    detail_name=ANALYSIS_NAME_PX_VOL,
                    description=DESCRIPTION_PX_VOL,
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

        # ---- opp_pair stage (industry pairs; index-space, runs once) ------
        n_pair = 0
        if "opp_pair" in metrics and (not args.sec_type or args.sec_type == OPP_PAIR_SEC_TYPE):
            n_pair = await _process_opp_pairs(conn, specs, force=force)
            if n_pair or (force and "opp_pair" in metrics):
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_OPP_PAIR,
                    detail_name=ANALYSIS_NAME_OPP_PAIR,
                    description=DESCRIPTION_OPP_PAIR,
                )

        if total_rsi == 0 and total_std == 0 \
                and total_pairs == 0 and total_epairs == 0 \
\
                and total_hstreaks == 0 \
                and total_pxvol == 0 and total_mratio == 0 \
                and total_pe == 0 and total_div == 0 \
                and total_base == 0 and n_pair == 0 and not force:
            logger.info("\n  DB is up to date; nothing to do.")
            print_wall_time(t0)
            return

        logger.info(f"\n  TOTAL: {total_rsi:,} mov_rsi + {total_std:,} mov_std "
              f"+ {total_pairs:,} mov_pairs + "
              f"{total_epairs:,} mov_pairs_ema + "
              f"{total_hstreaks:,} high_low_streaks + "
              f"{total_pxvol:,} px_vol + "
              f"{total_mratio:,} margin_ratio + {total_pe:,} pe + "
              f"{total_div:,} dividend "
              f"+ {n_pair:,} opp_pair rows "
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
