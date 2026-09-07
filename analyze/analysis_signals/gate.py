"""Forecast-confirmation gate for analysis_signals.

A detected extreme day is RECORDED only when its matching
analysis_forecasts bucket (same code/sec_type/stat_month/window/side/
pct|k/cooldown config) qualifies under the ABSOLUTE reversal rule:
at least ONE forecast_results period (next / 5d / 20d / 60d) must have

  - reverse P > 1% — ``reverse_prob > GATE_RP_MIN``: the bucket's
    probability of a reversal beyond its adaptive reverse_threshold
    is material (a bare ``> 0`` tail is not enough), AND
  - a MEAN REVERSAL — the period's mean forward change in the
    bucket's direction (``dir_ave`` = ±ave_change, sign-flipped for
    top/upper buckets) is > 0: the bucket's average outcome reverses,
    so the signal holds — a fat reversal tail with a continuing mean
    does not.

Both conjuncts read the bucket's OWN historical outcomes from
analysis_forecasts.forecast_results (via the bucket's forecast_id) —
no population calibration and no look-ahead concern: a month's gate
only sees that month's own bucket statistics, exactly what the
forecast table already recorded for it.

Per-security calibration columns (kept from the 2026-09 gate study —
prior-vs-future mean rp correlation 0.80-0.97; row metadata only,
they do not gate):
  - tier — MAX over QUALIFYING periods: 'proven' (2) when the code's
    prior mean rp >= PROVEN_RP, 'proven_dir' (1) when the code's prior
    mean DIRECTIONAL move >= PROVEN_DIR_AVE, else 'standard' (0).
    Code stats need >= PROVEN_MIN_POP prior bucket-periods.
  - code_baseline — the code's prior mean rp for the confidence's
    argmax period.
  - code_rank — coarse within-code percentile FLOOR of the confidence
    (from the code's own prior P25/P50/P75/P90/P95), NULL below
    RANK_MIN_POP prior bucket-periods.

All qualification / means run in SQL (percentile_cont interpolates
linearly). The returned ConfirmMap keeps the legacy shape per
(stat_month, matrix_key, side) with three added per-code arrays, so
the signal engines only gain new row fields.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from analyze.analysis_signals.signals._base import ConfirmMap
from analyze.analysis_signals.config import (
    GATE_RP_MIN,
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
    code_col: str = "code",
) -> ConfirmMap:
    """Confirmed-code sets for one bucket family under the reversal
    gate (shared by every signal engine).

    Args:
        mov_table: analysis_forecasts.mov_{rsi,std,gap} / px_vol_state /
              margin_ratio_state / opp_pair_state.
        win_col: the bucket's window column (rsi_window / ma_window /
              gap_window / px_speed / ratio_state / trend_window).
        config_filter: extra SQL filter on the mov table ("pct = 1" /
              "k::float8 = 2.0" / "TRUE").
        matrix_key: window value → the engine's matrix key
              ("rsi_{w}" / "ma_{w}" / "gap_{w}" / the state string).
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
        "    SELECT bp.stat_month, bp.win, bp.side, bp.code, "
        "           bp.rp, bp.dir_ave, "
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
        "    LEFT JOIN code_thr c ON c.target_month = bp.stat_month "
        "                        AND c.code = bp.code "
        "                        AND c.side = bp.side "
        "                        AND c.period = bp.period "
        "), qual AS ("
        "    SELECT stat_month, win, side, code, MAX(tier_pts) AS tier_pts "
        "    FROM gated "
        "    WHERE rp > " + repr(GATE_RP_MIN) + " AND dir_ave > 0 "
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
