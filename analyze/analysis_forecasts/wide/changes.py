"""Forward-change wide matrices (analyze.analysis_forecasts.wide.changes).

Wide endpoint-change + path-extreme matrices shared by every bucket
engine: the forward quantities the monthly aggregation reduces over.
Input is the fetched long frame (cudf.pandas DataFrame — the
next_change / path columns were derived on it by the fetch layer's
vectorized grouped ops); this module only scatters them through
grid.scatter_column (the single pandas→numpy boundary).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.analysis_forecasts.config import FORWARD_HORIZONS, MM_HORIZONS

from .grid import scatter_column


def build_change_matrices(
    df: pd.DataFrame,
    shape: tuple[int, int],
    didx: np.ndarray,
    cidx: np.ndarray,
) -> dict[str, np.ndarray]:
    """Wide matrices derived from the forward-change columns.

    Keys (n = forward horizon in trading days):
      NC0_{n} — next_change_{n}d with NaN→0 (einsum-safe sums)
      FIN_{n} — validity bool (day has a finite n-day forward change)
      FMAX0_{n} / FMIN0_{n} — MM horizons only: the n-day forward
              WINDOW's signed close extremes vs the signal close
              (fetch.add_path_extremes path_high_{n}d / path_low_{n}d)
              with NaN→0 (invalid days never fire a threshold compare),
              the swing the reversal event consumes. At the next-day
              horizon the path IS the endpoint
              (NC0_1), so no separate matrices exist there.
    """
    mats: dict[str, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        nc = scatter_column(df, f"next_change_{n}d", shape, didx, cidx)
        fin = np.isfinite(nc)
        mats[f"NC0_{n}"] = np.where(fin, nc, 0.0)
        mats[f"FIN_{n}"] = fin
        if n in MM_HORIZONS:
            for key, col in (("FMAX0", "path_high_{n}d"),
                             ("FMIN0", "path_low_{n}d")):
                pc = scatter_column(df, col.format(n=n), shape, didx, cidx)
                mats[f"{key}_{n}"] = np.where(np.isfinite(pc), pc, 0.0)
    return mats
