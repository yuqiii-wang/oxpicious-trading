"""The options_vix family's input fetchers (analysis.analysis_signals.
fetch.inputs._options_vix; 2026-10-13).

The vol-index fear-gauge family's value sources: the 30d model-free
vol index (analysis.options_vol_index — the family's VALUE SPACE and
its limiting input) inner-joined with the options spot axis
(analysis.options_spot_daily — the corr's spot leg and the forward
changes' price base; the join is over the vol index's calendar — a
spot session without a vol row cannot feed any axis) and the PUBLISHED
corr_20d samples (analysis.options_vol_index_vs_spot_price_corr — the
stride-grid fear-gauge series the engine linearly interpolates to
daily in-frame; never interpolated in the DB).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

from ._shared import _OW_TARGET_TYPE

_OVIX_INPUT_COLUMNS = ["code", "date", "price", "vol30", "corr_20d_pub"]


async def fetch_options_vix_inputs(
    conn,
    codes: list[str],
) -> pd.DataFrame:
    """The options_vix family's joined long-format input frame: one row
    per (code, date) of the vol index with the options spot close and
    the PUBLISHED corr_20d sample (NULLs where either source has no row
    that day — the published corr rides a 5-session stride grid through
    history + the daily tail, so non-sample sessions stay NULL here and
    the engine interpolates; a missing spot session NaNs its own change
    pair in the published series exactly as computed).

    Full history, no date bounds (the corr/z axes read each code's
    whole series; forward changes need post-snapshot prices). The
    frame is tiny (<= a few thousand rows)."""
    if not codes:
        return pd.DataFrame(columns=_OVIX_INPUT_COLUMNS)
    # sequential awaits — asyncpg connections are single-operation.
    # NOTE: each SELECT aliases underlying_code AS code so the rec_cols
    # dict keys match the frame columns list (a missing key silently
    # NaNs the column — and NaN join keys cross-join under cudf).
    vol_rows = await conn.fetch(
        """
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               vol_index_30d::float8 AS vol30
        FROM analysis.options_vol_index
        WHERE underlying_code = ANY($1::text[])
        ORDER BY underlying_code, date ASC
        """,
        sorted(codes),
    )
    if not vol_rows:
        return pd.DataFrame(columns=_OVIX_INPUT_COLUMNS)
    df = pd.DataFrame(rec_cols(vol_rows), columns=[
        "code", "date", "vol30",
    ])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    spot_rows = await conn.fetch(
        """
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS price
        FROM analysis.options_spot_daily
        WHERE underlying_code = ANY($1::text[])
        ORDER BY underlying_code, date ASC
        """,
        sorted(codes),
    )
    if spot_rows:
        s = pd.DataFrame(rec_cols(spot_rows), columns=[
            "code", "date", "price",
        ])
        s["date"] = epoch_col_to_dt64(s["date"], index=s.index)
        df = df.merge(s, on=["code", "date"], how="left")
    else:
        df["price"] = np.nan
    corr_rows = await conn.fetch(
        """
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               corr_20d::float8 AS corr_20d_pub
        FROM analysis.options_vol_index_vs_spot_price_corr
        WHERE underlying_code = ANY($1::text[])
        ORDER BY underlying_code, date ASC
        """,
        sorted(codes),
    )
    if corr_rows:
        c = pd.DataFrame(rec_cols(corr_rows), columns=[
            "code", "date", "corr_20d_pub",
        ])
        c["date"] = epoch_col_to_dt64(c["date"], index=c.index)
        df = df.merge(c, on=["code", "date"], how="left")
    else:
        df["corr_20d_pub"] = np.nan
    return df[_OVIX_INPUT_COLUMNS]


async def fetch_latest_options_vix_date(
    conn, sec_type: str,
) -> date | None:
    """The sec_type's latest vol-index date (max(date) on
    analysis.options_vol_index over the sec_type's option underlyings)
    — the options_vix family's ROLLING LATEST snapshot key (the vol
    index is the limiting input of the corr axes)."""
    target = _OW_TARGET_TYPE.get(sec_type)
    if target is None:
        return None
    return await conn.fetchval(
        """
        SELECT max(v.date)
        FROM analysis.options_vol_index v
        JOIN (
            SELECT DISTINCT underlying_code
            FROM stats.options_terms
            WHERE underlying_target_type = $1
        ) u ON u.underlying_code = v.underlying_code
        """,
        target,
    )
