"""Forecast-confirmation gate + driving-factor confidence for
analysis_signals.

A detected extreme day is RECORDED only when its matching
analysis_forecasts bucket (same code/sec_type/stat_month/window/side/
pct|k/cooldown config) qualifies under the FORECAST-RESULT rule:
the bucket's MIXED forecast_results period row (period = 'mixed' — the
FIXED-weight blend of the four horizon rows next/5d/20d/60d at the
config.MIXED_HORIZON_WEIGHTS weights 5d 0.50 / next 0.30 / 20d 0.15 /
60d 0.05, materialized on every forecast bucket so the whole forward
profile of one trigger reads as ONE clean row) must have

  - reverse P > 1% — ``reverse_prob > GATE_RP_MIN``: the blended
    probability of a swing-aware reversal beyond the FIXED 1% bar —
    P(the n-day forward window's ADVERSE PATH EXTREME goes beyond ±1%,
    i.e. the window swung past the bar against the side at some close,
    not merely at the period-end) — is material (a bare ``> 0`` tail is
    not enough), AND
  - a MEAN REVERSAL — the blended mean forward change in the bucket's
    direction (``dir_ave`` = ±ave_change, sign-flipped for top/upper
    buckets) is > 0: the bucket's average outcome reverses, so the
    signal holds — a fat reversal tail with a continuing mean does not,
    AND
  - PROBABILITY LIFT — ``reverse_prob > base_prob`` where base_prob is
    the UNCONDITIONAL blended base rate (analysis_forecasts.base_rates
    period='mixed': base_down_prob for top/upper buckets, base_up_prob
    for bottom/lower — the SAME weights over the same horizons) — the
    bucket must beat the base rate, not just clear an absolute bar.
    When the code has NO base_rates mixed row the conjunct falls back
    to TRUE, AND
  - MAGNITUDE LIFT — ``dir_ave > base_dir`` where base_dir is the
    UNCONDITIONAL blended mean directional change (sign-aligned
    base_ave_change) — the probability lift's magnitude twin: a bucket
    whose average reversal is smaller than the window's own drift has
    no mean edge regardless of its hit rate. Same base_rates fallback.
    AND
  - CONFIDENCE FLOOR — the composite confidence (below) clears the
    family's CONF_FLOOR bar (set at the measured knee of the family's
    realized-return ladder on the pass-1 population — ≈ its P75-P80
    confirmed-confidence quantile, mov_gap at its P50; $3 /
    min_confidence, NULL = no floor).
    The 2026-09 reduction study's confidence-decile ladder is cleanly
    monotone on realized forward returns with a zero/negative bottom
    (mov_rsi deciles 1/2: -1.11% / +0.13% vs +2.95..+3.37% at deciles
    7-10), so the floor removes the whole weak-composite tail at once.

(REMOVED 2026-09: the sample-size bar ``occ >= GATE_MIN_OCCURRENCE``
 and the significance bar ``dir_ave·√occ >= GATE_T_STAT_MIN·std_change``.
 The 2026-09 streak-merge leaves pct-width buckets with only ~3-6 merged
 signals (median occurrence 3-4 on the mixed row's MIN-over-horizons
 count), so the two bars dropped ~96% of the strongest-edge pct=1
 buckets — the strongest mean lift of any width (3.09% vs 1.96% at
 pct=5) — while the lift conjuncts passed ~96%. Absence of t-significance
 at n=3-4 is not absence of edge; the remaining conjuncts carry the
 edge evidence, and occ / std_change still feed the confidence's evidence
 and efficiency factors, where they belong.)

Reading the gate on the MIXED row (since 2026-09; previously ANY single
qualifying horizon could carry the signal and the confidence was taken
at the argmax-composite horizon) is what makes EVERY forecast horizon of
the same signal trigger contribute to the signal — the short horizons
carry it (5d 50% + next 30%), the long ones season it (20d 15% + 60d
5%) — and one blended row per bucket keeps the gate's qualifying/
confidence semantics horizon-free by construction.

All conjuncts read the bucket's OWN historical outcomes from
analysis_forecasts (forecast_results + base_rates via forecast_id) —
no population calibration and no look-ahead concern: a month's gate
only sees that month's own bucket statistics, exactly what the
forecast tables already recorded for it.

Driving-factor CONFIDENCE (the row's confidence): the bucket's
composite of the four factors that drive its reversal edge, computed
on the mixed row (the period is carried so consumers know the horizon
blend the confidence speaks about; conf_period is always 'mixed'):

  confidence = W_EVIDENCE · f_t + W_EFFICIENCY · f_sharpe
             + W_CONSISTENCY · f_lift_prob + W_CALIBRATION · f_prior
             + W_SWING · f_swing

  f_t       = t / (t + CONF_T_ANCHOR)      t = dir_ave·√occ / std_change
  f_sharpe  = 1 - exp(-sharpe / CONF_SHARPE_ANCHOR)
                                       sharpe = dir_ave / std_change
  f_lift_p  = 1 - exp(-lift_prob / CONF_LIFT_PROB_ANCHOR)
                                       lift_prob = reverse_prob - base_prob
  f_prior   = the code's PRIOR mean base composite (same code/side/
            mixed period, months < M, windows pooled — the gate study's
            prior-vs-future mean-rp correlation 0.80-0.97 transplanted
            to the composite), CONF_PRIOR_NEUTRAL below
            CONF_PRIOR_MIN_POP prior bucket-periods.
  f_swing   = clamp((max_low_change_ratio - CONF_SWING_LO)
                    / (CONF_SWING_HI - CONF_SWING_LO), 0, 1) — the
            bucket's mixed-row max_low_change_ratio (the widest
            within-window swing (1 + max path high) / (1 + min path
            low)) as the SOFT max/low-ratio factor. The 2026-09
            reduction study measured realized signal returns rising
            monotonically with the ratio on BOTH sides (the proposed
            deterministic "<1.1 for buy" bar is inverted by the data —
            low-swing buckets realize the LEAST), so the factor ramps
            with the ratio and the user's 1.1/1.2 bars sit inside the
            ramp as soft mid-anchors. FORCE-INCLUDE guarantee: the
            swing ratio is a composite FACTOR ONLY — it can re-rank
            confidence, never remove a signal by itself, and the
            pct=1 extreme-day detection base is untouched (no
            deterministic max/low cutoff exists anywhere).

Every factor is computed in the SIGNAL'S direction and floored at 0,
and every factor except the prior is horizon-free — so the composite
compares across families and sec_types. (History: this replaced the
former MAX(reverse_prob) confidence, which mostly ranked buckets by
horizon because rp saturates at 0.6-1.0 on the long horizons; the OOS
study is temp_scripts/study_confidence_oos.py.)

The full factor breakdown (t, sharpe, lift_prob, prior,
max_low_change_ratio + the four factor scores) is recorded per row in
the params JSON under confidence_factors; the period under conf_period
('mixed').

Per-security calibration columns (kept from the 2026-09 gate study —
prior-vs-future mean rp correlation 0.80-0.97; row metadata only,
they do not gate):
  - tier — 'proven' (2) when the code's prior mean composite >=
    CONF_PROVEN (top ~10% of codes by prior bucket quality),
    'proven_dir' (1) when the code's prior mean DIRECTIONAL move >=
    PROVEN_DIR_AVE, else 'standard' (0). Code stats need >=
    PROVEN_MIN_POP prior bucket-periods.
  - code_baseline — the code's prior mean composite (mixed period).
  - code_rank — coarse within-code percentile FLOOR of the confidence
    (from the code's own prior P25/P50/P75/P90/P95 of the composite),
    NULL below RANK_MIN_POP prior bucket-periods.

All qualification / means / factor math run in SQL (percentile_cont
interpolates linearly; factors are GREATEST-clamped at 0 so the
ungated prior population cannot underflow exp or credit contrary
buckets). ONE SQL FILE PER FAMILY under
database/sql/analysis/analysis_signals/gate/<bucket>.sql — the
family's motivation table, bucket name, win column and emission-slice
filter (RSI_PCT / STD_SIGNAL_K / GAP_PCT / the state / pairs slices)
are literal SQL in the family's own file instead of Python string
fragments threaded through here (2026-09-15 refactor; regenerate the
files with temp_scripts/_gen_gate_sql.py after changing the mirrored
config constants). The returned ConfirmMap keeps the legacy shape per
(stat_month, matrix_key, side) with the per-code arrays extended by
the period + factor JSON, so the signal engines only gain new
params fields.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import numpy as np

from analyze.analysis_signals.config import CONF_FLOOR
from analyze.analysis_signals.signals._base import ConfirmMap

if TYPE_CHECKING:
    import asyncpg

# The gate SQL directory, resolved from THIS module's repo location
# (never cwd — the pipeline runs from arbitrary working directories).
_GATE_SQL_DIR = (
    Path(__file__).resolve().parents[2]
    / "database" / "sql" / "analysis" / "analysis_signals" / "gate"
)

# Gate SQL bucket → CONF_FLOOR key. CONF_FLOOR is keyed by FAMILY name
# while the state families' SQL buckets carry a _state suffix — mapped
# explicitly here instead of a string strip (the pairing is a fact
# about the two namings, not a rule).
_BUCKET_TO_FLOOR_KEY: dict[str, str] = {
    "mov_rsi": "mov_rsi",
    "mov_std": "mov_std",
    "mov_gap": "mov_gap",
    "mov_pairs": "mov_pairs",
    "mov_pairs_ema": "mov_pairs_ema",
    "px_vol_state": "px_vol",
    "margin_ratio_state": "margin_ratio",
    "high_low_streaks": "high_low_streaks",
}


@lru_cache(maxsize=None)
def _load_sql(filename: str) -> str:
    """One gate SQL file's text (cached — read once per process)."""
    return (_GATE_SQL_DIR / filename).read_text(encoding="utf-8")


async def fetch_confirm(
    conn: asyncpg.Connection,
    sec_type: str,
    months: list[date],
    bucket: str,
    matrix_key: Callable[[int | str], str],
) -> ConfirmMap:
    """Confirmed-code calibration sets for one bucket family under the
    reversal gate (shared by every signal engine).

    Runs the family's SQL file
    ``database/sql/analysis/analysis_signals/gate/<bucket>.sql`` as
    ``CREATE TEMP TABLE gate_fx ON COMMIT DROP AS (...)`` + ANALYZE,
    then the shared ``_upper.sql`` machinery on top (the forecast
    -result rule + driving-factor confidence + calibration columns —
    see the module docstring), all inside ONE transaction (the temp
    table is ON COMMIT DROP). The family file is parameterless from
    this side; _upper.sql takes $1 = the target months (date[]) and
    $2 = the family's CONF_FLOOR bar (float8, NULL = no floor).

    Args:
        conn: asyncpg connection (the caller's pool; no outer
              transaction is assumed — this opens its own).
        sec_type: 'index' | 'etf' | 'stock' (the family file's $1).
        months: the target stat months (the months to confirm).
        bucket: the gate SQL bucket name — also the family file's
              stem (mov_rsi / mov_std / mov_gap / mov_pairs /
              mov_pairs_ema / px_vol_state / margin_ratio_state /
              high_low_streaks).
        matrix_key: window value → the engine's matrix key
              ("rsi_{w}" / "ma_{w}" / "gap_{w}" / the state string).

    Returns {(stat_month, matrix_key, side): (codes, confidences,
    tier_pts, baselines, ranks, periods, factors)} — confidences are
    the buckets' driving-factor composite on the MIXED row (the
    forecast-result rule: material rp + mean reversal + probability
    lift + magnitude lift + the confidence floor, see the module
    docstring); tier_pts 2/1/0 = proven / proven_dir / standard;
    baselines / ranks are the code's prior mean composite and
    within-code percentile floor (NaN when unknown); periods are the
    qualifying period strings (always 'mixed' — comes from the SQL);
    factors are the per-code confidence_factors JSON objects (as
    text) for the params column.
    """
    family_sql = _load_sql(bucket + ".sql")
    upper_sql = _load_sql("_upper.sql")
    min_confidence: float | None = CONF_FLOOR.get(
        _BUCKET_TO_FLOOR_KEY[bucket]
    )

    async with conn.transaction():
        # The family file materializes its bucket rows (params $1 =
        # sec_type, $2 = the target months date[]); the shared
        # machinery then plans on REAL row counts (ANALYZE).
        await conn.execute(
            "CREATE TEMP TABLE gate_fx ON COMMIT DROP AS (\n"
            + family_sql
            + "\n)",
            sec_type, months,
        )
        await conn.execute("ANALYZE pg_temp.gate_fx")
        rows = await conn.fetch(upper_sql, months, min_confidence)
    return {
        (r["stat_month"], matrix_key(r["win"]), r["side"]): (
            np.asarray(r["codes"]),
            np.asarray(r["confidences"], dtype=np.float64),
            np.asarray(r["tier_pts"], dtype=np.int64),
            np.asarray(r["baselines"], dtype=np.float64),
            np.asarray(r["ranks"], dtype=np.float64),
            np.asarray(r["periods"]),
            np.asarray(r["factors"]),
        )
        for r in rows
    }
