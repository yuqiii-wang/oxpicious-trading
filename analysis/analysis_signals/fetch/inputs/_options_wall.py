"""The options_wall family's input fetchers (analysis.analysis_signals.
fetch.inputs._options_wall; 2026-10-12, hit-and-bounce additions
2026-10-13).

The OI-wall family's value sources are the OPTIONS analytics tables
(analysis.options_spot_daily the spot axis the walls were computed
against — NOT the mov_ave_spread price convention, so the breach
excesses match the walls' own underlying_close basis; analysis.
options_walls the chain-dominant zone edges — the put side's OUTER
edge + CENTER (the hit-and-bounce buy's reference level) and the
call side's OUTER cap; analysis.options_vol_index the VRP gate's
implied leg). The options universe is dictated by data availability
(000300/000016/000852 + the ETF option underlyings) — the family
intersects it with the sec_type's active universe.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

from ._shared import _OW_TARGET_TYPE

_OW_INPUT_COLUMNS = [
    "code", "date", "price", "put_wall_low", "put_wall_center",
    "call_wall_high", "vol30",
]


async def fetch_options_wall_inputs(
    conn,
    codes: list[str],
) -> pd.DataFrame:
    """The options_wall family's joined long-format input frame: one row
    per (code, date) of the options spot axis (analysis.options_spot_
    daily) with the CHAIN-DOMINANT zone wall edge per side (analysis.
    options_walls — dominant per (code, date, side) by strength_score
    then wall_oi, the analysis/options bands convention) and the 30d
    implied vol index (analysis.options_vol_index — the VRP gate's
    implied leg; NULL before the index's own history).

    Full history, no date bounds (the VRP trailing percentile reads
    each code's whole series; forward changes need post-snapshot
    prices). The frame is tiny (<= a few thousand rows)."""
    if not codes:
        return pd.DataFrame(columns=_OW_INPUT_COLUMNS)
    # sequential awaits — asyncpg connections are single-operation.
    # NOTE: each SELECT aliases underlying_code AS code so the rec_cols
    # dict keys match the frame columns list (a missing key silently
    # NaNs the column — and NaN join keys cross-join under cudf).
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
    wall_rows = await conn.fetch(
        """
        SELECT DISTINCT ON (underlying_code, date, option_type)
               underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               option_type,
               wall_low::float8 AS wall_low,
               wall_high::float8 AS wall_high,
               wall_center::float8 AS wall_center
        FROM analysis.options_walls
        WHERE wall_type = 'zone'
          AND underlying_code = ANY($1::text[])
        ORDER BY underlying_code, date, option_type,
                 strength_score DESC NULLS LAST,
                 wall_oi DESC NULLS LAST
        """,
        sorted(codes),
    )
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
    if not spot_rows:
        return pd.DataFrame(columns=_OW_INPUT_COLUMNS)
    df = pd.DataFrame(rec_cols(spot_rows), columns=[
        "code", "date", "price",
    ])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    if wall_rows:
        w = pd.DataFrame(rec_cols(wall_rows), columns=[
            "code", "date", "option_type", "wall_low", "wall_high",
            "wall_center",
        ])
        w["date"] = epoch_col_to_dt64(w["date"], index=w.index)
        # pivot the side rows into edge columns via where-masks on the
        # int codes (option_type 0=CALL / 1=PUT) — no string columns;
        # the put side carries its zone CENTER beside the outer edge
        # (the hit-and-bounce buy's reference level)
        call = w.loc[
            w["option_type"] == 0, ["code", "date", "wall_high"]
        ]
        put = w.loc[
            w["option_type"] == 1, ["code", "date", "wall_low", "wall_center"]
        ]
        df = df.merge(call, on=["code", "date"], how="left") \
               .rename(columns={"wall_high": "call_wall_high"})
        df = df.merge(put, on=["code", "date"], how="left") \
               .rename(columns={
                   "wall_low": "put_wall_low",
                   "wall_center": "put_wall_center",
               })
    else:
        df["call_wall_high"] = np.nan
        df["put_wall_low"] = np.nan
        df["put_wall_center"] = np.nan
    if vol_rows:
        v = pd.DataFrame(rec_cols(vol_rows), columns=[
            "code", "date", "vol30",
        ])
        v["date"] = epoch_col_to_dt64(v["date"], index=v.index)
        df = df.merge(v, on=["code", "date"], how="left")
    else:
        df["vol30"] = np.nan
    return df[_OW_INPUT_COLUMNS]


async def fetch_latest_options_wall_date(
    conn, sec_type: str,
) -> date | None:
    """The sec_type's latest options spot date (max(date) on
    analysis.options_spot_daily over the sec_type's option underlyings)
    — the options_wall family's ROLLING LATEST snapshot key."""
    target = _OW_TARGET_TYPE.get(sec_type)
    if target is None:
        return None
    return await conn.fetchval(
        """
        SELECT max(d.date)
        FROM analysis.options_spot_daily d
        JOIN (
            SELECT DISTINCT underlying_code
            FROM stats.options_terms
            WHERE underlying_target_type = $1
        ) u ON u.underlying_code = d.underlying_code
        """,
        target,
    )
