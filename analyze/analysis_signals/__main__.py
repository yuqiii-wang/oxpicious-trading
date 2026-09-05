"""Entry point for analyze.analysis_signals.

Run via ``python -m analyze.analysis_signals``.

Per-DAY trading signals in the ``analysis_signals`` schema (see
database/sql/analysis/analysis_signals/): one row per
(code, sec_type, signal_type, signal_sub_type, date) with the crossed
threshold (signal_threshold), a human-readable reason, the full
detection params (JSON) and the action:

  - mov_rsi (sub_type rsi{W}): rsi_{W}days in the top 1% (action=sell)
    or bottom 1% (action=buy) of the trailing 5-year window ending at
    the snapshot month; W mirrors analysis.mov_ave_rsi.
  - mov_std (sub_type std{W}): price beyond the 2σ Bollinger band
    ma_{W} ± 2.0·std_{W}days (upper → sell, lower → buy).
  - opp_pair (sub_type pair{W}): by INDUSTRY pair (buckets
    analysis_forecasts.opp_pair_state) — when ONE side industry's
    benchmark-offset MA trend crosses below the 0 bar (its W-day
    relative MA return < the benchmark's), a BUY row is emitted on the
    OTHER side industry (the forecast target; side='bottom'). Months
    are gated to analysis_forecasts.opp_pair_state, the gate
    calibration groups by the TARGET industry (gate code_col), and the
    stage runs ONCE outside the per-sec_type loop.

Cooperation with analyze.analysis_forecasts (the gates are read, never
recomputed here):
  1. Target stat_months = the months ALREADY PRESENT in
     analysis_forecasts.mov_rsi (at pct = 1) / mov_std (at k = 2.0)
     for the sec_type — the forecasts' start month sets the first
     signal date.
  2. Incremental: a target month is computed only when
     analysis_signals.signals has no rows for it yet (month-level
     DISTINCT check per signal_type; months are written atomically in
     ONE transaction, so a crash can never leave a half-written
     month). ``--force`` deletes the sec_type's signal rows and
     recomputes every target month.
  3. Detection reuses the forecast machinery: the same trailing 5y
     window (M - 5y, M], linear-interpolated window percentile
     thresholds (RSI) / band levels (std), cooldown suppression and
     full-5y-history gate (first data strictly before the window
     start). Each date is emitted only within its own snapshot month.
  4. Forecast-confirmation gate (gate.fetch_confirm): a detected day
     is RECORDED only when the matching forecast bucket (same
     code/sec_type/stat_month/window/side/pct|k/cooldown config)
     qualifies — in ANY forecast_results period (next / 5d / 20d /
     60d) that period's reverse_prob clears its calibrated M-1
     threshold (QRp_P90 / HYB QRp_P90, see gate.py; legacy
     reverse_prob > 0 below GATE_MIN_POP population bucket-periods)
     AND the code's prior mean reverse_prob for that period is
     positive where known (the mean sees reverse too), read from
     analysis_forecasts.forecast_results via the bucket's forecast_id
     (the probabilities are NOT stored on the signal row; the row's
     confidence = MAX(reverse_prob) across all periods). Detection
     stays identical to the buckets; the gate only filters which days
     get written. NOTE: months written by an earlier (ungated) build
     keep their rows — ``--force`` rebuilds them under the gate.

``--live`` additionally runs the day-close mirror
(live_close.mirror_live_close): every signal row not yet recorded gets
one live.live_signals observation at the session close (time 15:00:00,
is_day_close_trigger = TRUE) — mov_std close vs band level, mov_rsi
day RSI vs threshold; PK-checked, so re-running backfills exactly the
missing rows. The signal pipeline itself stays incremental either way.

Pipeline per sec_type (index / etf / stock):
  1. Fetch active-universe codes + true first-data dates.
  2. Resolve target months (forecast presence − signal presence).
  3. Fetch the joined long input frame (price / ma / rsi / std; date >=
     earliest needed window start) and scatter to (date × code) wide
     matrices.
  4. Fetch the reverse-confirmed code sets from forecast_results.
  5. Run the vectorized signal engines (compute_rsi_signals /
     compute_std_signals), writing month-major, one transaction per
     month.
  6. Upsert analysis.analysis_identity; with --live, mirror day-close
     observations into live.live_signals.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import os
import sys
import time
from datetime import date, timedelta

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.analysis_signals`` or as a script.
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

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate  # noqa: E402
activate()

from analyze._common.identity import upsert_analysis_identity  # noqa: E402
from analyze.analysis_forecasts.config import (  # noqa: E402
    GAP_WINDOWS,
    MA_WINDOWS,
    OPP_PAIR_BENCHMARK,
    OPP_PAIR_SEC_TYPE,
    RSI_WINDOWS,
    SEC_TYPES,
    WINDOW_YEARS,
)
from analyze.analysis_forecasts.fetch import (  # noqa: E402
    fetch_active_codes,
    fetch_analysis_inputs,
    fetch_benchmark_closes,
    fetch_first_dates,
    fetch_industry_closes,
    fetch_industry_first_dates,
    fetch_opp_pair_industries,
    fetch_opp_pair_pairs,
    add_px_vol_features,
    add_margin_ratio_features,
)
from analyze.analysis_forecasts.wide import (  # noqa: E402
    MonthSpec,
    _shift_years,
    build_grid,
    first_ords_from_dates,
    month_row_windows,
    scatter_column,
)
from analyze.analysis_forecasts.compute_opp_pair import (  # noqa: E402
    build_opp_pair_matrices,
)
from analyze.analysis_signals.config import (  # noqa: E402
    ANALYSIS_NAME,
    DESCRIPTION,
    DETAIL_NAME,
    GAP_PCT,
    GATE_Q,
    K_SHRINK,
    MARGIN_RATIO_GATE_HYBRID,
    MARGIN_RATIO_SIGNAL_STATES,
    OPP_PAIR_GATE_HYBRID,
    PX_VOL_GATE_HYBRID,
    RSI_PCT,
    SIGNAL_COLUMNS,
    SIGNAL_TYPE_MARGIN_RATIO,
    SIGNAL_TYPE_OPP_PAIR,
    SIGNAL_TYPE_PX_VOL,
    STD_K,
    TABLE_SIGNALS,
)
from analyze.analysis_signals.signals import (  # noqa: E402
    compute_gap_signals,
    compute_margin_ratio_signals,
    compute_opp_pair_signals,
    compute_px_vol_signals,
    compute_rsi_signals,
    compute_std_signals,
)
from analyze.analysis_signals.gate import fetch_confirm  # noqa: E402
from analyze.analysis_signals.live_close import (  # noqa: E402
    mirror_live_close,
)


# Max rows per COPY chunk (a full stock-universe month of signals is
# bounded by ~1% of trading days × codes × sub_types; chunk to bound
# peak memory).
_WRITE_CHUNK = 100_000


# ---------------------------------------------------------------------------
#  Month gates (forecast presence → target months; signal presence →
#  missing months)
# ---------------------------------------------------------------------------

async def _forecast_present_months(
    conn,
    sec_type: str,
) -> tuple[list[date], list[date], list[date], list[date], list[date]]:
    """Stat_months present in analysis_forecasts for the matching
    signal configs: mov_rsi at pct = RSI_PCT (any side/window),
    mov_std at k = STD_K (any side/window), mov_gap at pct = GAP_PCT
    (any side/window), px_vol_state (any sided cell) and
    margin_ratio_state (any signal-emitting z state)."""
    rsi_rows = await conn.fetch(
        f"SELECT DISTINCT stat_month FROM analysis_forecasts.mov_rsi "
        f"WHERE sec_type = $1 AND pct = {RSI_PCT}",
        sec_type,
    )
    std_rows = await conn.fetch(
        f"SELECT DISTINCT stat_month FROM analysis_forecasts.mov_std "
        f"WHERE sec_type = $1 AND k::float8 = {STD_K!r}",
        sec_type,
    )
    gap_rows = await conn.fetch(
        f"SELECT DISTINCT stat_month FROM analysis_forecasts.mov_gap "
        f"WHERE sec_type = $1 AND pct = {GAP_PCT}",
        sec_type,
    )
    pxvol_rows = await conn.fetch(
        "SELECT DISTINCT stat_month FROM analysis_forecasts.px_vol_state "
        "WHERE sec_type = $1 AND px_speed <> 'flat'",
        sec_type,
    )
    mratio_rows = await conn.fetch(
        "SELECT DISTINCT stat_month FROM "
        "analysis_forecasts.margin_ratio_state "
        f"WHERE sec_type = $1 AND ratio_state IN {MARGIN_RATIO_SIGNAL_STATES!r}",
        sec_type,
    )
    return (
        sorted(r["stat_month"] for r in rsi_rows),
        sorted(r["stat_month"] for r in std_rows),
        sorted(r["stat_month"] for r in gap_rows),
        sorted(r["stat_month"] for r in pxvol_rows),
        sorted(r["stat_month"] for r in mratio_rows),
    )


# ---------------------------------------------------------------------------
#  Adaptive forecast-confirmation gate ("strong reverse signal") — see
#  gate.py: the confirmed-code sets are fetched from analysis_forecasts
#  with the self-adaptive QRp_P90 population-quantile threshold plus the
#  mean-reversal conjunct (the code's prior mean rp must also be
#  positive where known).
# ---------------------------------------------------------------------------


async def _signal_present_months(
    conn,
    sec_type: str,
    signal_type: str,
) -> set[date]:
    """Snapshot months that already have signal rows for this
    sec_type + signal_type (month-level DISTINCT on the date PK)."""
    rows = await conn.fetch(
        f"SELECT DISTINCT date_trunc('month', date)::date AS m "
        f"FROM {TABLE_SIGNALS} "
        f"WHERE sec_type = $1 AND signal_type = $2",
        sec_type,
        signal_type,
    )
    return {r["m"] for r in rows}


def _specs_for(months: list[date]) -> list[MonthSpec]:
    """Target months (ascending) → MonthSpec list with the inclusive
    trailing-window start (month-end - WINDOW_YEARS + 1 day)."""
    return [
        MonthSpec(m, _shift_years(m, -WINDOW_YEARS) + timedelta(days=1), m)
        for m in months
    ]


# ---------------------------------------------------------------------------
#  Month write (one atomic transaction per month — the month-granular
#  incremental detection relies on it)
# ---------------------------------------------------------------------------

async def _write_month(conn, rows: list[dict]) -> int:
    """Write one month-batch of signal rows. Pure COPY, no pre-clear:
    a month reaches this function only when missing from the table
    (incremental) or after --force deleted the sec_type's rows. The
    WHOLE month is written in one transaction (chunked COPYs become
    savepoints), so a crash rolls back the month together — the
    DISTINCT-month detection never sees a half-written month."""
    if not rows:
        return 0
    async with conn.transaction():
        for i in range(0, len(rows), _WRITE_CHUNK):
            await copy_insert_async(
                conn, TABLE_SIGNALS, rows[i : i + _WRITE_CHUNK],
                columns=SIGNAL_COLUMNS,
            )
    return len(rows)


# ---------------------------------------------------------------------------
#  is_active refresh (latest-date flag)
# ---------------------------------------------------------------------------

async def _refresh_is_active(conn, sec_type: str) -> None:
    """Flip the is_active flag for one sec_type: TRUE only on the
    sec_type's LATEST signal date (max(date) — the latest date the run
    wrote), FALSE everywhere else. Two narrow UPDATEs (only rows actually
    changing state are rewritten); no-op when the sec_type has no rows.
    Runs after EVERY run (incremental or force) so the invariant
    "exactly one active date per sec_type" self-heals."""
    row = await conn.fetchrow(
        f"SELECT max(date) AS d FROM {TABLE_SIGNALS} WHERE sec_type = $1",
        sec_type,
    )
    d = row["d"] if row else None
    if d is None:
        return
    await conn.execute(
        f"UPDATE {TABLE_SIGNALS} SET is_active = TRUE "
        f"WHERE sec_type = $1 AND date = $2 AND NOT is_active",
        sec_type, d,
    )
    await conn.execute(
        f"UPDATE {TABLE_SIGNALS} SET is_active = FALSE "
        f"WHERE sec_type = $1 AND is_active AND date <> $2",
        sec_type, d,
    )


# ---------------------------------------------------------------------------
#  Per-sec_type pipeline
# ---------------------------------------------------------------------------

async def _process_sec_type(
    conn,
    sec_type: str,
    *,
    force: bool,
) -> tuple[int, int, int, int, int]:
    """Process one sec_type end-to-end. Returns (mov_rsi, mov_std,
    mov_gap, px_vol, margin_ratio) signal rows written."""
    print(f"\n  [{sec_type}] Fetching active codes...", flush=True)
    codes = sorted(await fetch_active_codes(conn, sec_type))
    print(f"  [{sec_type}]   {len(codes):,} active codes", flush=True)
    if not codes:
        print(f"  [{sec_type}]   no active codes; skipping.", flush=True)
        return 0, 0, 0, 0, 0

    # ---- Target months: forecast presence gate ---------------------------
    fc_rsi, fc_std, fc_gap, fc_pxvol, fc_mratio = \
        await _forecast_present_months(conn, sec_type)
    print(f"  [{sec_type}]   forecast-gated months: rsi={len(fc_rsi)} "
          f"std={len(fc_std)} gap={len(fc_gap)} pxvol={len(fc_pxvol)} "
          f"mratio={len(fc_mratio)}", flush=True)
    if not fc_rsi and not fc_std and not fc_gap and not fc_pxvol \
            and not fc_mratio:
        print(f"  [{sec_type}]   no analysis_forecasts data yet; "
              f"skipping.", flush=True)
        return 0, 0, 0, 0, 0

    # ---- Missing months (incremental / force) -----------------------------
    if force:
        print(f"  [{sec_type}] FORCE mode: deleting existing {sec_type} "
              f"signal rows...", flush=True)
        await conn.execute(
            f"DELETE FROM {TABLE_SIGNALS} WHERE sec_type = $1", sec_type
        )
        missing_rsi, missing_std, missing_gap, missing_pxvol, \
            missing_mratio = fc_rsi, fc_std, fc_gap, fc_pxvol, fc_mratio
    else:
        present_rsi = await _signal_present_months(conn, sec_type, "mov_rsi")
        present_std = await _signal_present_months(conn, sec_type, "mov_std")
        present_gap = await _signal_present_months(conn, sec_type, "mov_gap")
        present_pxvol = await _signal_present_months(
            conn, sec_type, SIGNAL_TYPE_PX_VOL)
        present_mratio = await _signal_present_months(
            conn, sec_type, SIGNAL_TYPE_MARGIN_RATIO)
        # forecast months are month-ENDS; signal presence is month-STARTS
        # (date_trunc) — compare on the truncated month.
        missing_rsi = [m for m in fc_rsi if m.replace(day=1) not in present_rsi]
        missing_std = [m for m in fc_std if m.replace(day=1) not in present_std]
        missing_gap = [m for m in fc_gap if m.replace(day=1) not in present_gap]
        missing_pxvol = [
            m for m in fc_pxvol if m.replace(day=1) not in present_pxvol
        ]
        missing_mratio = [
            m for m in fc_mratio if m.replace(day=1) not in present_mratio
        ]
        print(f"  [{sec_type}]   missing months: rsi={len(missing_rsi)} "
              f"std={len(missing_std)} gap={len(missing_gap)} "
              f"pxvol={len(missing_pxvol)} "
              f"mratio={len(missing_mratio)}", flush=True)
    if not missing_rsi and not missing_std and not missing_gap \
            and not missing_pxvol and not missing_mratio:
        print(f"  [{sec_type}]   up to date; skipping.", flush=True)
        return 0, 0, 0, 0, 0

    # ---- Adaptive confirmation gate: confirmed codes per config --------
    # mov_rsi: SEC QRp_P90 (rp-saturated family — per-security
    # differentiation lands in the tier columns); mov_std / mov_gap:
    # HYB QRp_P90 (per-code shrinkage blend, see gate.py).
    confirm_rsi = (
        await fetch_confirm(
            conn, sec_type, missing_rsi,
            "analysis_forecasts.mov_rsi", "rsi_window",
            f"m.pct = {RSI_PCT}", lambda w: f"rsi_{w}",
            hybrid=False,
        )
        if missing_rsi else {}
    )
    confirm_std = (
        await fetch_confirm(
            conn, sec_type, missing_std,
            "analysis_forecasts.mov_std", "ma_window",
            f"m.k::float8 = {STD_K!r}", lambda w: f"ma_{w}",
            hybrid=True,
        )
        if missing_std else {}
    )
    confirm_gap = (
        await fetch_confirm(
            conn, sec_type, missing_gap,
            "analysis_forecasts.mov_gap", "gap_window",
            f"m.pct = {GAP_PCT}", lambda w: f"gap_{w}",
            hybrid=True,
        )
        if missing_gap else {}
    )
    confirm_pxvol = (
        await fetch_confirm(
            conn, sec_type, missing_pxvol,
            "analysis_forecasts.px_vol_state", "px_speed",
            "m.px_speed <> 'flat'", lambda s: s,
            hybrid=PX_VOL_GATE_HYBRID,
        )
        if missing_pxvol else {}
    )
    confirm_mratio = (
        await fetch_confirm(
            conn, sec_type, missing_mratio,
            "analysis_forecasts.margin_ratio_state", "ratio_state",
            "m.ratio_state IN ('vlow', 'low', 'high', 'vhigh')",
            lambda s: s,
            hybrid=MARGIN_RATIO_GATE_HYBRID,
        )
        if missing_mratio else {}
    )
    print(f"  [{sec_type}]   gate-confirmed configs: rsi={len(confirm_rsi)} "
          f"(SEC QRp_P{int(100 * GATE_Q)}), std={len(confirm_std)}, "
          f"gap={len(confirm_gap)}, pxvol={len(confirm_pxvol)}, "
          f"mratio={len(confirm_mratio)} "
          f"(HYB QRp_P{int(100 * GATE_Q)}, "
          f"K={K_SHRINK}) — month x window x side combos clearing the "
          f"calibrated threshold over prior months, with per-code "
          f"tier/baseline/rank calibration", flush=True)

    # ---- Fetch inputs (bounded to the earliest needed window start) ------
    todo_months = sorted(set(missing_rsi) | set(missing_std)
                         | set(missing_gap) | set(missing_pxvol)
                         | set(missing_mratio))
    since = min(_specs_for(todo_months), key=lambda s: s.lower).lower
    print(f"  [{sec_type}] Fetching joined inputs (price / ma / rsi / "
          f"gap / std / trading_amount) for {len(codes):,} codes since "
          f"{since.isoformat()}...", flush=True)
    df = await fetch_analysis_inputs(conn, sec_type, codes, since)
    print(f"  [{sec_type}]   {len(df):,} (code, date) rows", flush=True)
    if df.empty:
        print(f"  [{sec_type}]   no source data; skipping.", flush=True)
        return 0, 0, 0, 0, 0
    df = add_px_vol_features(df)
    df = add_margin_ratio_features(df)

    # ---- Wide grid + per-code first-data gate ------------------------------
    grid_ord, grid_codes, didx, cidx = build_grid(df)
    shape = (len(grid_ord), len(grid_codes))
    first_dates = await fetch_first_dates(conn, sec_type, codes)
    first_ord = first_ords_from_dates(first_dates, grid_codes)

    n_rsi = n_std = n_gap = n_pxvol = n_mratio = 0

    # ---- Stage 1: RSI extreme signals --------------------------------------
    if missing_rsi:
        windows_rsi = month_row_windows(grid_ord, _specs_for(missing_rsi))
        print(f"  [{sec_type}] Computing RSI signals (top/bottom "
              f"{RSI_PCT}%) for {len(windows_rsi)} months...", flush=True)
        rsi_mats = {
            f"rsi_{w}": scatter_column(df, f"rsi_{w}days", shape, didx, cidx)
            for w in RSI_WINDOWS
        }
        for stat_month, rows in compute_rsi_signals(
            rsi_mats, windows_rsi, grid_codes, sec_type, first_ord,
            grid_ord, confirm_rsi,
        ):
            n = await _write_month(conn, rows)
            n_rsi += n
            print(f"    [{stat_month}] mov_rsi signals: wrote {n:,} rows",
                  flush=True)
        del rsi_mats

    # ---- Stage 2: Bollinger-breach signals ----------------------------------
    if missing_std:
        windows_std = month_row_windows(grid_ord, _specs_for(missing_std))
        print(f"  [{sec_type}] Computing Bollinger-breach signals "
              f"(±{STD_K:g}σ) for {len(windows_std)} months...", flush=True)
        std_mats: dict = {
            "price": scatter_column(df, "price", shape, didx, cidx),
        }
        for w in MA_WINDOWS:
            std_mats[f"ma_{w}"] = scatter_column(df, f"ma_{w}days", shape,
                                                 didx, cidx)
            std_mats[f"std_{w}"] = scatter_column(df, f"std_{w}days", shape,
                                                  didx, cidx)
        for stat_month, rows in compute_std_signals(
            std_mats, windows_std, grid_codes, sec_type, first_ord,
            grid_ord, confirm_std,
        ):
            n = await _write_month(conn, rows)
            n_std += n
            print(f"    [{stat_month}] mov_std signals: wrote {n:,} rows",
                  flush=True)
        del std_mats

    # ---- Stage 3: gap extreme signals ---------------------------------------
    if missing_gap:
        windows_gap = month_row_windows(grid_ord, _specs_for(missing_gap))
        print(f"  [{sec_type}] Computing gap signals (top/bottom "
              f"{GAP_PCT}%) for {len(windows_gap)} months...", flush=True)
        gap_mats = {
            f"gap_{w}": scatter_column(df, f"gap_{w}days", shape, didx, cidx)
            for w in GAP_WINDOWS
        }
        for stat_month, rows in compute_gap_signals(
            gap_mats, windows_gap, grid_codes, sec_type, first_ord,
            grid_ord, confirm_gap,
        ):
            n = await _write_month(conn, rows)
            n_gap += n
            print(f"    [{stat_month}] mov_gap signals: wrote {n:,} rows",
                  flush=True)
        del gap_mats

    # ---- Stage 4: px_vol state signals ---------------------------------------
    if missing_pxvol:
        windows_pxvol = month_row_windows(grid_ord, _specs_for(missing_pxvol))
        print(f"  [{sec_type}] Computing px_vol signals (adaptive σ/z "
              f"state cells) for {len(windows_pxvol)} months...",
              flush=True)
        px_mats = {
            "t": scatter_column(df, "px_t", shape, didx, cidx),
            "z": scatter_column(df, "px_z", shape, didx, cidx),
        }
        for stat_month, rows in compute_px_vol_signals(
            px_mats, windows_pxvol, grid_codes, sec_type, first_ord,
            grid_ord, confirm_pxvol,
        ):
            n = await _write_month(conn, rows)
            n_pxvol += n
            print(f"    [{stat_month}] px_vol signals: wrote {n:,} rows",
                  flush=True)
        del px_mats

    # ---- Stage 5: margin_ratio state signals --------------------------------
    if missing_mratio:
        windows_mratio = month_row_windows(
            grid_ord, _specs_for(missing_mratio))
        print(f"  [{sec_type}] Computing margin_ratio signals (margin-buy "
              f"intensity z states) for {len(windows_mratio)} months...",
              flush=True)
        mr_mats = {
            "z": scatter_column(df, "ratio_z", shape, didx, cidx),
        }
        for stat_month, rows in compute_margin_ratio_signals(
            mr_mats, windows_mratio, grid_codes, sec_type, first_ord,
            grid_ord, confirm_mratio,
        ):
            n = await _write_month(conn, rows)
            n_mratio += n
            print(f"    [{stat_month}] margin_ratio signals: wrote "
                  f"{n:,} rows", flush=True)
        del mr_mats

    return n_rsi, n_std, n_gap, n_pxvol, n_mratio


# ---------------------------------------------------------------------------
#  opp_pair stage (industry opposite-pair signals — rows are emitted on
#  the TARGET industry with the constant sec_type 'index'; industries
#  are NOT an active-code universe, so this runs ONCE, outside the
#  per-sec_type loop)
# ---------------------------------------------------------------------------

async def _process_opp_pair_signals(conn, *, force: bool) -> int:
    """opp_pair family: a paired industry's benchmark-offset trend
    crossing below the 0 bar emits a buy row on the OTHER side industry
    (see signals/opp_pair.py). Months are gated to the stat_months
    already present in analysis_forecasts.opp_pair_state. Returns signal
    rows written."""
    fc_rows = await conn.fetch(
        "SELECT DISTINCT stat_month FROM "
        "analysis_forecasts.opp_pair_state WHERE sec_type = $1",
        OPP_PAIR_SEC_TYPE,
    )
    fc = sorted(r["stat_month"] for r in fc_rows)
    print(f"\n  [opp_pair] forecast-gated months: {len(fc)}",
          flush=True)
    if not fc:
        print(f"  [opp_pair]   no analysis_forecasts.opp_pair_state "
              f"data yet; skipping.", flush=True)
        return 0

    if force:
        print(f"  [opp_pair] FORCE mode: deleting existing opp_pair "
              f"signal rows...", flush=True)
        await conn.execute(
            f"DELETE FROM {TABLE_SIGNALS} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            OPP_PAIR_SEC_TYPE, SIGNAL_TYPE_OPP_PAIR,
        )
        missing = fc
    else:
        present = await _signal_present_months(
            conn, OPP_PAIR_SEC_TYPE, SIGNAL_TYPE_OPP_PAIR)
        # forecast months are month-ENDS; signal presence is month-STARTS
        # (date_trunc) — compare on the truncated month.
        missing = [m for m in fc if m.replace(day=1) not in present]
        print(f"  [opp_pair]   missing months: {len(missing)}", flush=True)
    if not missing:
        print(f"  [opp_pair]   up to date; skipping.", flush=True)
        return 0

    # ---- Adaptive confirmation gate (HYB QRp_P90 keyed by the TARGET
    # industry — the code the signal is emitted on) ------------------------
    confirm_pair = await fetch_confirm(
        conn, OPP_PAIR_SEC_TYPE, missing,
        "analysis_forecasts.opp_pair_state", "trend_window",
        "TRUE", lambda w: f"pair_{w}",
        hybrid=OPP_PAIR_GATE_HYBRID, code_col="pair_industry_id",
    )
    print(f"  [opp_pair]   gate-confirmed configs: {len(confirm_pair)} "
          f"(HYB QRp_P{int(100 * GATE_Q)}, K={K_SHRINK}, per-TARGET-"
          f"industry calibration)", flush=True)

    # ---- Industry composite + benchmark trend inputs ----------------------
    industries = await fetch_opp_pair_industries(conn)
    pairs = await fetch_opp_pair_pairs(conn)
    since = min(_specs_for(missing), key=lambda s: s.lower).lower
    print(f"  [opp_pair] Fetching industry composite closes for "
          f"{len(industries)} industries since {since.isoformat()}...",
          flush=True)
    df = await fetch_industry_closes(conn, industries, since)
    bench = await fetch_benchmark_closes(conn, OPP_PAIR_BENCHMARK, since)
    if df.empty or bench.empty:
        print(f"  [opp_pair]   no source data; skipping.", flush=True)
        return 0

    grid_ord, grid_inds, _didx, _cidx, mats = build_opp_pair_matrices(
        df, bench,
    )
    first_dates = await fetch_industry_first_dates(conn, industries)
    first_ord = first_ords_from_dates(first_dates, grid_inds)
    windows = month_row_windows(grid_ord, _specs_for(missing))

    n_pair = 0
    print(f"  [opp_pair] Computing opposite-pair signals for "
          f"{len(windows)} months...", flush=True)
    for stat_month, rows in compute_opp_pair_signals(
        mats, windows, grid_inds, first_ord, grid_ord, pairs, confirm_pair,
    ):
        n = await _write_month(conn, rows)
        n_pair += n
        print(f"    [{stat_month}] opp_pair signals: wrote {n:,} rows",
              flush=True)
    return n_pair


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analysis Signals (ETF + Index + Stock). Per-day "
                "buy/sell signals mirroring the analysis_forecasts "
                "extreme-day detection: analysis_signals.signals — "
                "mov_rsi (top/bottom-1% RSI days), mov_std "
                "(2σ Bollinger breaches) and mov_gap (top/bottom-1% "
                "N-day price-return days) with the crossed "
                "threshold, a human-readable reason, the full "
                "detection params (JSON) and the action. Months "
                "are gated to the stat_months present in "
                "analysis_forecasts (mov_rsi pct=1 / mov_std k=2.0 / "
                "mov_gap pct=1) and computed incrementally at month "
                "granularity; opp_pair emits, by industry pair, a buy "
                "on the OTHER side industry when one industry's "
                "benchmark-offset trend is dropping."
    )
    ap.add_argument(
        "--sec-type", choices=SEC_TYPES, default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    ap.add_argument(
        "--live", action="store_true",
        help="After the signal pipeline, mirror every not-yet-recorded "
             "signal row into live.live_signals as a day-close "
             "observation (time 15:00:00, is_day_close_trigger=TRUE; "
             "mov_std close vs band, mov_rsi day RSI vs threshold).",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force
    live = args.live

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES

    t0 = time.time()
    print_build_header(
        "ANALYZE SIGNALS (per-day RSI-extreme + Bollinger-breach + "
        "gap-extreme + industry opposite-pair signals)",
        tables=f"{TABLE_SIGNALS}, live.live_signals (day-close mirror)"
        if live else TABLE_SIGNALS,
        sec_types=", ".join(sec_types),
        mode="FORCE (delete + recompute all forecast-gated months)" if force
        else "incremental (forecast-gated months missing from signals only)",
    )

    conn = await get_db_connection_async()
    try:
        total_rsi = total_std = total_gap = total_pxvol = 0
        total_mratio = total_live = 0
        for st in sec_types:
            r, s, g, p, mr = await _process_sec_type(conn, st, force=force)
            total_rsi += r
            total_std += s
            total_gap += g
            total_pxvol += p
            total_mratio += mr
            # is_active invariant refresh (latest signal date per
            # sec_type) — runs even when the data was up to date so a
            # pre-migration table self-heals on the next run.
            await _refresh_is_active(conn, st)

            if r or s or g or p or mr or force:
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME,
                    detail_name=DETAIL_NAME, description=DESCRIPTION,
                )

            if live:
                print(f"  [{st}] Mirroring day-close signals into "
                      f"live.live_signals...", flush=True)
                n_live = await mirror_live_close(conn, st)
                total_live += n_live
                print(f"  [{st}]   day-close records written: "
                      f"{n_live:,}", flush=True)

        # ---- opp_pair stage (industry pairs; index-space, runs once) ------
        n_pair = 0
        if not args.sec_type or args.sec_type == OPP_PAIR_SEC_TYPE:
            n_pair = await _process_opp_pair_signals(conn, force=force)
            # The pair rows share the 'index' sec_type — re-run the
            # is_active invariant after the stage (idempotent, cheap).
            await _refresh_is_active(conn, OPP_PAIR_SEC_TYPE)

        if total_rsi == 0 and total_std == 0 and total_gap == 0 \
                and total_pxvol == 0 and total_mratio == 0 \
                and total_live == 0 and n_pair == 0 and not force:
            print("\n  DB is up to date; nothing to do.", flush=True)
            print_wall_time(t0)
            return

        print(f"\n  TOTAL: {total_rsi:,} mov_rsi signals + "
              f"{total_std:,} mov_std signals + "
              f"{total_gap:,} mov_gap signals + "
              f"{total_pxvol:,} px_vol signals + "
              f"{total_mratio:,} margin_ratio signals + "
              f"{n_pair:,} opp_pair signals written", flush=True)
        if live:
            print(f"  TOTAL: {total_live:,} live day-close records "
                  f"written", flush=True)
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
