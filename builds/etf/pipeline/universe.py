"""Step 5b — per-ETF universe (sec_classification quality stats source)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from builds.etf.theme import classify_etf_theme


def build_universe(merged: pd.DataFrame, comp_universe: pd.DataFrame | None) -> pd.DataFrame:
    """Groupby-aggregate per-ETF coverage stats + theme classification.

    All lookups are vectorized joins on frames — no dict hops, no row-wise
    list comprehensions. ``classify_etf_theme`` is an inherently scalar
    keyword-rule classifier (host-only); applied once via Series.map.
    """
    _g = merged.groupby("code", sort=True)
    sizes = _g.size()
    first_d = _g["date"].min().dt.strftime("%Y-%m-%d")
    last_d = _g["date"].max().dt.strftime("%Y-%m-%d")

    if "rz_balance" in merged.columns:
        n_margin = (
            (merged["rz_balance"].fillna(0) > 0)
            .groupby(merged["code"]).sum().astype("int64")
        )
    else:
        n_margin = sizes * 0

    # ``code`` is carried as an explicit column (never the index) so later
    # merges/joins cannot duplicate it on reset.  ``exchange`` comes straight
    # from the canonical CSV column (downloads guarantees it) — conditions
    # must branch on it, never on code-suffix string ops.
    uni = pd.DataFrame({
        "code": sizes.index.to_series(index=sizes.index),
        "exchange": _g["exchange"].first(),
        "name": _g["name"].first(),
        "n_ohlcv_days": sizes,
        "n_margin_days": n_margin,
        "first_date": first_d,
        "last_date": last_d,
    })

    # Theme classification — scalar keyword rules via one Series.map pass;
    # (id, label, slug) tuples unpacked to an (n, 3) object array once.
    names = uni["name"]
    th = np.asarray(
        names.where(names.notna(), "").map(classify_etf_theme).tolist()
    )
    uni["theme_id"] = th[:, 0]
    uni["theme_label"] = th[:, 1]
    uni["theme_slug"] = th[:, 2]

    # Composition coverage — vectorized left-join on whole suffixed codes.
    if comp_universe is not None and len(comp_universe):
        cu = comp_universe.rename(columns={"etf_code": "code"})[
            ["code", "n_dates", "n_holdings_latest"]]
        uni = uni.merge(cu, on="code", how="left")
    for src, dst in (("n_dates", "n_comp_dates"),
                     ("n_holdings_latest", "n_holdings_latest")):
        if src in uni.columns:
            uni[dst] = uni[src].fillna(0).astype("int64")
            if dst != src:
                del uni[src]
        else:
            uni[dst] = 0

    return uni.sort_values(["theme_id", "n_ohlcv_days"],
                           ascending=[True, False]).reset_index(drop=True)
