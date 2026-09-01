"""Corporate-action (split/dividend) adjustment for ETF OHLCV data.

Detects split and dividend events by comparing raw close-to-close returns
against the source-published pct_change, then computes cumulative split
factors and adjusted OHLC columns.
"""
import numpy as np
import pandas as pd

from _common.df_utils import safe_columns


def apply_split_adjustment(
    df: pd.DataFrame,
    verbose: bool = True,
    adj_seeds: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Apply split/dividend adjustment to ETF OHLCV data.

    Detects corp-action events by comparing raw close-to-close returns
    against the SZSE/SSE-published pct_change. Dividend-like events
    (small factor deviation) produce implied_dividend_per_share; larger
    deviations are split/conversion events.

    Adds columns: cum_split_factor, is_split_event_day, action_type,
    implied_dividend_per_share, cum_dividend_per_share,
    adj_prev_close, adj_open, adj_high, adj_low, adj_close.

    *adj_seeds* (columns: code, cum_factor, cum_dividend) restores
    corp-action continuity when the frame was window-truncated at the DB
    fetch (B3): cum factors/dividends are forward products, and the seed
    carries the stored state of each code's last pre-window row, so
    scaling the recomputed products by it reproduces full-history values
    exactly. Codes absent from the seed frame are treated as unseeded
    (factor 1.0 / dividend 0.0).
    """
    if df is None or len(df) == 0:
        return df

    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    n_rows = len(df)
    cum_factors = np.ones(n_rows, dtype=float)
    daily_factors = np.ones(n_rows, dtype=float)
    close_prevs = np.zeros(n_rows, dtype=float)
    is_split = np.zeros(n_rows, dtype=bool)

    codes = np.asarray(df["code"])
    closes = np.asarray(df["close"], dtype="float64")
    pcts = np.asarray(df["pct_change"], dtype="float64")

    n_splits_detected = 0
    start_idx = 0
    cur_code = codes[0]
    for i in range(1, n_rows + 1):
        if i == n_rows or codes[i] != cur_code:
            sub_slice = slice(start_idx, i)
            sub_n = i - start_idx
            if sub_n > 1:
                sub_close = closes[sub_slice]
                sub_pct = pcts[sub_slice]
                raw_ret = np.concatenate([[0.0], np.diff(sub_close) / np.where(sub_close[:-1] == 0, np.nan, sub_close[:-1])])
                szse_ret = sub_pct / 100.0
                raw_ret = np.nan_to_num(raw_ret, nan=0.0, posinf=0.0, neginf=0.0)
                szse_ret = np.nan_to_num(szse_ret, nan=0.0, posinf=0.0, neginf=0.0)
                d_factor = np.where(
                    np.abs(raw_ret - szse_ret) > 0.002,
                    (1.0 + szse_ret) / (1.0 + raw_ret),
                    1.0,
                )
                d_factor[0] = 1.0
                sub_factor = np.cumprod(d_factor)
                cum_factors[sub_slice] = sub_factor
                daily_factors[sub_slice] = d_factor
                close_prevs[sub_slice.start + 1 : sub_slice.stop] = sub_close[:-1]
                diff_mask = np.abs(np.log(np.maximum(d_factor, 1e-12))) > 1e-3
                is_split[sub_slice] = diff_mask
                n_splits_detected += int(diff_mask.sum())
            else:
                cum_factors[sub_slice] = 1.0
                daily_factors[sub_slice] = 1.0
                is_split[sub_slice] = False
            if i < n_rows:
                start_idx = i
                cur_code = codes[i]

    df["cum_split_factor"] = cum_factors
    df["is_split_event_day"] = is_split.astype(int)

    DIV_FACTOR_TOL = 0.15
    evt_mask = is_split.astype(bool)
    abs_dev = np.abs(daily_factors - 1.0)
    is_div_like = evt_mask & (abs_dev < DIV_FACTOR_TOL)
    is_split_like = evt_mask & ~is_div_like

    prev_close_arr = np.asarray(df["prev_close"], dtype="float64")
    D_from_szse = np.where(
        evt_mask & (close_prevs > 0),
        close_prevs - prev_close_arr,
        0.0,
    )
    df["implied_dividend_per_share"] = np.where(is_div_like, np.round(D_from_szse, 6), 0.0)

    df["cum_dividend_per_share"] = df.groupby("code", sort=False)["implied_dividend_per_share"].cumsum().round(6)

    # B3 seed: continue each code's forward products from its pre-window
    # stored state (recomputed window products start at 1.0 / 0.0; the
    # stored seed row state completes them — see docstring). The merge
    # (how="left") preserves row count and order, so the `codes` array
    # stays aligned.
    if adj_seeds is not None and len(adj_seeds):
        df = df.merge(
            adj_seeds.rename(columns={
                "cum_factor": "_seed_factor",
                "cum_dividend": "_seed_dividend"}),
            on="code", how="left",
        )
        df["cum_split_factor"] = df["cum_split_factor"] * df["_seed_factor"].fillna(1.0)
        df["cum_dividend_per_share"] = (
            df["cum_dividend_per_share"] + df["_seed_dividend"].fillna(0.0)
        ).round(6)
        df = df.drop(columns=["_seed_factor", "_seed_dividend"])
        if verbose:
            print(f"    [CORP-ADJ] seeded {len(adj_seeds):,} codes with their "
                  f"pre-window cum factor/dividend state", flush=True)

    act_type = np.full(n_rows, "", dtype=object)
    act_type[is_div_like] = "dividend"
    act_type[is_split_like] = "split_or_conv"
    df["action_type"] = act_type

    cf = np.asarray(df["cum_split_factor"], dtype="float64")
    df["adj_prev_close"] = np.asarray(df["prev_close"], dtype="float64") * cf
    df["adj_open"] = np.asarray(df["open"], dtype="float64") * cf
    df["adj_high"] = np.asarray(df["high"], dtype="float64") * cf
    df["adj_low"] = np.asarray(df["low"], dtype="float64") * cf
    df["adj_close"] = np.asarray(df["close"], dtype="float64") * cf

    valid = np.asarray(df["close"], dtype="float64") > 1e-9
    szse_prevclose_equiv = np.where(
        valid,
        np.asarray(df["adj_close"], dtype="float64")
        / (1.0 + np.asarray(df["pct_change"], dtype="float64") / 100.0),
        np.asarray(df["adj_prev_close"], dtype="float64"),
    )
    use_equiv = np.zeros(n_rows, dtype=bool)
    cur_code = codes[0]
    start_idx = 0
    for i in range(1, n_rows + 1):
        if i == n_rows or codes[i] != cur_code:
            if i - start_idx > 1:
                use_equiv[start_idx + 1 : i] = True
            if i < n_rows:
                start_idx = i
                cur_code = codes[i]
    # Column-first whole-array replacement (no boolean .loc addressing;
    # same overwrite semantics as the old df.loc[mask, col] = vals[mask])
    _take_equiv = use_equiv & valid
    df["adj_prev_close"] = np.where(
        _take_equiv, szse_prevclose_equiv, df["adj_prev_close"])

    for col in ["adj_prev_close", "adj_open", "adj_high", "adj_low", "adj_close"]:
        df[col] = df[col].round(6)

    col_order = safe_columns(df)
    block_tail = [
        "cum_split_factor", "is_split_event_day",
        "action_type", "implied_dividend_per_share", "cum_dividend_per_share",
        "adj_prev_close", "adj_open", "adj_high", "adj_low", "adj_close",
    ]
    for col in block_tail:
        if col in col_order:
            col_order.remove(col)
    anchor = "pct_change"
    if anchor in col_order:
        pos = col_order.index(anchor) + 1
    else:
        pos = len(col_order)
    col_order[pos:pos] = block_tail
    df = df[col_order]

    if verbose:
        n_etfs_affected = int(df["code"][df["is_split_event_day"] == 1].nunique())
        n_div = int(is_div_like.sum())
        n_split = int(is_split_like.sum())
        print(f"    [CORP-ADJ] detected {n_splits_detected} corp-action days "
              f"({n_div} dividend-like, {n_split} split/conv) across {n_etfs_affected} ETFs; "
              f"added adj_* OHLC + dividend columns", flush=True)

    return df
