"""One-off offline test: verify compute_ohlc_columns against the
today-constrained mixed close/intraday anchor semantics (temporary
diagnostic).

Checks per window W (cooldown = max(1, int(0.2*W))):
  1. high_Wd  == CLOSE at high_date_Wd  (top-high anchor value = close)
  2. low_Wd   == CLOSE at low_date_Wd   (top-low anchor value = close)
  3. high_2nd_Wd == INTRADAY HIGH at high_2nd_date_Wd
  4. low_2nd_Wd  == INTRADAY LOW  at low_2nd_date_Wd
  5. every anchor date is MORE THAN cooldown trading days before the row
     date (anchor-vs-today separation)
  6. 2nd anchor pos - top anchor pos > cooldown — the 2nd anchor lies
     strictly AFTER the top anchor, more than cooldown trading days later
     (roof/floor line runs forward in time)
  7. rows within cooldown+1 of history start have NULL anchors (no
     qualifying date exists)
"""
from __future__ import annotations

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np
import pandas as pd

from analyze.mov_ave_spread.ohlc import compute_ohlc_columns

rng = np.random.default_rng(42)
n = 300
dates = pd.bdate_range("2024-01-01", periods=n)
price = 100 + np.cumsum(rng.normal(0, 1, n))
high = price + rng.uniform(0, 1, n)
low = price - rng.uniform(0, 1, n)
open_ = price + rng.normal(0, 0.5, n)

df = pd.DataFrame({
    "sec_type": ["etf"] * n,
    "code": ["TEST.SZ"] * n,
    "date": dates,
    "price": price,
    "open": open_,
    "high": high,
    "low": low,
})

out = compute_ohlc_columns(df)
pos = pd.Series(np.arange(n), index=out.index)
date_to_pos = pd.Series(np.arange(n), index=pd.DatetimeIndex(dates))

failures: list[str] = []

for w in (20, 60, 120):
    cd = max(1, int(w * 0.20))
    cols = {
        "h_val": f"high_{w}d", "h_date": f"high_date_{w}d",
        "h2_val": f"high_2nd_{w}d", "h2_date": f"high_2nd_date_{w}d",
        "l_val": f"low_{w}d", "l_date": f"low_date_{w}d",
        "l2_val": f"low_2nd_{w}d", "l2_date": f"low_2nd_date_{w}d",
    }
    counts = {c: int(out[c].notna().sum()) for c in cols.values()}
    print(f"window {w}d non-null counts:", counts)

    # 7. early rows (<= cooldown rows of history) have NULL top anchors
    #    (row cd+1 is the first that can anchor at position 0, distance cd+1)
    early_null = out.loc[:cd, [cols["h_val"], cols["l_val"]]].isna().all(axis=None)
    print(f"  early rows (<= {cd}) NULL top anchors: {early_null}")
    if not early_null:
        failures.append(f"w={w}: top anchors non-NULL within cooldown rows")

    # anchor positions (NaN -> -1 sentinel)
    h_pos = out[cols["h_date"]].map(date_to_pos)
    h2_pos = out[cols["h2_date"]].map(date_to_pos)
    l_pos = out[cols["l_date"]].map(date_to_pos)
    l2_pos = out[cols["l2_date"]].map(date_to_pos)

    m_h = h_pos.notna()
    m_h2 = h2_pos.notna()
    m_l = l_pos.notna()
    m_l2 = l2_pos.notna()

    # 5. anchor-vs-today separation (both top and 2nd)
    for name, m, p in (("high", m_h, h_pos), ("high_2nd", m_h2, h2_pos),
                       ("low", m_l, l_pos), ("low_2nd", m_l2, l2_pos)):
        sep_ok = ((pos[m] - p[m]) > cd).all()
        if not sep_ok:
            failures.append(f"w={w}: {name} anchor within cooldown of today")

    # 1./2. top anchor value == close at anchor date
    for name, m, p, vcol in (("high", m_h, h_pos, cols["h_val"]),
                             ("low", m_l, l_pos, cols["l_val"])):
        vals = out.loc[m, vcol].to_numpy(dtype=float)
        closes = price[p[m].to_numpy(dtype=int)]
        if not np.allclose(vals, closes):
            failures.append(f"w={w}: {name} value != close at anchor date")

    # 3. high_2nd value == intraday high at anchor date
    if m_h2.any():
        vals = out.loc[m_h2, cols["h2_val"]].to_numpy(dtype=float)
        highs = high[h2_pos[m_h2].to_numpy(dtype=int)]
        if not np.allclose(vals, highs):
            failures.append(f"w={w}: high_2nd value != intraday high at anchor date")

    # 4. low_2nd value == intraday low at anchor date
    if m_l2.any():
        vals = out.loc[m_l2, cols["l2_val"]].to_numpy(dtype=float)
        lows = low[l2_pos[m_l2].to_numpy(dtype=int)]
        if not np.allclose(vals, lows):
            failures.append(f"w={w}: low_2nd value != intraday low at anchor date")

    # 6. 2nd-vs-top: the 2nd anchor must lie strictly AFTER the top
    #    anchor, more than cooldown trading days later — the 2nd date is
    #    always later than the top date (roof/floor line runs forward in
    #    time).
    m_both_h = m_h & m_h2
    if m_both_h.any():
        if not ((h2_pos[m_both_h] - h_pos[m_both_h]) > cd).all():
            failures.append(f"w={w}: high 2nd anchor not > cooldown days AFTER top")
    m_both_l = m_l & m_l2
    if m_both_l.any():
        if not ((l2_pos[m_both_l] - l_pos[m_both_l]) > cd).all():
            failures.append(f"w={w}: low 2nd anchor not > cooldown days AFTER top")

    print(f"  sample last row:", {c: str(out[c].iloc[-1]) for c in cols.values()})

if failures:
    print("\nFAILURES:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("\nAll anchor-semantics checks passed.")
