"""Row-wise capital-per-movement ratio helpers shared by the builds.* pipelines.

Row-wise arithmetic only (no groupby / rolling state), so a single
vectorized pass serves stock / ETF / index / industry frames alike and
runs identically on pandas and cudf.pandas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# NUMERIC(18,6) storage bound: 12 integer digits -> |value| must stay
# below 1e12 or asyncpg rejects the row. Values at/beyond the bound are
# nulled (same overflow-guard convention as the mov_ave_spread ratios).
TRADING_AMT_PER_MOVE_MAX_ABS = 1e12


def compute_trading_amt_per_move(df: pd.DataFrame, *,
                                 amount_col: str = "trading_amount",
                                 open_col: str = "open",
                                 close_col: str = "close",
                                 out_col: str = "trading_amt_per_pct_change") -> None:
    """Add ``out_col`` = trading_amount / (close - open), IN PLACE.

    Capital traded per unit of the day's open→close move — signed:
    positive on up days, negative on down days (the sign carries the
    direction). Reciprocal of the Amihud (2002) illiquidity spirit on
    the intraday net-move leg: HIGH = much capital absorbed per unit of
    move (deep book), LOW = little capital moved the price a lot.

    Conventions (shared with analyze.mov_ave_spread ratio columns):
      - Zero move (close == open — flat / 一字板 limit-locked): the 0
        denominator is auto-set to 1.0, so the stored value equals the
        raw trading amount — a pragmatic floor, NOT a true ratio.
      - NULL when any input is NULL.
      - NULL when the result is non-finite or |result| >= 1e12
        (NUMERIC(18,6) bound — sub-tick moves with huge turnover).

    ``pd.to_numeric`` coercion keeps the helper usable on object-dtype
    numerators (e.g. an all-None column created on an empty merge).
    """
    amount = pd.to_numeric(df[amount_col], errors="coerce")
    open_ = pd.to_numeric(df[open_col], errors="coerce")
    close = pd.to_numeric(df[close_col], errors="coerce")
    move = close - open_
    den = move.where(move != 0, 1.0)
    result = amount / den
    bad = amount.isna() | open_.isna() | close.isna() \
        | ~np.isfinite(result) | (result.abs() >= TRADING_AMT_PER_MOVE_MAX_ABS)
    df[out_col] = result.where(~bad)
