"""One-off benchmark: extrapolate compute_ohlc_columns wall time to
full-universe scale. (temporary diagnostic)"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

from analyze.mov_ave_spread.ohlc import compute_ohlc_columns

rng = np.random.default_rng(7)
n_codes = 20
n_days = 1700
n = n_codes * n_days

codes = np.repeat([f"C{i:03d}" for i in range(n_codes)], n_days)
dates = np.tile(pd.bdate_range("2019-01-01", periods=n_days), n_codes)

# Random-walk OHLC per code.
steps = rng.normal(0, 1, n)
price = 100 + np.cumsum(steps).reshape(n_codes, n_days).repeat(1, axis=0).reshape(-1)
price = 100 + np.concatenate([np.cumsum(rng.normal(0, 1, n_days)) for _ in range(n_codes)])

df = pd.DataFrame({
    "sec_type": "etf",
    "code": codes,
    "date": dates,
    "price": price,
    "open": price + rng.normal(0, 0.5, n),
    "high": price + rng.uniform(0, 1, n),
    "low": price - rng.uniform(0, 1, n),
})

t0 = time.time()
out = compute_ohlc_columns(df)
dt = time.time() - t0
print(f"rows={len(out):,}  wall={dt:.1f}s  -> {len(out)/dt:,.0f} rows/s")
print(f"extrapolated 1.55M rows: {1_550_000 / (len(out)/dt) / 60:.1f} min")
for c in ("high_date_20d", "high_2nd_20d", "high_2nd_date_20d",
          "low_date_1275d", "low_2nd_1275d", "low_2nd_date_1275d"):
    print(f"  {c}: non-null={int(out[c].notna().sum()):,}/{n:,}")
