"""Step 4 — merge OHLCV+margin, corp-action adjustment + moving averages."""
from __future__ import annotations

import pandas as pd

from _common.df_utils import compute_moving_averages, compute_emas, safe_columns

from builds.etf.split_adjustment import apply_split_adjustment
from builds.etf.composition import build_composition

MARGIN_VALUE_COLS: list = [
    "rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
    "rq_balance_amt", "total_balance",
]


def prepare_features(ohlcv_df: pd.DataFrame, margin_df: pd.DataFrame) -> pd.DataFrame:
    """Merge OHLCV + margin and compute adjustment/MA columns.

    Needs FULL per-code history — the caller must pass the combined
    historical+new frame.
    """
    print("\n[4/7] Merging OHLCV + margin, applying corp-action adjustment + MAs …", flush=True)
    if len(margin_df):
        merged = ohlcv_df.merge(margin_df, on=["date", "code"], how="left", validate="m:1")
    else:
        merged = ohlcv_df.copy()
        for c in MARGIN_VALUE_COLS:
            merged[c] = 0.0
    for c in MARGIN_VALUE_COLS:
        if c in safe_columns(merged):
            merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0.0)

    _fill_sse_rq_balance_amt(merged)

    # Corp-action adjustment then grouped rolling MAs / EMAs
    # apply_split_adjustment() guarantees [code,date] sort + contiguous
    # index on return (sort+reset inside) — no re-sort needed here.
    merged = apply_split_adjustment(merged, verbose=True)
    compute_moving_averages(
        merged, group_key="code", value_col="adj_close",
        windows=[5, 20, 60, 120, 255],
    )
    compute_emas(
        merged, group_key="code", value_col="adj_close",
        spans=[6, 10, 20, 60, 120, 255],
    )
    print(f"    → MA columns added: ma5, ma5_ratio, ma20, ma60, ma120, ma255; "
          f"EMA columns added: ema6, ema10, ema20, ema60, ema120, ema255", flush=True)
    return merged


def _fill_sse_rq_balance_amt(merged: pd.DataFrame) -> None:
    """Compute rq_balance_amt in place for SSE ETF rows (quantity × mid price).

    Whole-column ``where`` replacement — no boolean .loc row addressing.
    """
    cols = safe_columns(merged)
    if not ("rq_balance_qty" in cols and "rq_balance_amt" in cols):
        return
    missing_rq_amt = (
        merged["exchange"].eq("SS")
        & (merged["rq_balance_amt"] == 0)
        & (merged["rq_balance_qty"] > 0)
    )
    if missing_rq_amt.any():
        mid_price = (merged["open"] + merged["close"]) / 2.0
        implied = merged["rq_balance_qty"] * mid_price
        merged["rq_balance_amt"] = merged["rq_balance_amt"].where(
            ~missing_rq_amt, implied)
        print(f"    → Filled rq_balance_amt for {int(missing_rq_amt.sum()):,} SSE ETF rows", flush=True)


def load_composition(code_filter: str | None) -> tuple:
    """Build composition frames, restricted to the target code when set.

    Composition CSVs carry whole suffixed ``etf_code`` — the data filter
    compares codes directly.  Filenames end with the BARE code
    (``szse_etf_comp_YYYYMMDD_<code>.csv``); only that file-glob needs the
    suffix stripped (filename convention, not data).

    Returns: (comp_long, comp_universe).
    """
    print("\n    Building composition …", flush=True)
    comp_long, comp_universe = build_composition(verbose=True, code=code_filter)

    if code_filter and comp_long is not None and len(comp_long) > 0:
        comp_long = comp_long[
            comp_long["etf_code"].astype(str).str.strip().eq(code_filter)
        ].reset_index(drop=True)
        print(f"    [CODE FILTER] Composition rows restricted to {code_filter}: {len(comp_long):,}", flush=True)
    return comp_long, comp_universe
