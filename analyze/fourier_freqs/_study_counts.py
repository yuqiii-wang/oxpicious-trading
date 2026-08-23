"""Study: time-domain half-cycle counting on 000300's latest 1275d window.

Tests several extrema-detection variants and prints the gap-duration
histograms, to pick the method that matches the visible ~10d / ~20d
half-circle arcs on the price plot.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from _common.df_utils._activate import activate
activate()

import numpy as np
import pandas as pd
import psycopg2

conn = psycopg2.connect(host="127.0.0.1", port=9876, dbname="oxpicious-stats",
                        user="postgres", password="postgres")
cur = conn.cursor()
cur.execute("""
    SELECT date, close FROM stats.index_basic_stats
    WHERE code = '000300' ORDER BY date
""")
rows = cur.fetchall()
cur.close()
conn.close()

close = pd.DataFrame(rows, columns=["date", "close"])["close"].values.astype(np.float64)
N = 1275
w = close[-N:]  # latest 1275d window
print(f"window: {N} days, close range {w.min():.0f}..{w.max():.0f}")


def gap_histogram(x: np.ndarray) -> pd.Series:
    """Count half-cycle durations between consecutive alternating extrema."""
    # strict local maxima / minima (plateau-safe: >= on one side)
    is_max = np.zeros(len(x), dtype=bool)
    is_min = np.zeros(len(x), dtype=bool)
    d1 = np.diff(x)
    up = d1 > 0
    down = d1 < 0
    # local max: up then down
    is_max[1:-1] = up[:-1] & down[1:]
    # local min: down then up
    is_min[1:-1] = down[:-1] & up[1:]
    ext_idx = np.where(is_max | is_min)[0]
    # enforce alternation (keep first of consecutive same-type extrema)
    types = is_max[ext_idx].astype(int)  # 1=max, 0=min
    keep = np.ones(len(ext_idx), dtype=bool)
    for i in range(1, len(ext_idx)):
        if types[i] == types[i - 1]:
            keep[i] = False
    ext_idx = ext_idx[keep]
    gaps = np.diff(ext_idx)
    return pd.Series(gaps).value_counts().sort_index()


def moving_average(x: np.ndarray, n: int) -> np.ndarray:
    # centered MA, same length (edges: shrink window)
    out = np.empty_like(x)
    half = n // 2
    for i in range(len(x)):
        lo = max(0, i - half)
        hi = min(len(x), i + half + 1)
        out[i] = x[lo:hi].mean()
    return out


variants = {
    "raw extrema": w,
    "MA3 smoothed": moving_average(w, 3),
    "MA5 smoothed": moving_average(w, 5),
}

for name, x in variants.items():
    h = gap_histogram(x)
    total = h.sum()
    top = h.sort_values(ascending=False).head(8)
    print(f"\n[{name}] {total} half-cycles total")
    print("  top duration bins (days: count):",
          {int(d): int(c) for d, c in top.items()})
    # aggregate into 5d-wide bins for a coarser view
    bins = (h.index.values // 5) * 5
    agg = pd.Series(h.values, index=bins).groupby(level=0).sum()
    print("  5d-wide bins:", {int(k): int(v) for k, v in agg.items() if v > 0})
