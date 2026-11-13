"""study_kit.studies.volume_contrast — the up/down-day 量能 contrast."""
from __future__ import annotations

import numpy as np
import pandas as pd


def volume_contrast(
    close: pd.Series,
    amount: pd.Series,
    window: int = 20,
) -> pd.DataFrame:
    """The up/down-day trading-amount contrast (量能对比) over a rolling
    ``window`` — one row per date with {up_amt, dn_amt, ratio}:
    ``up_amt`` / ``dn_amt`` are the mean trading_amount of the UP days
    vs the DOWN days inside the window, ``ratio`` = up/dn — > 1 is the
    healthy 涨放量跌缩量承接 (rallies carry real money, dips are
    quiet), < 1 the 派发 pattern (rallies on thin volume, selloffs
    heavy).

    ``amount`` is the trading AMOUNT (成交额) — the house volume
    convention. The amt MA levels and their slopes are MATERIALIZED on
    analysis.mov_ave_trading_amt (fetch via study_kit.db.query or
    study_kit.db.fetch_amt_metrics) — this function adds only the
    per-side contrast the table does not store.
    """
    side = np.sign(close.diff()).replace(0.0, np.nan)
    up = amount.where(side > 0)
    dn = amount.where(side < 0)
    out = pd.DataFrame({
        "up_amt": up.rolling(window, min_periods=1).mean(),
        "dn_amt": dn.rolling(window, min_periods=1).mean(),
    }, index=close.index)
    out["ratio"] = out["up_amt"] / out["dn_amt"]
    return out
