"""The analysis.ohlcv_ext build (analysis.analysis_signals.
signal_amplification.ohlcv_ext).

Fetches the raw OHLCV source (stats.{index,etf,stock}_basic_stats —
ETFs adjusted, estimated-OHLC rows excluded at the SQL), computes the
extended tech stats (anatomy.OhlcvAnatomy), and writes
analysis.ohlcv_ext incrementally:

  incremental (default): the PER-CODE horizon check (the run.py
  _write_trend_days model) — a code whose stored max(date) lags the
  source horizon is purged (scoped by code) and rewritten whole; fresh
  codes are skipped. A universe growth writes the newcomer even when
  the veterans are fresh (the check is per-code, never scope-wide).
  --force: every code is stale.

The frame is always fetched + computed for the WHOLE requested
universe (the amplifier engine consumes it in the same run; the
trailing windows and the prev-row chain need full history anyway) —
only the WRITE is gated on staleness. The writer is the sanctioned
host boundary (host_array -> python records for the key-chunked COPY,
batched_copy_by_key_async — every commit stays under the 100k-row
replication-slot target).
"""
from __future__ import annotations

import datetime as _dt
import logging

import numpy as np
import pandas as pd

from _common.db_commons import (
    batched_copy_by_key_async,
    chunked_purge_async,
)
from _common.df_utils import epoch_col_to_dt64, host_array
from _common.labels import SEC_TYPE_CODE
from _common.sql_views import frag, render_sql_view

from analysis._common import upsert_analysis_identity
from analysis.analysis_signals.signal_amplification.anatomy import (
    OHLV_EXT_COLUMNS,
    OhlcvAnatomy,
)

logger = logging.getLogger(__name__)

__all__ = ["OHLCV_EXT_TABLE", "OhlcvExtBuilder"]

OHLCV_EXT_TABLE = "analysis.ohlcv_ext"

ANALYSIS_NAME = "signal_amplification"
ANALYSIS_DETAIL = "ohlcv_ext"
ANALYSIS_DESCRIPTION = (
    "Signal amplification — the day-anatomy amplifier tier (the "
    "amplifiers strengthen/discount the EXISTING signal families' "
    "claims, never create signals): builds analysis.ohlcv_ext (the "
    "per-day extended OHLCV tech stats: intraday + prev-day gaps, "
    "wicks, close position, trading-amount ratios) self-contained "
    "from stats.*_basic_stats, attaches the margin anatomy (融资余额 "
    "quantile bars) + the news anatomy (the daily news-text MARKET "
    "state — the migrated news_wash rules), detects the amplifier "
    "days (the ohlcv / margin / news sub types), measures their "
    "evidence against the bound signal families' own history, and "
    "writes analysis_signals.signal_amplification_rules (the binding "
    "config) + signal_amplification_history (the event record)."
)

# the per-sec_type source fragments (the mov_ave_spread
# config.price_sources conventions: ETF uses the adjusted series —
# COALESCE(adj_*, raw) — and etf/stock amounts live on the
# liquidity-margin stores; index amounts ride the base table)
_SOURCE_FRAGMENTS: dict[str, dict[str, str]] = {
    "index": {
        "base_from": "stats.index_basic_stats b",
        "open_expr": "b.open",
        "high_expr": "b.high",
        "low_expr": "b.low",
        "close_expr": "b.close",
        "amt_select": "b.trading_amount",
        "amt_joins": "",
    },
    "etf": {
        "base_from": (
            "stats.etf_basic_stats b "
            "LEFT JOIN stats.etf_adjustment a "
            "ON a.code = b.code AND a.date = b.date"
        ),
        "open_expr": "COALESCE(a.adj_open, b.open)",
        "high_expr": "COALESCE(a.adj_high, b.high)",
        "low_expr": "COALESCE(a.adj_low, b.low)",
        "close_expr": "COALESCE(a.adj_close, b.close)",
        "amt_select": "m.trading_amount",
        "amt_joins": (
            "LEFT JOIN stats.etf_liquidity_margin m "
            "ON m.code = b.code AND m.date = b.date"
        ),
    },
    "stock": {
        "base_from": "stats.stock_basic_stats b",
        "open_expr": "b.open",
        "high_expr": "b.high",
        "low_expr": "b.low",
        "close_expr": "b.close",
        "amt_select": "m.trading_amount",
        "amt_joins": (
            "LEFT JOIN stats.stock_liquidity_margin m "
            "ON m.code = b.code AND m.date = b.date"
        ),
    },
}

# the write projection's numeric columns (everything but the keys)
_NUMERIC_COLUMNS = [c for c in OHLV_EXT_COLUMNS
                    if c not in ("sec_type", "code", "date")]


class OhlcvExtBuilder:
    """Fetch + compute the ohlcv_ext anatomy for one (sec_type, codes)
    scope; write the STALE codes' rows. ``build`` returns the computed
    anatomy frame for the whole requested universe."""

    def __init__(self, conn, sec_type: str, codes: list[str],
                 force: bool = False) -> None:
        self.conn = conn
        self.sec_type = sec_type
        self.codes = sorted(codes)
        self.force = force
        self.sec_type_code = int(SEC_TYPE_CODE[sec_type])
        self._anatomy = OhlcvAnatomy(self.sec_type_code)

    # -- fetch ---------------------------------------------------------
    async def _fetch_source(self) -> pd.DataFrame:
        """The raw source frame for the whole scope (full history — the
        prev-row chain and the trailing windows need it)."""
        f = _SOURCE_FRAGMENTS[self.sec_type]
        view = render_sql_view(
            "analysis/analysis_signals/signal_amplification/ohlcv_source",
            codes=self.codes,
            since=_EPOCH_START,
            base_from=frag(f["base_from"]),
            open_expr=frag(f["open_expr"]),
            high_expr=frag(f["high_expr"]),
            low_expr=frag(f["low_expr"]),
            close_expr=frag(f["close_expr"]),
            amt_select=frag(f["amt_select"]),
            amt_joins=frag(f["amt_joins"]),
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(
                columns=["code", "date", "open", "high", "low", "close",
                         "trading_amount"],
            )
        df = pd.DataFrame([dict(r) for r in rows])
        # the DB-read convention: epoch float8 -> datetime64[us] (the
        # ::float8 casts avoid Decimal); sec_type as the int16 frame code
        df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
        df["sec_type"] = np.int16(self.sec_type_code)
        return df

    # -- staleness -----------------------------------------------------
    async def _stale_codes(self, horizon) -> list[str]:
        """Codes whose stored max(date) lags the source horizon (all
        codes on --force). The PER-CODE check: a universe growth writes
        the newcomer even when the veterans are fresh."""
        if self.force:
            return list(self.codes)
        stored = await self.conn.fetch(
            f"SELECT code, max(date) AS latest FROM {OHLCV_EXT_TABLE} "
            f"WHERE sec_type = $1 AND code = ANY($2) GROUP BY code",
            self.sec_type_code, self.codes,
        )
        store_latest = {r["code"]: r["latest"] for r in stored}
        return [c for c in self.codes
                if store_latest.get(c) is None or store_latest[c] < horizon]

    # -- build ---------------------------------------------------------
    async def build(self) -> pd.DataFrame:
        """Build the anatomy frame for the whole scope; write the stale
        codes' rows; return the frame."""
        src = await self._fetch_source()
        if src.empty:
            logger.warning("      ohlcv_ext: empty source frame — "
                           "nothing to do")
            return src
        anatomy = self._anatomy.compute(src)

        # the source horizon = the frame's max date (the whole-universe
        # compute already ran; the write gate is per-code against it)
        horizon = host_array(anatomy["date"]).max()
        horizon_date = horizon.astype("datetime64[D]").item()
        stale = await self._stale_codes(horizon_date)
        if stale:
            logger.info(f"      ohlcv_ext: writing {len(stale):,}/"
                        f"{len(self.codes):,} codes "
                        f"({'force rebuild' if self.force else 'stale'})")
            await chunked_purge_async(
                self.conn, OHLCV_EXT_TABLE,
                where_sql="sec_type = $1 AND code = ANY($2)",
                params=(self.sec_type_code, stale), key_column="code",
            )
            rows = self._materialize(anatomy, stale)
            await batched_copy_by_key_async(
                self.conn, OHLCV_EXT_TABLE, rows, key="code",
                label="ohlcv_ext",
            )
            await upsert_analysis_identity(
                self.conn, ANALYSIS_NAME, ANALYSIS_DETAIL,
                ANALYSIS_DESCRIPTION,
            )
            logger.info(f"      ohlcv_ext: wrote {len(rows):,} rows")
        else:
            logger.info(f"      ohlcv_ext: all {len(self.codes):,} codes "
                        f"at the source horizon — write skipped")
        return anatomy

    def _materialize(self, anatomy: pd.DataFrame,
                     codes: list[str]) -> list[dict]:
        """The writer's host boundary: the stale codes' rows as python
        records (numeric NaN -> None rounded to 6dp, dates -> python
        date) for the key-chunked COPY."""
        from _common.df_utils import to_py_dates
        out = anatomy[anatomy["code"].isin(codes)][OHLV_EXT_COLUMNS].copy()
        out = to_py_dates(out, ["date"])
        code_arr = host_array(out["code"].astype(object))
        val_arrs = {
            c: host_array(out[c].to_numpy(dtype="float64", na_value=np.nan))
            for c in _NUMERIC_COLUMNS
        }
        date_arr = host_array(out["date"])
        st = self.sec_type_code
        recs: list[dict] = []
        for i in range(len(out)):
            row: dict = {"sec_type": st, "code": str(code_arr[i]),
                         "date": date_arr[i]}
            for c in _NUMERIC_COLUMNS:
                v = float(val_arrs[c][i])
                row[c] = None if np.isnan(v) else round(v, 6)
            recs.append(row)
        return recs


# the source history lower bound (a safety net — each code carries its
# own start; index history begins 2020-01-01 per the ohlcv skill).
# Plain python date — a pd.Timestamp.date() call here would trip two
# cudf fallback lines at import time under cudf.pandas.
_EPOCH_START = _dt.date(2000, 1, 1)
