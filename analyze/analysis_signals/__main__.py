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
    ma_{W} ± 2.0·std_{W}days (upper → sell, lower → buy); W in 20/60 —
    W=5 is not emitted (its ~1-day buckets carry no base-rate lift:
    pure detection noise).

  - high_low_streaks (sub_type p{band_period}_{pct_type}): the MEAN-MID
    anchor day of every MA-Spread band-break excursion streak — the
    analysis_forecasts.high_low_streaks buckets' trigger days 1:1 (the
    forecasts engine's own anchor machinery reused verbatim). side top
    an ABOVE-band excursion → sell, bottom BELOW-band → buy (the
    mean-reversion reading the forecasts study measured from the mid
    anchor). The anchor is EX-POST: target months carry a
    HL_STREAKS_RESOLVE_LAG_MONTHS resolve lag and still-open streaks'
    anchors are dropped, so a write-once month never carries a
    provisional mid.

  - mov_pairs (sub_type pair{W}) / mov_pairs_ema (sub_type
    emapair{W}): the CROSS days of the EXISTING relative spreads
    ma5_vs_ma{W} / ema6_vs_ema{W} (fetched as pair_{W} / ema_pair_{W})
    — side top a CROSS UP / golden cross → sell, bottom a CROSS DOWN /
    death cross → buy; W in 60/120/255, cooldown 5 (the forecast event
    buckets' own machinery via build_pairs_matrices).

  (The former opp_pair industry-pair family was removed — its buckets
  average ~600 trigger days yet ~0 pooled mean forward offset change;
  the OOS study showed zero mean confirmation content even for
  statistically-strengthened buckets. Forecasts keep computing the
  opp_pair_state buckets; see analysis_signals.config.)

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
     60d) that period's reverse_prob exceeds GATE_RP_MIN (reverse
     P > 1% — a material reversal probability) AND that period's mean
     forward change is a REVERSAL (dir_ave > 0 — the bucket's average
     outcome reverses, so the signal holds) AND that period's
     reverse_prob beats the unconditional base rate (base_rates, per
     side — probability lift; the conjunct falls back to TRUE when the
     code has no base_rates row) AND that period's mean forward change
     beats the base drift (magnitude lift — the mean reversal must be
     bigger than the window's own; same fallback) AND the period is
     backed by at least GATE_MIN_OCCURRENCE observed forward outcomes
     AND a statistically real mean reversal (dir_ave · sqrt(occurrence)
     >= GATE_T_STAT_MIN · std_change), read from analysis_forecasts via
     the bucket's forecast_id. The row's confidence is NOT the reverse
     probability (it saturates at long horizons) but the DRIVING-FACTOR
     COMPOSITE at the qualifying period with the best composite: a
     weighted blend of evidence (t-stat), efficiency (sharpe),
     consistency (probability lift over the base rate) and the code's
     prior mean composite — all computed in the signal's direction
     (buy = upward reversal, sell = downward), all horizon-free; the
     argmax period + factor breakdown ride in the params JSON
     (conf_period / confidence_factors); see analysis_signals.config.
     Detection stays identical to the buckets; the gate only filters
     which days get written. NOTE: months written by an earlier
     (ungated) build keep their rows — ``--force`` rebuilds them
     under the gate.

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

import numpy as np

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
from _common.df_utils import host_array  # noqa: E402

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate  # noqa: E402
activate()

from analyze._common.identity import upsert_analysis_identity  # noqa: E402
from analyze.analysis_forecasts.config import (  # noqa: E402
    GAP_WINDOWS,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
    RSI_WINDOWS,
    SEC_TYPES,
    WINDOW_YEARS,
)
from analyze.analysis_forecasts.compute_pairs import (  # noqa: E402
    build_pairs_matrices,
)
from analyze.analysis_forecasts.compute_high_low_streaks import (  # noqa: E402
    build_streak_anchor_cells,
)
from analyze.analysis_forecasts.fetch import (  # noqa: E402
    fetch_active_codes,
    fetch_analysis_inputs,
    fetch_first_dates,
    fetch_high_low_streaks,
    fetch_price_vs_amt_states,
    assert_price_vs_amt_params,
    add_margin_ratio_features,
)
from analyze.analysis_forecasts.wide import (  # noqa: E402
    MonthSpec,
    _shift_years,
    build_grid,
    build_px_vol_state_matrices,
    date_ordinals,
    first_ords_from_dates,
    month_row_windows,
    scatter_column,
)
from analyze.analysis_signals.config import (  # noqa: E402
    ANALYSIS_NAME,
    DESCRIPTION,
    DETAIL_NAME,
    GAP_PCT,
    HL_STREAKS_RESOLVE_LAG_MONTHS,
    MARGIN_RATIO_SIGNAL_STATES,
    RSI_PCT,
    SIGNAL_COLUMNS,
    SIGNAL_TYPE_HL_STREAKS,
    SIGNAL_TYPE_MARGIN_RATIO,
    SIGNAL_TYPE_MOV_PAIRS,
    SIGNAL_TYPE_MOV_PAIRS_EMA,
    SIGNAL_TYPE_PX_VOL,
    STD_K,
    STD_SIGNAL_MA_WINDOWS,
    TABLE_SIGNALS,
    sub_type_ema_pair,
    sub_type_pair,
)
from analyze.analysis_signals.signals import (  # noqa: E402
    compute_gap_signals,
    compute_hls_signals,
    compute_margin_ratio_signals,
    compute_pairs_signals,
    compute_px_vol_signals,
    compute_rsi_signals,
    compute_std_signals,
)
from analyze.analysis_signals.gate import fetch_confirm  # noqa: E402
from analyze.analysis_signals.live_close import (  # noqa: E402
    mirror_live_close,
)

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("analysis_signals")


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
) -> tuple[list[date], ...]:
    """Stat_months present in analysis_forecasts for the matching
    signal configs (per family): mov_rsi at pct = RSI_PCT, mov_std at
    k = STD_K, mov_gap at pct = GAP_PCT, px_vol_state (any sided
    cell), margin_ratio_state (any signal-emitting z state),
    mov_pairs / mov_pairs_ema (any window/side) and high_low_streaks
    (any band combo — additionally cut to the
    HL_STREAKS_RESOLVE_LAG_MONTHS resolve window: the anchor is
    EX-POST, so a month targets only once every streak anchored in it
    is final). The motivation tables carry code alone as their
    partition key (2026-09 (code, forecast_id)-keyed shape), so each
    query resolves (sec_type, bucket) through the forecast_identities
    registry and applies the family's metric filter on the joined
    motivation table (the code-equality join predicate keeps the mov
    side on its code-leading PK)."""
    rsi_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.mov_rsi m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'mov_rsi' "
        f"AND m.pct = {RSI_PCT}",
        sec_type,
    )
    std_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.mov_std m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'mov_std' "
        f"AND m.k::float8 = {STD_K!r}",
        sec_type,
    )
    gap_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.mov_gap m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'mov_gap' "
        f"AND m.pct = {GAP_PCT}",
        sec_type,
    )
    pxvol_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.px_vol_state m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'px_vol_state' "
        "AND m.px_speed <> 'flat'",
        sec_type,
    )
    mratio_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.margin_ratio_state m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'margin_ratio_state' "
        f"AND m.ratio_state IN {MARGIN_RATIO_SIGNAL_STATES!r}",
        sec_type,
    )
    pairs_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.mov_pairs m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'mov_pairs'",
        sec_type,
    )
    epairs_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.mov_pairs_ema m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'mov_pairs_ema'",
        sec_type,
    )
    hls_all_rows = await conn.fetch(
        "SELECT DISTINCT i.stat_month "
        "FROM analysis_forecasts.forecast_identities i "
        "JOIN analysis_forecasts.high_low_streaks m "
        "  ON m.forecast_id = i.forecast_id AND m.code = i.code "
        "WHERE i.sec_type = $1 AND i.bucket = 'high_low_streaks'",
        sec_type,
    )
    hls_all = sorted(r["stat_month"] for r in hls_all_rows)
    # EX-POST resolve lag: keep months at least
    # HL_STREAKS_RESOLVE_LAG_MONTHS before the latest forecast month —
    # every streak anchored there is final by then (write-once months
    # must never carry provisional mid-anchors).
    if hls_all:
        latest = hls_all[-1]
        hls_rows = [
            m for m in hls_all
            if (latest.year - m.year) * 12 + (latest.month - m.month)
            >= HL_STREAKS_RESOLVE_LAG_MONTHS
        ]
    else:
        hls_rows = []
    return (
        sorted(r["stat_month"] for r in rsi_rows),
        sorted(r["stat_month"] for r in std_rows),
        sorted(r["stat_month"] for r in gap_rows),
        sorted(r["stat_month"] for r in pxvol_rows),
        sorted(r["stat_month"] for r in mratio_rows),
        sorted(r["stat_month"] for r in pairs_rows),
        sorted(r["stat_month"] for r in epairs_rows),
        hls_rows,
    )


# ---------------------------------------------------------------------------
#  Forecast-confirmation gate ("strong reverse signal") — see gate.py:
#  the confirmed-code sets are fetched from analysis_forecasts with the
#  forecast-result rule (reverse P > 1% in some period AND that period's
#  mean forward change a reversal AND probability + magnitude lift over
#  the base rates), returning each code's driving-factor confidence.
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
) -> tuple[int, ...]:
    """Process one sec_type end-to-end. Returns (mov_rsi, mov_std,
    mov_gap, px_vol, margin_ratio, mov_pairs, mov_pairs_ema,
    high_low_streaks) signal rows written."""
    logger.info(f"\n  [{sec_type}] Fetching active codes...")
    codes = sorted(await fetch_active_codes(conn, sec_type))
    logger.info(f"  [{sec_type}]   {len(codes):,} active codes")
    if not codes:
        logger.info(f"  [{sec_type}]   no active codes; skipping.")
        return (0,) * 8

    # ---- Target months: forecast presence gate ---------------------------
    fc = await _forecast_present_months(conn, sec_type)
    (fc_rsi, fc_std, fc_gap, fc_pxvol, fc_mratio,
     fc_pairs, fc_epairs, fc_hls) = fc
    logger.info(f"  [{sec_type}]   forecast-gated months: rsi={len(fc_rsi)} "
          f"std={len(fc_std)} gap={len(fc_gap)} pxvol={len(fc_pxvol)} "
          f"mratio={len(fc_mratio)} pairs={len(fc_pairs)} "
          f"epairs={len(fc_epairs)} hls={len(fc_hls)} "
          f"(+{HL_STREAKS_RESOLVE_LAG_MONTHS}-month resolve lag)")
    if not any(fc):
        logger.info(f"  [{sec_type}]   no analysis_forecasts data yet; "
              f"skipping.")
        return (0,) * 8

    # ---- Missing months (incremental / force) -----------------------------
    if force:
        logger.info(f"  [{sec_type}] FORCE mode: deleting existing {sec_type} "
              f"signal rows...")
        await conn.execute(
            f"DELETE FROM {TABLE_SIGNALS} WHERE sec_type = $1", sec_type
        )
        missing = fc
    else:
        present = {
            st: await _signal_present_months(conn, sec_type, st)
            for st in (
                "mov_rsi", "mov_std", "mov_gap", SIGNAL_TYPE_PX_VOL,
                SIGNAL_TYPE_MARGIN_RATIO, SIGNAL_TYPE_MOV_PAIRS,
                SIGNAL_TYPE_MOV_PAIRS_EMA, SIGNAL_TYPE_HL_STREAKS,
            )
        }
        present_by_type = {
            "mov_rsi": present["mov_rsi"],
            "mov_std": present["mov_std"],
            "mov_gap": present["mov_gap"],
            SIGNAL_TYPE_PX_VOL: present[SIGNAL_TYPE_PX_VOL],
            SIGNAL_TYPE_MARGIN_RATIO: present[SIGNAL_TYPE_MARGIN_RATIO],
            SIGNAL_TYPE_MOV_PAIRS: present[SIGNAL_TYPE_MOV_PAIRS],
            SIGNAL_TYPE_MOV_PAIRS_EMA: present[SIGNAL_TYPE_MOV_PAIRS_EMA],
            SIGNAL_TYPE_HL_STREAKS: present[SIGNAL_TYPE_HL_STREAKS],
        }
        # forecast months are month-ENDS; signal presence is month-STARTS
        # (date_trunc) — compare on the truncated month.
        missing = tuple(
            [m for m in fcm if m.replace(day=1) not in present_by_type[st]]
            for fcm, st in zip(
                fc,
                ("mov_rsi", "mov_std", "mov_gap", SIGNAL_TYPE_PX_VOL,
                 SIGNAL_TYPE_MARGIN_RATIO, SIGNAL_TYPE_MOV_PAIRS,
                 SIGNAL_TYPE_MOV_PAIRS_EMA, SIGNAL_TYPE_HL_STREAKS),
            )
        )
        logger.info(f"  [{sec_type}]   missing months: "
              f"rsi={len(missing[0])} std={len(missing[1])} "
              f"gap={len(missing[2])} pxvol={len(missing[3])} "
              f"mratio={len(missing[4])} pairs={len(missing[5])} "
              f"epairs={len(missing[6])} hls={len(missing[7])}")
    if not any(missing):
        logger.info(f"  [{sec_type}]   up to date; skipping.")
        return (0,) * 8
    (missing_rsi, missing_std, missing_gap, missing_pxvol, missing_mratio,
     missing_pairs, missing_epairs, missing_hls) = missing

    # ---- Forecast-confirmation gate: confirmed codes per config --------
    # Forecast-result rule (see gate.py): reverse P > 1% in some
    # forecast period AND that period's mean forward change a reversal
    # AND that period's reverse_prob above the unconditional base rate.
    confirm_rsi = (
        await fetch_confirm(
            conn, sec_type, missing_rsi,
            "analysis_forecasts.mov_rsi", "rsi_window",
            f"m.pct = {RSI_PCT}", lambda w: f"rsi_{w}",
        )
        if missing_rsi else {}
    )
    confirm_std = (
        await fetch_confirm(
            conn, sec_type, missing_std,
            "analysis_forecasts.mov_std", "ma_window",
            f"m.k::float8 = {STD_K!r}", lambda w: f"ma_{w}",
        )
        if missing_std else {}
    )
    confirm_gap = (
        await fetch_confirm(
            conn, sec_type, missing_gap,
            "analysis_forecasts.mov_gap", "gap_window",
            f"m.pct = {GAP_PCT}", lambda w: f"gap_{w}",
        )
        if missing_gap else {}
    )
    confirm_pxvol = (
        await fetch_confirm(
            conn, sec_type, missing_pxvol,
            "analysis_forecasts.px_vol_state", "px_speed",
            "m.px_speed <> 'flat'", lambda s: s,
        )
        if missing_pxvol else {}
    )
    confirm_mratio = (
        await fetch_confirm(
            conn, sec_type, missing_mratio,
            "analysis_forecasts.margin_ratio_state", "ratio_state",
            "m.ratio_state IN ('vlow', 'low', 'high', 'vhigh')",
            lambda s: s,
        )
        if missing_mratio else {}
    )
    # high_low_streaks: the "window" axis packs the two band axes into
    # one sortable key ("{band_period}_{pct_type}") so the generic
    # gate SQL groups the 12 combos; matrix_key passes it through.
    confirm_hls = (
        await fetch_confirm(
            conn, sec_type, missing_hls,
            "analysis_forecasts.high_low_streaks",
            "band_period::text || '_' || pct_type::text",
            "TRUE", lambda w: w,
        )
        if missing_hls else {}
    )
    # mov_pairs / mov_pairs_ema: the cross buckets, keyed by the slow
    # leg (matrix key f"{prefix}_{w}" — the engine's spread matrix
    # naming, build_pairs_matrices' prefix convention).
    confirm_pairs = (
        await fetch_confirm(
            conn, sec_type, missing_pairs,
            "analysis_forecasts.mov_pairs", "pair_window",
            "TRUE", lambda w: f"pair_{w}",
        )
        if missing_pairs else {}
    )
    confirm_epairs = (
        await fetch_confirm(
            conn, sec_type, missing_epairs,
            "analysis_forecasts.mov_pairs_ema", "pair_window",
            "TRUE", lambda w: f"ema_pair_{w}",
        )
        if missing_epairs else {}
    )
    logger.info(f"  [{sec_type}]   gate-confirmed configs: rsi={len(confirm_rsi)} "
          f"std={len(confirm_std)}, gap={len(confirm_gap)}, "
          f"pxvol={len(confirm_pxvol)}, mratio={len(confirm_mratio)}, "
          f"pairs={len(confirm_pairs)}, epairs={len(confirm_epairs)}, "
          f"hls={len(confirm_hls)} "
          f"(reverse P > 1% + mean reversal + probability lift + "
          f"magnitude lift) — month x window x side combos with a "
          f"qualifying forecast period, with per-code tier/baseline/"
          f"rank calibration + driving-factor confidence")

    # ---- Fetch inputs (bounded to the earliest needed window start) ------
    todo_months = sorted(set(missing_rsi) | set(missing_std)
                         | set(missing_gap) | set(missing_pxvol)
                         | set(missing_mratio) | set(missing_pairs)
                         | set(missing_epairs) | set(missing_hls))
    since = min(_specs_for(todo_months), key=lambda s: s.lower).lower
    logger.info(f"  [{sec_type}] Fetching joined inputs (price / ma / rsi / "
          f"gap / std / trading_amount) for {len(codes):,} codes since "
          f"{since.isoformat()}...")
    df = await fetch_analysis_inputs(conn, sec_type, codes, since)
    logger.info(f"  [{sec_type}]   {len(df):,} (code, date) rows")
    if df.empty:
        logger.info(f"  [{sec_type}]   no source data; skipping.")
        return (0,) * 8
    df = add_margin_ratio_features(df)

    # ---- Wide grid + per-code first-data gate ------------------------------
    grid_ord, grid_codes, didx, cidx = build_grid(df)
    shape = (len(grid_ord), len(grid_codes))
    first_dates = await fetch_first_dates(conn, sec_type, codes)
    first_ord = first_ords_from_dates(first_dates, grid_codes)

    n_rsi = n_std = n_gap = n_pxvol = n_mratio = 0
    n_pairs = n_epairs = n_hls = 0

    # ---- Stage 1: RSI extreme signals --------------------------------------
    if missing_rsi:
        windows_rsi = month_row_windows(grid_ord, _specs_for(missing_rsi))
        logger.info(f"  [{sec_type}] Computing RSI signals (top/bottom "
              f"{RSI_PCT}%) for {len(windows_rsi)} months...")
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
            logger.info(f"    [{stat_month}] mov_rsi signals: wrote {n:,} rows")
        del rsi_mats

    # ---- Stage 2: Bollinger-breach signals ----------------------------------
    if missing_std:
        windows_std = month_row_windows(grid_ord, _specs_for(missing_std))
        logger.info(f"  [{sec_type}] Computing Bollinger-breach signals "
              f"(±{STD_K:g}σ, W in {STD_SIGNAL_MA_WINDOWS}) for "
              f"{len(windows_std)} months...")
        std_mats: dict = {
            "price": scatter_column(df, "price", shape, didx, cidx),
        }
        for w in STD_SIGNAL_MA_WINDOWS:
            std_mats[f"ma_{w}"] = scatter_column(df, f"ma_{w}days", shape,
                                                 didx, cidx)
            std_mats[f"std_{w}"] = scatter_column(df, f"std_{w}days", shape,
                                                  didx, cidx)
        for stat_month, rows in compute_std_signals(
            std_mats, windows_std, grid_codes, sec_type, first_ord,
            grid_ord, confirm_std, ma_windows=STD_SIGNAL_MA_WINDOWS,
        ):
            n = await _write_month(conn, rows)
            n_std += n
            logger.info(f"    [{stat_month}] mov_std signals: wrote {n:,} rows")
        del std_mats

    # ---- Stage 3: gap extreme signals ---------------------------------------
    if missing_gap:
        windows_gap = month_row_windows(grid_ord, _specs_for(missing_gap))
        logger.info(f"  [{sec_type}] Computing gap signals (top/bottom "
              f"{GAP_PCT}%) for {len(windows_gap)} months...")
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
            logger.info(f"    [{stat_month}] mov_gap signals: wrote {n:,} rows")
        del gap_mats

    # ---- Stage 4: px_vol state signals ---------------------------------------
    # The day categories come from the analysis.mov_ave_price_vs_amt
    # REGISTRY (the px_vol family's date-level source of truth) — the
    # recorded build parameters are verified against the constants
    # before consuming.
    if missing_pxvol:
        await assert_price_vs_amt_params(conn, sec_type)
        states_df = await fetch_price_vs_amt_states(
            conn, sec_type, codes, since
        )
        if states_df.empty:
            logger.info(f"  [{sec_type}] px_vol: no price_vs_amt registry "
                        f"rows (run python -m analyze.mov_ave_spread); "
                        f"skipping.")
            del states_df
        else:
            windows_pxvol = month_row_windows(grid_ord, _specs_for(missing_pxvol))
            logger.info(f"  [{sec_type}] Computing px_vol signals from "
                        f"{len(states_df):,} price_vs_amt registry rows "
                        f"(adaptive σ/z state cells) for "
                        f"{len(windows_pxvol)} months...")
            px_mats = build_px_vol_state_matrices(
                states_df, grid_ord, grid_codes, shape,
            )
            for stat_month, rows in compute_px_vol_signals(
                px_mats, windows_pxvol, grid_codes, sec_type, first_ord,
                grid_ord, confirm_pxvol,
            ):
                n = await _write_month(conn, rows)
                n_pxvol += n
                logger.info(f"    [{stat_month}] px_vol signals: wrote {n:,} rows")
            del px_mats
            del states_df

    # ---- Stage 5: margin_ratio state signals --------------------------------
    if missing_mratio:
        windows_mratio = month_row_windows(
            grid_ord, _specs_for(missing_mratio))
        logger.info(f"  [{sec_type}] Computing margin_ratio signals (margin-buy "
              f"intensity z states) for {len(windows_mratio)} months...")
        mr_mats = {
            "z": scatter_column(df, "ratio_z", shape, didx, cidx),
        }
        for stat_month, rows in compute_margin_ratio_signals(
            mr_mats, windows_mratio, grid_codes, sec_type, first_ord,
            grid_ord, confirm_mratio,
        ):
            n = await _write_month(conn, rows)
            n_mratio += n
            logger.info(f"    [{stat_month}] margin_ratio signals: wrote "
                  f"{n:,} rows")
        del mr_mats

    # ---- Stage 6: MA / EMA-pair cross signals ------------------------------
    # Cross-EVENT detection on the EXISTING relative-spread columns
    # (fetch_analysis_inputs' pair_{W} / ema_pair_{W} — the forecasts
    # engine's own matrix machinery via build_pairs_matrices): a signal
    # day is the stored spread's sign flip, with the same cooldown as
    # the forecast event buckets. One source-agnostic engine; the
    # prefix selects the family.
    if missing_pairs or missing_epairs:
        logger.info(f"  [{sec_type}] Computing MA/EMA-pair cross signals "
              f"(pair_windows={list(MOV_PAIRS_WINDOWS)})...")
    if missing_pairs:
        windows_pairs = month_row_windows(
            grid_ord, _specs_for(missing_pairs))
        pairs_mats = build_pairs_matrices(
            df, shape, didx, cidx,
            pair_windows=MOV_PAIRS_WINDOWS, prefix="pair",
        )
        for stat_month, rows in compute_pairs_signals(
            pairs_mats, windows_pairs, grid_codes, sec_type, first_ord,
            grid_ord, confirm_pairs,
            pair_windows=MOV_PAIRS_WINDOWS, prefix="pair",
            signal_type=SIGNAL_TYPE_MOV_PAIRS, sub_type=sub_type_pair,
        ):
            n = await _write_month(conn, rows)
            n_pairs += n
            logger.info(f"    [{stat_month}] mov_pairs signals: "
                  f"wrote {n:,} rows")
        del pairs_mats

    # ---- Stage 7: EMA-pair cross signals ------------------------------------
    if missing_epairs:
        windows_epairs = month_row_windows(
            grid_ord, _specs_for(missing_epairs))
        epairs_mats = build_pairs_matrices(
            df, shape, didx, cidx,
            pair_windows=MOV_PAIRS_EMA_WINDOWS, prefix="ema_pair",
        )
        for stat_month, rows in compute_pairs_signals(
            epairs_mats, windows_epairs, grid_codes, sec_type, first_ord,
            grid_ord, confirm_epairs,
            pair_windows=MOV_PAIRS_EMA_WINDOWS, prefix="ema_pair",
            signal_type=SIGNAL_TYPE_MOV_PAIRS_EMA, sub_type=sub_type_ema_pair,
        ):
            n = await _write_month(conn, rows)
            n_epairs += n
            logger.info(f"    [{stat_month}] mov_pairs_ema signals: "
                  f"wrote {n:,} rows")
        del epairs_mats

    # ---- Stage 8: High/Low streak mid-anchor signals -----------------------
    # Signal days = the MEAN-MID anchor days of the band-break excursion
    # streaks (analysis.mov_ave_high_low_pct_streaks) — the
    # high_low_streaks forecast buckets' trigger days 1:1, resolved by
    # the FORECASTS ENGINE'S OWN MACHINERY (fetch + anchor cells), so
    # the gate's bucket stats describe exactly these days. The anchor
    # is EX-POST: target months carry the resolve lag, and anchors of
    # streaks still within GAP_TOLERANCE rows of the data edge are
    # dropped (write-once months must never carry provisional mids).
    if missing_hls:
        streaks = await fetch_high_low_streaks(
            conn, sec_type, codes, since)
        if streaks.empty:
            logger.info(f"  [{sec_type}] hls: no streak rows in "
                  f"analysis.mov_ave_high_low_pct_streaks (run "
                  f"python -m analyze.mov_ave_spread); skipping.")
            del streaks
        else:
            windows_hls = month_row_windows(
                grid_ord, _specs_for(missing_hls))
            logger.info(f"  [{sec_type}] Computing High/Low streak "
                  f"mid-anchor signals from {len(streaks):,} streaks "
                  f"for {len(windows_hls)} months...")
            side_top = host_array(streaks["side"].to_numpy()) == "top"
            extra = {
                # the side's band edge in PRICE space (signal_threshold)
                "band_edge": np.where(
                    side_top,
                    host_array(streaks["band_high"].to_numpy(
                        dtype="float64")),
                    host_array(streaks["band_low"].to_numpy(
                        dtype="float64")),
                ),
                "start_ord": date_ordinals(streaks["start_date"]),
                "end_ord": date_ordinals(streaks["end_date"]),
            }
            cells = build_streak_anchor_cells(
                streaks, df, grid_ord, grid_codes, shape, didx, cidx,
                extra_cols=extra,
            )
            if cells is None:
                logger.info(f"  [{sec_type}] hls: no streak anchors on "
                      f"the grid; skipping.")
            else:
                price_mat = scatter_column(
                    df, "price", shape, didx, cidx)
                for stat_month, rows in compute_hls_signals(
                    cells, price_mat, windows_hls, grid_codes, sec_type,
                    first_ord, grid_ord, confirm_hls,
                ):
                    n = await _write_month(conn, rows)
                    n_hls += n
                    logger.info(f"    [{stat_month}] high_low_streaks "
                          f"signals: wrote {n:,} rows")
                del price_mat
                del cells
            del streaks

    return (n_rsi, n_std, n_gap, n_pxvol, n_mratio,
            n_pairs, n_epairs, n_hls)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analysis Signals (ETF + Index + Stock). Per-day "
                "buy/sell signals mirroring the analysis_forecasts "
                "extreme-day detection: analysis_signals.signals — "
                "mov_rsi (top/bottom-1% RSI days), mov_std "
                "(2σ Bollinger breaches), mov_gap (top/bottom-1% "
                "N-day price-return days), mov_pairs / mov_pairs_ema "
                "(the golden / death CROSS days of the existing "
                "ma5_vs_ma{W} / ema6_vs_ema{W} spreads), "
                "high_low_streaks (the "
                "mean-mid anchor day of every band-break excursion "
                "streak — the high_low_streaks forecast buckets' "
                "trigger days 1:1, gated by a resolve lag so a "
                "write-once month never carries a provisional mid) "
                "with the crossed "
                "threshold, a human-readable reason, the full "
                "detection params (JSON) and the action. Months "
                "are gated to the stat_months present in "
                "analysis_forecasts (mov_rsi pct=1 / mov_std k=2.0 / "
                "mov_gap pct=1) and computed incrementally at month "
                "granularity."
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
        "gap-extreme + px_vol + margin_ratio + MA/EMA-pair-cross + "
        "High/Low-streak signals)",
        tables=f"{TABLE_SIGNALS}, live.live_signals (day-close mirror)"
        if live else TABLE_SIGNALS,
        sec_types=", ".join(sec_types),
        mode="FORCE (delete + recompute all forecast-gated months)" if force
        else "incremental (forecast-gated months missing from signals only)",
    )

    conn = await get_db_connection_async()
    try:
        total_rsi = total_std = total_gap = total_pxvol = 0
        total_mratio = total_pairs = total_epairs = 0
        total_hls = total_live = 0
        for st in sec_types:
            (r, s, g, p, mr, pr, ep, hs) = await _process_sec_type(
                conn, st, force=force)
            total_rsi += r
            total_std += s
            total_gap += g
            total_pxvol += p
            total_mratio += mr
            total_pairs += pr
            total_epairs += ep
            total_hls += hs
            # is_active invariant refresh (latest signal date per
            # sec_type) — runs even when the data was up to date so a
            # pre-migration table self-heals on the next run.
            await _refresh_is_active(conn, st)

            if r or s or g or p or mr or pr or ep or hs or force:
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME,
                    detail_name=DETAIL_NAME, description=DESCRIPTION,
                )

            if live:
                logger.info(f"  [{st}] Mirroring day-close signals into "
                      f"live.live_signals...")
                n_live = await mirror_live_close(conn, st)
                total_live += n_live
                logger.info(f"  [{st}]   day-close records written: "
                      f"{n_live:,}")

        if total_rsi == 0 and total_std == 0 and total_gap == 0 \
                and total_pxvol == 0 and total_mratio == 0 \
                and total_pairs == 0 and total_epairs == 0 \
                and total_hls == 0 \
                and total_live == 0 and not force:
            logger.info("\n  DB is up to date; nothing to do.")
            print_wall_time(t0)
            return

        logger.info(f"\n  TOTAL: {total_rsi:,} mov_rsi signals + "
              f"{total_std:,} mov_std signals + "
              f"{total_gap:,} mov_gap signals + "
              f"{total_pxvol:,} px_vol signals + "
              f"{total_mratio:,} margin_ratio signals + "
              f"{total_pairs:,} mov_pairs signals + "
              f"{total_epairs:,} mov_pairs_ema signals + "
              f"{total_hls:,} high_low_streaks signals written")
        if live:
            logger.info(f"  TOTAL: {total_live:,} live day-close records "
                  f"written")
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
