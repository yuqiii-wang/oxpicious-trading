"""Base-rate monthly aggregation (analysis_forecasts).

Per stat month's trailing 5-year window [lo, hi) and horizon n, the
UNCONDITIONAL forward-change stats over ALL of each live code's window
days with a valid n-day forward change (vs the buckets' extreme-day
subsets): valid-day count, mean change, P(change < -1%),
P(change > +1%). These are the base rates the bucket results in
analysis_forecasts.forecast_results are read against (lift) — written
to analysis_forecasts.base_rates. Same full-window gate as the bucket
engines (a code is live only once its own history strictly precedes the
window start); one row per (code, period) where the count > 0.

Yields (stat_month, rows) so __main__ can write month-major.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    PERIOD_FOR_HORIZON,
    REVERSE_THRESHOLD,
)
from analyze.analysis_forecasts.wide import MonthWindow, round6


def compute_base_rate_rows(
    chg: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, base-rate rows) per stat month.

    Args:
        chg: shared change matrices (build_change_matrices):
             NC0_{n} (n-day forward change, 0.0 on invalid days) and
             FIN_{n} (validity bool) for n in FORWARD_HORIZONS.
        windows: resolved MonthWindow list for the target months.
        codes: sorted code list (matrix column order).
        sec_type: emitted into every row.
        first_ord: (C,) per-code first data date as ABSOLUTE epoch-day
              ordinals (first_ords_from_dates) — the full-window gate.
    """
    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue  # no grid rows in this window at all
        live = first_ord < mw.lo_ord
        if not live.any():
            continue

        rows: list[dict] = []
        for n in FORWARD_HORIZONS:
            fin = chg[f"FIN_{n}"][lo:hi] & live[None, :]
            cnt = fin.sum(axis=0)
            emit = cnt > 0
            if not emit.any():
                continue
            # NC0 is 0.0 on invalid days — masked sums equal valid-day
            # sums (same trick as aggregate_horizons_sparse).
            g = np.where(fin, chg[f"NC0_{n}"][lo:hi], 0.0)
            s = g.sum(axis=0)
            dn = (fin & (g < -REVERSE_THRESHOLD)).sum(axis=0)
            up = (fin & (g > REVERSE_THRESHOLD)).sum(axis=0)

            idx = np.nonzero(emit)[0]
            rows.extend(
                {
                    "sec_type": sec_type,
                    "code": codes[i],
                    "stat_month": mw.stat_month,
                    "period": PERIOD_FOR_HORIZON[n],
                    "base_count": int(cnt[i]),
                    "base_ave_change": round6(s[i] / cnt[i]),
                    "base_down_prob": round6(dn[i] / cnt[i]),
                    "base_up_prob": round6(up[i] / cnt[i]),
                }
                for i in idx.tolist()
            )

        if rows:
            yield mw.stat_month, rows
