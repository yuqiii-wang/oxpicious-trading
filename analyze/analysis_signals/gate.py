"""Forecast-confirmation gate + driving-factor confidence for
analysis_signals.

A detected extreme day is RECORDED only when its matching
analysis_forecasts bucket (same code/sec_type/stat_month/window/side/
pct|k/cooldown config) qualifies under the FORECAST-RESULT rule:
at least ONE forecast_results period (next / 5d / 20d / 60d) must have

  - reverse P > 1% — ``reverse_prob > GATE_RP_MIN``: the bucket's
    probability of a swing-aware reversal beyond the FIXED 1% bar —
    P(the n-day forward window's ADVERSE PATH EXTREME goes beyond ±1%,
    i.e. the window swung past the bar against the side at some close,
    not merely at the period-end; at the next horizon the path IS the
    endpoint, e.g. the 5d probability is about the 5 closes after the
    signal) — is material (a bare ``> 0`` tail is not enough), AND
  - a MEAN REVERSAL — the period's mean forward change in the
    bucket's direction (``dir_ave`` = ±ave_change, sign-flipped for
    top/upper buckets) is > 0: the bucket's average outcome reverses,
    so the signal holds — a fat reversal tail with a continuing mean
    does not, AND
  - PROBABILITY LIFT — ``reverse_prob > base_prob`` where base_prob is
    the UNCONDITIONAL same-window, same-threshold reversal probability
    (analysis_forecasts.base_rates: base_down_prob for top/upper
    buckets, base_up_prob for bottom/lower) — the bucket must beat the
    base rate, not just clear an absolute bar (a 1% next-day reversal
    probability is worthless when every random day reverses 26% of the
    time at the same threshold). When the code has NO base_rates row
    (short-history state buckets outside the full-window gate) the
    conjunct falls back to TRUE, AND
  - MAGNITUDE LIFT — ``dir_ave > base_dir`` where base_dir is the
    UNCONDITIONAL same-window mean directional change (sign-aligned
    base_ave_change) — the probability lift's magnitude twin: a bucket
    whose average reversal is smaller than the window's own drift has
    no mean edge regardless of its hit rate. Same base_rates fallback.
    OOS (temp_scripts/study_confidence_oos.py): buckets dropped by
    this conjunct realize ~half the mean forward reversal of the
    passers (mov_gap index 0.0076 vs 0.0380, px_vol stock 0.0360 vs
    0.0779), AND
  - a REAL SAMPLE — ``occurrence_count >= GATE_MIN_OCCURRENCE``: a
    bucket-period backed by fewer observed forward outcomes has a
    reverse_prob quantized to coarse steps and a mean flip-flopping on
    one observation (the std5 failure mode generalized), AND
  - a SIGNIFICANT MEAN — ``dir_ave · sqrt(occurrence_count)
    >= GATE_T_STAT_MIN · std_change`` (a Student-t style bar): the
    mean reversal must be statistically distinguishable from 0 given
    the bucket's OWN dispersion and sample size, not merely positive.
    Rolling-half OOS (temp_scripts/study_signal_strength.py):
    bar-passers realize 2-3x the future mean dir_ave of absolute-rule
    -only passers.

All conjuncts read the bucket's OWN historical outcomes from
analysis_forecasts (forecast_results + base_rates via forecast_id) —
no population calibration and no look-ahead concern: a month's gate
only sees that month's own bucket statistics, exactly what the
forecast tables already recorded for it.

Driving-factor CONFIDENCE (the row's confidence): the bucket's
composite of the four factors that drive its reversal edge, at the
qualifying period with the best composite (argmax; the period is
carried so consumers know the horizon the confidence speaks about):

  confidence = W_EVIDENCE · f_t + W_EFFICIENCY · f_sharpe
             + W_CONSISTENCY · f_lift_prob + W_CALIBRATION · f_prior

  f_t       = t / (t + CONF_T_ANCHOR)      t = dir_ave·√occ / std_change
  f_sharpe  = 1 - exp(-sharpe / CONF_SHARPE_ANCHOR)
                                       sharpe = dir_ave / std_change
  f_lift_p  = 1 - exp(-lift_prob / CONF_LIFT_PROB_ANCHOR)
                                       lift_prob = reverse_prob - base_prob
  f_prior   = the code's PRIOR mean base composite (same code/side/
            period, months < M, windows pooled — the gate study's
            prior-vs-future mean-rp correlation 0.80-0.97 transplanted
            to the composite), CONF_PRIOR_NEUTRAL below
            CONF_PRIOR_MIN_POP prior bucket-periods.

Every factor is computed in the SIGNAL'S direction and floored at 0,
and every factor except the prior is horizon-free — so the composite
compares across periods, families and sec_types. This replaces the
former MAX(reverse_prob) confidence: rp saturates at long horizons
(gate-passers' 20d/60d rp sits at 0.6-1.0, quantized by 5-7
occurrences), so the old confidence mostly ranked buckets by horizon —
and long horizons mechanically realize larger |dir_ave|. The OOS study
(temp_scripts/study_confidence_oos.py, prior-half calibration →
future-half realized dir_ave) shows the composite ranks outcomes
better than rp WITHIN every period (e.g. mov_gap stock: next 0.62→
0.79, 5d 0.59→0.78, 20d 0.51→0.71, 60d 0.49→0.70 Spearman) while
spreading the argmax across periods instead of pinning the longest.

The full factor breakdown (t, sharpe, lift_prob, prior + the three
factor scores) is recorded per row in the params JSON under
confidence_factors; the argmax period under conf_period.

Per-security calibration columns (kept from the 2026-09 gate study —
prior-vs-future mean rp correlation 0.80-0.97; row metadata only,
they do not gate):
  - tier — MAX over QUALIFYING periods: 'proven' (2) when the code's
    prior mean composite >= CONF_PROVEN (top ~10% of codes by prior
    bucket quality), 'proven_dir' (1) when the code's prior mean
    DIRECTIONAL move >= PROVEN_DIR_AVE, else 'standard' (0). Code
    stats need >= PROVEN_MIN_POP prior bucket-periods.
  - code_baseline — the code's prior mean composite for the
    confidence's argmax period.
  - code_rank — coarse within-code percentile FLOOR of the confidence
    (from the code's own prior P25/P50/P75/P90/P95 of the composite),
    NULL below RANK_MIN_POP prior bucket-periods.

All qualification / means / factor math run in SQL (percentile_cont
interpolates linearly; factors are GREATEST-clamped at 0 so the
ungated prior population cannot underflow exp or credit contrary
buckets). The returned ConfirmMap keeps the legacy shape per
(stat_month, matrix_key, side) with the per-code arrays extended by
the argmax period + factor JSON, so the signal engines only gain new
params fields.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from analyze.analysis_signals.signals._base import ConfirmMap
from analyze.analysis_signals.config import (
    CONF_LIFT_PROB_ANCHOR,
    CONF_PRIOR_MIN_POP,
    CONF_PRIOR_NEUTRAL,
    CONF_PROVEN,
    CONF_SHARPE_ANCHOR,
    CONF_T_ANCHOR,
    CONF_W_CALIBRATION,
    CONF_W_CONSISTENCY,
    CONF_W_EFFICIENCY,
    CONF_W_EVIDENCE,
    GATE_MIN_OCCURRENCE,
    GATE_RP_MIN,
    GATE_T_STAT_MIN,
    PROVEN_DIR_AVE,
    PROVEN_MIN_POP,
    RANK_MIN_POP,
)

# Within-code prior quantile levels for the coarse confidence rank.
_RANK_LEVELS = (0.25, 0.50, 0.75, 0.90, 0.95)

# SQL expression of the BASE composite (evidence + efficiency +
# consistency — no prior term). Inputs t / sh / lp are GREATEST-clamped
# at 0 (a contrary bucket scores 0, not a reborn positive), and the
# exp arguments are LEAST-bounded so extreme sharpe/lift values cannot
# underflow float8 exp() (Windows Postgres raises on underflow).
_BASE_CONF_SQL = (
    "("
    + repr(CONF_W_EVIDENCE)
    + " * (GREATEST(t, 0) / (GREATEST(t, 0) + "
    + repr(CONF_T_ANCHOR) + ")) + "
    + repr(CONF_W_EFFICIENCY)
    + " * (1 - exp(-LEAST(GREATEST(sh, 0), 50) / "
    + repr(CONF_SHARPE_ANCHOR) + ")) + "
    + repr(CONF_W_CONSISTENCY)
    + " * (1 - exp(-LEAST(GREATEST(lp, 0), 10) / "
    + repr(CONF_LIFT_PROB_ANCHOR) + "))"
    + ")"
)


async def fetch_confirm(
    conn,
    sec_type: str,
    months: list[date],
    mov_table: str,
    win_col: str,
    config_filter: str,
    matrix_key,
) -> ConfirmMap:
    """Confirmed-code calibration sets for one bucket family under the
    reversal gate (shared by every signal engine).

    Args:
        mov_table: analysis_forecasts.mov_{rsi,std,gap} / px_vol_state /
              margin_ratio_state.
        win_col: the bucket's window column (rsi_window / ma_window /
              gap_window / px_speed / ratio_state).
        config_filter: extra SQL filter on the mov table ("pct = 1" /
              "k::float8 = 2.0" / "TRUE").
        matrix_key: window value → the engine's matrix key
              ("rsi_{w}" / "ma_{w}" / "gap_{w}" / the state string).

    Returns {(stat_month, matrix_key, side): (codes, confidences,
    tier_pts, baselines, ranks, periods, factors)} — confidences are
    the buckets' driving-factor composite at the argmax-composite
    QUALIFYING period (forecast-result rule: material rp + mean
    reversal + probability lift + magnitude lift + real sample +
    significant mean, see the module docstring); tier_pts 2/1/0 =
    proven / proven_dir / standard (MAX over qualifying periods);
    baselines / ranks are the code's prior mean composite and
    within-code percentile floor for the confidence's argmax period
    (NaN when unknown); periods are the argmax period strings
    ("next"/"5d"/"20d"/"60d"); factors are the per-code
    confidence_factors JSON objects (as text) for the params column.
    """
    rank_cases = "".join(
        " WHEN b.confidence >= c.q" + str(int(l * 100)) + " THEN "
        + repr(l) for l in reversed(_RANK_LEVELS)
    )

    rows = await conn.fetch(
        "WITH bp AS ("
        # The motivation tables carry code alone as their partition
        # key (2026-09 (code, forecast_id)-keyed shape) — the remaining
        # identity (sec_type, stat_month) + the bucket family resolve
        # through the forecast_identities registry ($3 = the mov
        # table's bucket name); the code-equality join predicate keeps
        # the mov side on its code-leading PK.
        "    SELECT i.stat_month, m." + win_col + " AS win, m.side, "
        "           i.code, "
        "           fr.period, fr.reverse_prob::float8 AS rp, "
        "           CASE WHEN m.side IN ('top', 'upper') "
        "                THEN -fr.ave_change::float8 "
        "                ELSE fr.ave_change::float8 END AS dir_ave, "
        "           fr.occurrence_count::float8 AS occ, "
        "           fr.std_change::float8 AS stdc, "
        "           CASE WHEN m.side IN ('top', 'upper') "
        "                THEN -br.base_ave_change::float8 "
        "                ELSE br.base_ave_change::float8 END AS base_dir, "
        "           CASE WHEN m.side IN ('top', 'upper') "
        "                THEN br.base_down_prob::float8 "
        "                ELSE br.base_up_prob::float8 END AS base_prob "
        "    FROM analysis_forecasts.forecast_identities i "
        "    JOIN " + mov_table + " m ON m.forecast_id = i.forecast_id "
        "         AND m.code = i.code "
        "    JOIN analysis_forecasts.forecast_results fr "
        "      ON fr.forecast_id = m.forecast_id "
        "    LEFT JOIN analysis_forecasts.base_rates br "
        "      ON br.sec_type = i.sec_type AND br.code = i.code "
        "     AND br.stat_month = i.stat_month AND br.period = fr.period "
        "    WHERE i.sec_type = $1 AND i.bucket = $3 AND "
        + config_filter + " "
        "      AND i.stat_month <= "
        "          (SELECT MAX(x) FROM unnest($2::date[]) x) "
        "), fx0 AS ("
        # The driving factors: significance (t), per-observation
        # efficiency (sharpe) and hit-rate lift over the base rate —
        # all in the bucket side's direction, all horizon-free.
        "    SELECT bp.*, "
        "           bp.dir_ave * sqrt(bp.occ) "
        "               / NULLIF(bp.stdc, 0) AS t, "
        "           bp.dir_ave / NULLIF(bp.stdc, 0) AS sh, "
        "           bp.rp - COALESCE(bp.base_prob, 0) AS lp "
        "    FROM bp "
        "), fx AS ("
        # The BASE composite (evidence + efficiency + consistency).
        "    SELECT *, " + _BASE_CONF_SQL + " AS base_conf "
        "    FROM fx0 "
        "), t AS ("
        "    SELECT DISTINCT unnest($2::date[]) AS target_month "
        "), code_thr AS ("
        # Per-code prior calibration over the PRIOR bucket-months only
        # (windows pooled per side/period): the prior mean BASE
        # composite (the calibration factor) + its quantiles (the rank
        # floor) + the prior mean directional move (proven_dir tier).
        "    SELECT t.target_month, fx.code, fx.side, fx.period, "
        + "".join(
            "percentile_cont(" + repr(l) + ") WITHIN GROUP "
            "(ORDER BY fx.base_conf) AS q" + str(int(l * 100)) + ", "
            for l in _RANK_LEVELS
        )
        + "           AVG(fx.base_conf) AS mean_conf, "
        "           AVG(fx.dir_ave) AS mean_dir_ave, "
        "           COUNT(fx.base_conf)::bigint AS n "
        "    FROM t JOIN fx ON fx.stat_month < t.target_month "
        "    GROUP BY t.target_month, fx.code, fx.side, fx.period "
        "), gated AS ("
        # Attach the prior factor (the code's prior mean composite,
        # neutral below CONF_PRIOR_MIN_POP) and the tier.
        "    SELECT fx.*, "
        "           CASE WHEN c.n >= " + str(CONF_PRIOR_MIN_POP) + " "
        "                THEN c.mean_conf ELSE "
        + repr(CONF_PRIOR_NEUTRAL) + " END AS prior, "
        "           CASE "
        "               WHEN c.n >= " + str(PROVEN_MIN_POP) + " "
        "                    AND c.mean_conf >= " + repr(CONF_PROVEN) + " "
        "               THEN 2 "
        "               WHEN c.n >= " + str(PROVEN_MIN_POP) + " "
        "                    AND c.mean_dir_ave >= "
        + repr(PROVEN_DIR_AVE) + " "
        "               THEN 1 ELSE 0 END AS tier_pts "
        "    FROM fx "
        "    JOIN t ON t.target_month = fx.stat_month "
        "    LEFT JOIN code_thr c ON c.target_month = fx.stat_month "
        "                        AND c.code = fx.code "
        "                        AND c.side = fx.side "
        "                        AND c.period = fx.period "
        "), qp AS ("
        # The forecast-result rule (all conjuncts) — the QUALIFYING
        # periods, with the full composite confidence.
        "    SELECT *, base_conf + "
        + repr(CONF_W_CALIBRATION) + " * prior AS confidence "
        "    FROM gated "
        "    WHERE rp > " + repr(GATE_RP_MIN) + " AND dir_ave > 0 "
        "      AND (base_prob IS NULL OR rp > base_prob) "
        "      AND (base_dir IS NULL OR dir_ave > base_dir) "
        "      AND occ >= " + str(GATE_MIN_OCCURRENCE) + " "
        "      AND dir_ave * sqrt(occ) >= "
        + repr(GATE_T_STAT_MIN) + " * stdc "
        "), qual AS ("
        "    SELECT stat_month, win, side, code, MAX(tier_pts) AS tier_pts "
        "    FROM qp "
        "    GROUP BY stat_month, win, side, code "
        "), best AS ("
        # Argmax-composite qualifying period = the confidence's period.
        "    SELECT DISTINCT ON (stat_month, win, side, code) "
        "           stat_month, win, side, code, "
        "           period AS best_period, confidence, t, sh, lp, prior "
        "    FROM qp "
        "    ORDER BY stat_month, win, side, code, confidence DESC, "
        "             period "
        ") "
        "SELECT q.stat_month, q.win, q.side, "
        "       array_agg(q.code) AS codes, "
        "       array_agg(q.confidence) AS confidences, "
        "       array_agg(q.tier_pts) AS tier_pts, "
        "       array_agg(q.baseline) AS baselines, "
        "       array_agg(q.srank) AS ranks, "
        "       array_agg(q.best_period) AS periods, "
        "       array_agg(jsonb_build_object( "
        "           't', round(q.t::numeric, 4), "
        "           'sharpe', round(q.sh::numeric, 4), "
        "           'lift_prob', round(q.lp::numeric, 4), "
        "           'prior', round(q.prior::numeric, 4), "
        "           'f_evidence', round((GREATEST(q.t, 0) / "
        "               (GREATEST(q.t, 0) + "
        + repr(CONF_T_ANCHOR) + "))::numeric, 4), "
        "           'f_efficiency', round((1 - "
        "               exp(-LEAST(GREATEST(q.sh, 0), 50) / "
        + repr(CONF_SHARPE_ANCHOR) + "))::numeric, 4), "
        "           'f_consistency', round((1 - "
        "               exp(-LEAST(GREATEST(q.lp, 0), 10) / "
        + repr(CONF_LIFT_PROB_ANCHOR) + "))::numeric, 4)"
        "       )::text) AS factors "
        "FROM ("
        "    SELECT b.stat_month, b.win, b.side, b.code, b.confidence, "
        "           b.best_period, b.t, b.sh, b.lp, b.prior, "
        "           ql.tier_pts, "
        "           c.mean_conf AS baseline, "
        "           CASE WHEN c.n >= " + str(RANK_MIN_POP) + " THEN "
        "               CASE" + rank_cases + " ELSE 0.0 END "
        "           END AS srank "
        "    FROM best b "
        "    JOIN qual ql ON ql.stat_month = b.stat_month "
        "                AND ql.win = b.win AND ql.side = b.side "
        "                AND ql.code = b.code "
        "    LEFT JOIN code_thr c ON c.target_month = b.stat_month "
        "                        AND c.code = b.code "
        "                        AND c.side = b.side "
        "                        AND c.period = b.best_period "
        ") q "
        "GROUP BY q.stat_month, q.win, q.side",
        sec_type, months, mov_table.split(".")[-1],
    )
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
