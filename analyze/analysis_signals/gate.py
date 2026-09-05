"""Adaptive forecast-confirmation gate for analysis_signals.

Replaces the fixed ``reverse_prob > 0`` bucket gate with the
self-adaptive population-quantile rule QRp_P90 plus the per-security
layers selected by the per-security gate study (2026-09,
temp_scripts/study_per_security_signals.py). Calibration mirrors the
study's populations exactly: for each (side, forecast period) the
threshold derives from reverse_prob over ALL buckets of the same
sec_type + signal family from ALL stat_months strictly BEFORE the
target month M (an M-1 calibration gate — a month's threshold never
sees its own outcomes, so no look-ahead).

Threshold modes (per family, ``hybrid`` flag):
  - SEC QRp_P90 (mov_rsi): rp >= the population P90. The mov_rsi rp
    distribution is saturated at 1.0 (21-24% of bucket-periods), where
    the per-code rank gate degenerates; per-security differentiation
    for mov_rsi happens in the tier columns instead.
  - HYB QRp_P90 (mov_std / mov_gap): threshold = w·code_P90 +
    (1-w)·population_P90 with shrinkage weight w = code_n/(code_n +
    K_SHRINK); below HYBRID_MIN_POP prior bucket-periods the weight is
    0 (pure population gate). OOS: uniformly tighter mean rp / dir_ave
    than the population-only gate at modestly lower volume.

A bucket qualifies when AT LEAST ONE period (next / 5d / 20d / 60d)
has reverse_prob >= its calibrated threshold AND the code's prior
mean reverse_prob for that (side, period) — the same M-1 per-code
mean the tier / baseline columns read — is positive where known:
besides the single bucket-period clearing its threshold, the code's
own history must also see reverse (mean rp > 0 — the legacy
``reverse_prob > 0`` bar applied to the mean; an unknown mean — the
code has no prior bucket-periods for that side/period — does not
block, the same fall-back-don't-fail convention as the HYB weight /
tier / rank layers). Consistent with the row's confidence semantics
(confidence = cross-period MAX(reverse_prob)).

Per-security calibration columns (study: prior-vs-future mean rp
correlation 0.80-0.97):
  - tier — MAX over QUALIFYING periods: 'proven' (2) when the code's
    prior mean rp >= PROVEN_RP, 'proven_dir' (1) when the code's prior
    mean DIRECTIONAL move >= PROVEN_DIR_AVE, else 'standard' (0).
    Code stats need >= PROVEN_MIN_POP prior bucket-periods.
  - code_baseline — the code's prior mean rp for the confidence's
    argmax period.
  - code_rank — coarse within-code percentile FLOOR of the confidence
    (from the code's own prior P25/P50/P75/P90/P95), NULL below
    RANK_MIN_POP prior bucket-periods.

Cold-start fallback: when a (target month, side, period) population
has fewer than GATE_MIN_POP bucket-periods, the calibrated quantile is
meaningless and that period falls back to the legacy
``reverse_prob > 0`` rule.

All quantiles / means / qualification run in SQL (percentile_cont
interpolates linearly — the same definition as the study's numpy
quantiles). The returned ConfirmMap keeps the legacy shape per
(stat_month, matrix_key, side) with three added per-code arrays, so
the signal engines only gain new row fields.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from analyze.analysis_signals.signals._base import ConfirmMap
from analyze.analysis_signals.config import (
    GATE_MIN_POP,
    GATE_Q,
    HYBRID_MIN_POP,
    K_SHRINK,
    PROVEN_DIR_AVE,
    PROVEN_MIN_POP,
    PROVEN_RP,
    RANK_MIN_POP,
)

# Within-code prior quantile levels for the coarse confidence rank.
_RANK_LEVELS = (0.25, 0.50, 0.75, 0.90, 0.95)


async def fetch_confirm(
    conn,
    sec_type: str,
    months: list[date],
    mov_table: str,
    win_col: str,
    config_filter: str,
    matrix_key,
    *,
    hybrid: bool,
    code_col: str = "code",
) -> ConfirmMap:
    """Confirmed-code sets for one percentile/band family under the
    adaptive per-security gate (shared by the rsi / std / gap engines).

    Args:
        mov_table: analysis_forecasts.mov_{rsi,std,gap}.
        win_col: the bucket's window column (rsi_window / ma_window /
              gap_window).
        config_filter: extra SQL filter on the mov table ("pct = 1" /
              "k::float8 = 2.0").
        matrix_key: window value → the engine's matrix key
              ("rsi_{w}" / "ma_{w}" / "gap_{w}").
        hybrid: True → HYB QRp_P90 threshold (per-code shrinkage, the
              mov_std / mov_gap mode); False → pure population QRp_P90
              (the mov_rsi mode).
        code_col: the mov table column the per-security calibration
              groups by — "code" for the per-security families, the
              TARGET industry ("pair_industry_id") for the opp_pair
              pair buckets (the signal is emitted on the target, so
              its prior history must calibrate the target).

    Returns {(stat_month, matrix_key, side): (codes, confidences,
    tier_pts, baselines, ranks)} — confidences are the buckets'
    cross-period MAX(reverse_prob); tier_pts 2/1/0 = proven /
    proven_dir / standard (MAX over qualifying periods); baselines /
    ranks are the code's prior mean rp and within-code percentile
    floor for the confidence's argmax period (NaN when unknown).
    """
    # Calibrated pass threshold: NULL → legacy rp > 0 fallback. HYB adds
    # the per-code shrinkage blend on top of the population P90. On top
    # of it, qual ANDs the mean-reversal rule: the code's own prior mean
    # rp for the same (side, period) must also see reverse (> 0) where
    # known — unknown (no prior bucket-periods) does not block.
    if hybrid:
        thr_expr = (
            "CASE WHEN s.q IS NULL OR s.n < " + str(GATE_MIN_POP) + " "
            "THEN NULL "
            "WHEN c.n IS NULL OR c.n < " + str(HYBRID_MIN_POP) + " "
            "THEN s.q "
            "ELSE (c.n::float8 / (c.n + " + str(K_SHRINK) + ")) * c.q90 "
            "+ (1.0 - c.n::float8 / (c.n + " + str(K_SHRINK) + ")) * s.q "
            "END"
        )
    else:
        thr_expr = (
            "CASE WHEN s.q IS NULL OR s.n < " + str(GATE_MIN_POP) + " "
            "THEN NULL ELSE s.q END"
        )

    rank_cases = "".join(
        " WHEN b.confidence >= c.q" + str(int(l * 100)) + " THEN "
        + repr(l) for l in reversed(_RANK_LEVELS)
    )

    rows = await conn.fetch(
        "WITH bp AS ("
        "    SELECT m.stat_month, m." + win_col + " AS win, m.side, "
        "           m." + code_col + " AS code, "
        "           fr.period, fr.reverse_prob::float8 AS rp, "
        "           CASE WHEN m.side IN ('top', 'upper') "
        "                THEN -fr.ave_change::float8 "
        "                ELSE fr.ave_change::float8 END AS dir_ave "
        "    FROM " + mov_table + " m "
        "    JOIN analysis_forecasts.forecast_results fr "
        "      ON fr.forecast_id = m.forecast_id "
        "    WHERE m.sec_type = $1 AND " + config_filter + " "
        "      AND m.stat_month <= "
        "          (SELECT MAX(x) FROM unnest($2::date[]) x) "
        "), t AS ("
        "    SELECT DISTINCT unnest($2::date[]) AS target_month "
        "), sec_thr AS ("
        "    SELECT t.target_month, bp.side, bp.period, "
        "           percentile_cont(" + repr(GATE_Q) + ") WITHIN GROUP "
        "               (ORDER BY bp.rp) AS q, "
        "           COUNT(*)::bigint AS n "
        "    FROM t JOIN bp ON bp.stat_month < t.target_month "
        "    GROUP BY t.target_month, bp.side, bp.period "
        "), code_thr AS ("
        "    SELECT t.target_month, bp.code, bp.side, bp.period, "
        + "".join(
            "percentile_cont(" + repr(l) + ") WITHIN GROUP "
            "(ORDER BY bp.rp) AS q" + str(int(l * 100)) + ", "
            for l in _RANK_LEVELS
        )
        + "           AVG(bp.rp) AS mean_rp, "
        "           AVG(bp.dir_ave) AS mean_dir_ave, "
        "           COUNT(*)::bigint AS n "
        "    FROM t JOIN bp ON bp.stat_month < t.target_month "
        "    GROUP BY t.target_month, bp.code, bp.side, bp.period "
"), gated AS ("
"    SELECT bp.stat_month, bp.win, bp.side, bp.code, bp.rp, "
+ thr_expr + " AS pass_thr, "
"           c.mean_rp AS mean_rp, "
"           CASE "
        "               WHEN c.n >= " + str(PROVEN_MIN_POP) + " "
        "                    AND c.mean_rp >= " + repr(PROVEN_RP) + " "
        "               THEN 2 "
        "               WHEN c.n >= " + str(PROVEN_MIN_POP) + " "
        "                    AND c.mean_dir_ave >= "
        + repr(PROVEN_DIR_AVE) + " "
        "               THEN 1 ELSE 0 END AS tier_pts "
        "    FROM bp "
        "    JOIN t ON t.target_month = bp.stat_month "
        "    LEFT JOIN sec_thr s ON s.target_month = bp.stat_month "
        "                   AND s.side = bp.side AND s.period = bp.period "
        "    LEFT JOIN code_thr c ON c.target_month = bp.stat_month "
        "                        AND c.code = bp.code "
        "                        AND c.side = bp.side "
        "                        AND c.period = bp.period "
"), qual AS ("
"    SELECT stat_month, win, side, code, MAX(tier_pts) AS tier_pts "
"    FROM gated "
"    WHERE CASE WHEN pass_thr IS NOT NULL "
"               THEN rp >= pass_thr ELSE rp > 0 END "
"         AND (mean_rp IS NULL OR mean_rp > 0) "
"    GROUP BY stat_month, win, side, code "
        "), best AS ("
        "    SELECT DISTINCT ON (stat_month, win, side, code) "
        "           stat_month, win, side, code, "
        "           period AS best_period, rp AS confidence "
        "    FROM bp JOIN t ON t.target_month = bp.stat_month "
        "    WHERE rp IS NOT NULL "
        "    ORDER BY stat_month, win, side, code, rp DESC, period "
        ") "
        "SELECT q.stat_month, q.win, q.side, "
        "       array_agg(q.code) AS codes, "
        "       array_agg(q.confidence) AS confidences, "
        "       array_agg(q.tier_pts) AS tier_pts, "
        "       array_agg(q.baseline) AS baselines, "
        "       array_agg(q.srank) AS ranks "
        "FROM ("
        "    SELECT b.stat_month, b.win, b.side, b.code, b.confidence, "
        "           ql.tier_pts, "
        "           c.mean_rp AS baseline, "
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
        sec_type, months,
    )
    return {
        (r["stat_month"], matrix_key(r["win"]), r["side"]): (
            np.asarray(r["codes"]),
            np.asarray(r["confidences"], dtype=np.float64),
            np.asarray(r["tier_pts"], dtype=np.int64),
            np.asarray(r["baselines"], dtype=np.float64),
            np.asarray(r["ranks"], dtype=np.float64),
        )
        for r in rows
    }
