"""Fetch layer for analysis.oracle_signals — the per-sec_type source
frame (daily closes) read through the SQL-view template under
database/sql_views/analysis/oracle_signals/. Class-first: one fetcher
class owning the sec_type + code scope; the sec_type's own bars table
rides as a fragment arg (the pipeline config's SEC_TYPE_SOURCES
mapping). Dates carry the epoch-float8 DB-read convention converted at
the frame boundary via epoch_col_to_dt64 (datetime64[us], cudf-clean)."""
from __future__ import annotations

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64
from _common.sql_views import frag, render_sql_view
from analysis.oracle_signals import config


class OracleSources:
    """The pipeline's source reader for one sec_type + code scope."""

    def __init__(self, conn, sec_type: str, codes: list[str]) -> None:
        self.conn = conn
        self.sec_type = sec_type
        self.codes = codes
        self.spec = config.SEC_TYPE_SOURCES[sec_type]

    async def fetch_daily_closes(self) -> pd.DataFrame:
        """Daily closes — [code, date, close] (one row per (code, date)
        bar with a close in the sec_type's basic-stats table), sorted
        (code, date) — the engine's per-code chronological walk relies
        on this order."""
        view = render_sql_view(
            "analysis/oracle_signals/daily_closes",
            bars_table=frag(self.spec.bars_table),
            codes=self.codes,
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(columns=["code", "date", "close"])
        cols = rec_cols(rows)
        cols["date"] = epoch_col_to_dt64(cols.pop("date_epoch"))
        return pd.DataFrame(
            cols, columns=["code", "date", "close"]
        ).sort_values(["code", "date"]).reset_index(drop=True)

    async def resolve_universe(self) -> list[str]:
        """The sec_type's run universe: the config's fixed list when one
        is set (index = STUDY_INDEX_CODES), else data-driven — every
        code with close data in the sec_type's own basic-stats table
        (the etf / stock path)."""
        fixed = config.universe_for(self.sec_type)
        if fixed is not None:
            return list(fixed)
        view = render_sql_view(
            "analysis/oracle_signals/universe_codes",
            bars_table=frag(self.spec.bars_table),
        )
        rows = await self.conn.fetch(view.sql)
        return [r["code"] for r in rows if r["code"] is not None]
