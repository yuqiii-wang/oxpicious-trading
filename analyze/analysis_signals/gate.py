"""Adaptive forecast-confirmation gate for analysis_signals.

Replaces the fixed ``reverse_prob > 0`` bucket gate with the
self-adaptive population-quantile rule QRp_P90 (selected by the
adaptive-threshold study as the only rule valid across index / etf /
stock and split-half OOS stable). Calibration mirrors the study's
populations exactly: for each (side, forecast period) the threshold is
the P90 quantile of that period's reverse_prob over ALL buckets of the
same sec_type + signal family from ALL stat_months strictly BEFORE the
target month M (an M-1 calibration gate — a month's threshold never
sees its own outcomes, so no look-ahead). A bucket qualifies when AT
LEAST ONE period (next / 5d / 20d / 60d) has reverse_prob >= its
calibrated threshold — consistent with the row's confidence semantics
(confidence = cross-period MAX(reverse_prob); a qualifying bucket is
top-decile for the horizon that justifies it).

Population quantiles make the gate self-adaptive per security type and
market regime: hard-to-reverse regimes raise the bar, easy ones lower
it, and index/etf/stock base-rate differences are absorbed by each
population instead of a hardcoded constant.

Cold-start fallback: when a (target month, side, period) population
has fewer than GATE_MIN_POP bucket-periods, the calibrated quantile is
meaningless and that period falls back to the legacy
``reverse_prob > 0`` rule.

Both the per-(target month, side, period) quantiles and the per-code
qualification run in SQL (percentile_cont interpolates linearly — the
same definition as the study's numpy quantiles). The returned
ConfirmMap shape is identical to the legacy gate: per (stat_month,
matrix_key, side) confirmed codes and their confidence values, so the
signal engines and everything downstream are unchanged.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from analyze.analysis_signals.compute import ConfirmMap
from analyze.analysis_signals.config import GATE_MIN_POP, GATE_Q


async def fetch_confirm(
    conn,
    sec_type: str,
    months: list[date],
    mov_table: str,
    win_col: str,
    config_filter: str,
    matrix_key,
) -> ConfirmMap:
    """Confirmed-code sets for one percentile/band family under the
    adaptive QRp_P90 gate (shared by the rsi / std / gap engines).

    Args:
        mov_table: analysis_forecasts.mov_{rsi,std,gap}.
        win_col: the bucket's window column (rsi_window / ma_window /
              gap_window).
        config_filter: extra SQL filter on the mov table ("pct = 1" /
              "k::float8 = 2.0").
        matrix_key: window value → the engine's matrix key
              ("rsi_{w}" / "ma_{w}" / "gap_{w}").

    Returns {(stat_month, matrix_key, side): (codes, confidences)} —
    confidences are the buckets' cross-period MAX(reverse_prob).
    """
    rows = await conn.fetch(
        "WITH bp AS ("
        "    SELECT m.stat_month, m." + win_col + " AS win, m.side, m.code, "
        "           fr.period, fr.reverse_prob::float8 AS rp "
        "    FROM " + mov_table + " m "
        "    JOIN analysis_forecasts.forecast_results fr "
        "      ON fr.forecast_id = m.forecast_id "
        "    WHERE m.sec_type = $1 AND " + config_filter + " "
        "      AND m.stat_month <= "
        "          (SELECT MAX(x) FROM unnest($2::date[]) x) "
        "), b AS ("
        "    SELECT stat_month, win, side, code, MAX(rp) AS confidence "
        "    FROM bp GROUP BY stat_month, win, side, code "
        "), thr AS ("
        "    SELECT t.target_month, bp.side, bp.period, "
        "           percentile_cont(" + repr(GATE_Q) + ") WITHIN GROUP "
        "               (ORDER BY bp.rp) AS q, "
        "           COUNT(*)::bigint AS n "
        "    FROM (SELECT DISTINCT unnest($2::date[]) AS target_month) t "
        "    JOIN bp ON bp.stat_month < t.target_month "
        "    GROUP BY t.target_month, bp.side, bp.period "
        "), qual AS ("
        "    SELECT bp.stat_month, bp.win, bp.side, bp.code "
        "    FROM bp "
        "LEFT JOIN thr ON thr.target_month = bp.stat_month "
        "            AND thr.side = bp.side AND thr.period = bp.period "
        "    WHERE CASE WHEN thr.q IS NOT NULL "
        "               AND thr.n >= " + str(GATE_MIN_POP) + " "
        "               THEN bp.rp >= thr.q "
        "               ELSE bp.rp > 0 END "
        "    GROUP BY bp.stat_month, bp.win, bp.side, bp.code "
        ") "
        "SELECT b.stat_month, b.win, b.side, "
        "       array_agg(b.code) AS codes, "
        "       array_agg(b.confidence) AS confidences "
        "FROM b "
        "JOIN qual q ON q.stat_month = b.stat_month AND q.win = b.win "
        "           AND q.side = b.side AND q.code = b.code "
        "GROUP BY b.stat_month, b.win, b.side",
        sec_type, months,
    )
    return {
        (r["stat_month"], matrix_key(r["win"]), r["side"]): (
            np.asarray(r["codes"]),
            np.asarray(r["confidences"], dtype=np.float64),
        )
        for r in rows
    }
