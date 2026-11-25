"""analysis.signal_rules_audit — steps (per-family census + pairwise overlap).

Both audit grains compute from ONE shared day-observation frame
(sql view ``analysis/signal_rules_audit/steps/signal_events`` — one row
per (code, date, signal_type), the standalone trading tier collapsed,
with per-side has_buy / has_sell flags):

  - ``TypeSummaryStep``  → analysis_signals.signal_type_audit
  - ``OverlapAuditStep`` → analysis_signals.signal_overlap_audit

COMPUTE LAYOUT (the cudf-dtypes contract): the year slice and the
per-family aggregates run whole-frame (``groupby().agg`` — GPU). The
PAIR OVERLAPS are the ONE sanctioned host-numpy boundary per period
(documented here once): a day × family boolean presence grid built
from a single ``host_array`` unwrap — positional gathers / row×day 2D
grids are the sanctioned host core, and no cuDF-native formulation
exists for a pairwise boolean grid over the composite (code, date) key
built by sorted-rank arithmetic (never ``np.searchsorted`` on proxy
positions). Every result frame materializes through the REAL
DataFrame ctor from plain host arrays and converts at the write
boundary via ``sanitize_for_db_insert`` (dates emit python dates
there, never object columns upstream).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64, host_array
from _common.db_commons import bulk_upsert_async
from _common.sql_views import render_sql_view
from _common.sql_writes import load_sql_write

from analysis._common import sanitize_for_db_insert
from analysis.signal_rules_audit.config import OVERLAP_AUDIT_TABLE, TYPE_AUDIT_TABLE

SECONDS_PER_DAY = 86_400.0

_EVENTS_VIEW = "analysis/signal_rules_audit/steps/signal_events"
_YEARS_VIEW = "analysis/signal_rules_audit/steps/signal_years"

_SUMMARY_COLUMNS = [
    "sec_type", "period", "period_start", "period_end", "signal_type",
    "n_events", "n_buy", "n_sell", "n_codes", "n_days",
    "signal_universe", "first_date", "last_date",
]
_OVERLAP_COLUMNS = [
    "sec_type", "period", "period_start", "period_end",
    "signal_type", "other_signal_type",
    "n_base", "n_other", "n_universe", "n_overlap",
    "n_same_action", "n_opposite_action",
    "overlap_pct", "expected_pct", "jaccard", "lift",
]

_SUMMARY_NUMERICS = [
    "sec_type", "period", "signal_type", "n_events", "n_buy", "n_sell",
    "n_codes", "n_days", "signal_universe",
]
_OVERLAP_NUMERICS = [
    "sec_type", "period", "signal_type", "other_signal_type",
    "n_base", "n_other", "n_universe", "n_overlap",
    "n_same_action", "n_opposite_action",
    "overlap_pct", "expected_pct", "jaccard", "lift",
]


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def fetch_frame(conn, view_name: str, **args) -> pd.DataFrame:
    """Render a sql_views template and land its rows as a DataFrame."""
    view = render_sql_view(view_name, **args)
    rows = await conn.fetch(view.sql, *view.params)
    return pd.DataFrame(rec_cols(rows))


async def fetch_signal_events(conn, sec_type: int) -> pd.DataFrame:
    """The standalone trading day-observation frame + int64 `year`.

    One row per (code, date, signal_type) — the SQL GROUP BY already
    collapsed sub types and the portfolio tier; `year` buckets the
    periods (cuDF-native ``.dt.year`` on the epoch-converted date).
    """
    events = await fetch_frame(conn, _EVENTS_VIEW, sec_type=sec_type)
    if events.empty:
        return events
    events["date"] = epoch_col_to_dt64(events["date_epoch"], index=events.index)
    events["year"] = events["date"].dt.year.astype("int64")
    return events


async def fetch_signal_years(conn, sec_type: int) -> set[int]:
    """Calendar years carrying standalone trading events at the scope —
    the empty-year exclusion for incremental targeting (a year with no
    events never materializes rows, so it must not read as missing
    forever)."""
    view = render_sql_view(_YEARS_VIEW, sec_type=sec_type)
    rows = await conn.fetch(view.sql, *view.params)
    return {int(r["year"]) for r in rows}


def _period_date_columns(n: int, start, end) -> dict[str, np.ndarray]:
    """Host datetime64[us] buffers for the period bounds (never a
    scalar-pd.NaT / python-date column init under cudf)."""
    start64 = np.datetime64(start, "D").astype("datetime64[us]")
    end64 = np.datetime64(end, "D").astype("datetime64[us]")
    return {
        "period_start": np.full(n, start64, "datetime64[us]"),
        "period_end": np.full(n, end64, "datetime64[us]"),
    }


def _epoch_to_date_column(epoch_seconds: np.ndarray) -> np.ndarray:
    """Host epoch-seconds array -> datetime64[us] (epoch math on RAW
    host numpy: seconds → [s] → upcast to the target unit)."""
    days = np.floor(epoch_seconds / SECONDS_PER_DAY).astype("int64")
    return (days * SECONDS_PER_DAY).astype("datetime64[s]").astype("datetime64[us]")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

class TypeSummaryStep:
    """Per-family census → analysis_signals.signal_type_audit."""

    title = "analysis_signals.signal_type_audit"
    table = TYPE_AUDIT_TABLE
    key_columns = ["sec_type", "period", "signal_type"]

    def compute(self, events: pd.DataFrame, sec_type: int, period: int,
                period_start, period_end) -> pd.DataFrame:
        """One period's per-family aggregates (whole-frame, GPU)."""
        year_slice = events[events["year"] == period]
        if year_slice.empty:
            return pd.DataFrame()
        universe = year_slice[["code", "date_epoch"]].drop_duplicates()
        n_universe = int(len(universe))
        agg = (
            year_slice.groupby("signal_type", sort=True)
            .agg(
                n_events=("date_epoch", "size"),
                n_buy=("has_buy", "sum"),
                n_sell=("has_sell", "sum"),
                n_codes=("code", "nunique"),
                n_days=("date_epoch", "nunique"),
                first_epoch=("date_epoch", "min"),
                last_epoch=("date_epoch", "max"),
            )
            .reset_index()
        )
        # Host materialization (the write boundary): one unwrap per
        # column, the REAL DataFrame ctor, dates as datetime64[us].
        n = len(agg)
        columns = {
            "sec_type": np.full(n, sec_type, "int64"),
            "period": np.full(n, period, "int64"),
            "signal_type": host_array(agg["signal_type"]).astype("int64"),
            "n_events": host_array(agg["n_events"]).astype("int64"),
            "n_buy": host_array(agg["n_buy"]).astype("int64"),
            "n_sell": host_array(agg["n_sell"]).astype("int64"),
            "n_codes": host_array(agg["n_codes"]).astype("int64"),
            "n_days": host_array(agg["n_days"]).astype("int64"),
            "signal_universe": np.full(n, n_universe, "int64"),
            "first_date": _epoch_to_date_column(
                host_array(agg["first_epoch"]).astype("float64")),
            "last_date": _epoch_to_date_column(
                host_array(agg["last_epoch"]).astype("float64")),
        }
        columns.update(_period_date_columns(n, period_start, period_end))
        real_pd = getattr(pd, "_fsproxy_slow", pd)
        return real_pd.DataFrame(columns)[_SUMMARY_COLUMNS]

    async def run(self, conn, events: pd.DataFrame, sec_type: int,
                  period: int, period_start, period_end) -> int:
        out = self.compute(events, sec_type, period, period_start, period_end)
        if out.empty:
            return 0
        rows = sanitize_for_db_insert(out, numeric_cols=_SUMMARY_NUMERICS,
                                      date_cols=["period_start", "period_end",
                                                 "first_date", "last_date"])
        await _purge_periods(conn, self.table, sec_type, [period])
        written = await bulk_upsert_async(conn, self.table, rows,
                                          key_columns=self.key_columns)
        print(f"[{self.title}] period {period}: {written} row(s)")
        return written


class OverlapAuditStep:
    """Pairwise family overlap → analysis_signals.signal_overlap_audit."""

    title = "analysis_signals.signal_overlap_audit"
    table = OVERLAP_AUDIT_TABLE
    key_columns = ["sec_type", "period", "signal_type", "other_signal_type"]

    def compute(self, events: pd.DataFrame, sec_type: int, period: int,
                period_start, period_end) -> pd.DataFrame:
        """One period's ordered-pair overlap counts + ratios.

        Host boundary (documented at module level): ONE unwrap of the
        year slice, a day × family presence grid by sorted-rank
        arithmetic, pairwise counts by boolean/matrix ops. Orientation:
        row = base family A, column = other family B — overlap_pct[a, b]
        = |A∩B| / |A|.
        """
        year_slice = events[events["year"] == period]
        if year_slice.empty:
            return pd.DataFrame()

        codes = host_array(year_slice["code"])
        day_nums = host_array(
            (year_slice["date_epoch"] / SECONDS_PER_DAY).astype("int64"))
        fams = host_array(year_slice["signal_type"]).astype("int64")
        buy = host_array(year_slice["has_buy"]).astype(np.int8)
        sell = host_array(year_slice["has_sell"]).astype(np.int8)

        # Day keys (code, date) by rank arithmetic over sorted uniques.
        _, code_idx = np.unique(codes, return_inverse=True)
        _, day_idx = np.unique(day_nums, return_inverse=True)
        keys = code_idx * (int(day_idx.max()) + 1) + day_idx
        _, key_idx = np.unique(keys, return_inverse=True)
        fam_codes, fam_idx = np.unique(fams, return_inverse=True)
        n_keys = int(key_idx.max()) + 1
        n_universe = n_keys
        n_fam = int(fam_codes.size)

        flat = key_idx * n_fam + fam_idx          # one row per (day, family)
        presence = np.zeros(n_keys * n_fam, dtype=bool)
        presence[flat] = True
        buy_hits = np.zeros(n_keys * n_fam, dtype=bool)
        buy_hits[flat[buy == 1]] = True
        sell_hits = np.zeros(n_keys * n_fam, dtype=bool)
        sell_hits[flat[sell == 1]] = True

        grid = presence.reshape(n_keys, n_fam)
        buy_g = buy_hits.reshape(n_keys, n_fam)
        sell_g = sell_hits.reshape(n_keys, n_fam)

        n_base = grid.sum(axis=0).astype(np.int64)
        inter = np.matmul(grid.T.astype(np.int64), grid)       # [a, b] = |A_a ∩ A_b|
        bi = buy_g[:, :, None]
        bj = buy_g[:, None, :]
        si = sell_g[:, :, None]
        sj = sell_g[:, None, :]
        same = ((bi & bj) | (si & sj)).sum(axis=0).astype(np.int64)
        opposite = ((bi & sj) | (si & bj)).sum(axis=0).astype(np.int64)

        # Ordered pairs A→B, A != B (every family present has n_base > 0).
        base_f, other_f = np.meshgrid(np.arange(n_fam), np.arange(n_fam),
                                      indexing="ij")
        mask = base_f != other_f
        n_overlap = inter[base_f, other_f][mask]
        n_b = n_base[base_f][mask]
        n_o = n_base[other_f][mask]
        n_same = same[base_f, other_f][mask]
        n_opp = opposite[base_f, other_f][mask]

        overlap_pct = n_overlap / n_b
        expected_pct = n_o / n_universe
        union = (n_b + n_o - n_overlap).astype(np.float64)
        jaccard = n_overlap / union
        lift = (overlap_pct / expected_pct).astype(np.float64)

        n_rows = int(n_overlap.size)
        columns = {
            "sec_type": np.full(n_rows, sec_type, "int64"),
            "period": np.full(n_rows, period, "int64"),
            "signal_type": fam_codes[base_f][mask].astype(np.int64),
            "other_signal_type": fam_codes[other_f][mask].astype(np.int64),
            "n_base": n_b.astype(np.int64),
            "n_other": n_o.astype(np.int64),
            "n_universe": np.full(n_rows, n_universe, "int64"),
            "n_overlap": n_overlap.astype(np.int64),
            "n_same_action": n_same.astype(np.int64),
            "n_opposite_action": n_opp.astype(np.int64),
            "overlap_pct": overlap_pct.astype(np.float64),
            "expected_pct": expected_pct.astype(np.float64),
            "jaccard": jaccard.astype(np.float64),
            "lift": lift,
        }
        columns.update(_period_date_columns(n_rows, period_start, period_end))
        real_pd = getattr(pd, "_fsproxy_slow", pd)
        return real_pd.DataFrame(columns)[_OVERLAP_COLUMNS]

    async def run(self, conn, events: pd.DataFrame, sec_type: int,
                  period: int, period_start, period_end) -> int:
        out = self.compute(events, sec_type, period, period_start, period_end)
        if out.empty:
            return 0
        rows = sanitize_for_db_insert(
            out, numeric_cols=_OVERLAP_NUMERICS, round_to=4,
            date_cols=["period_start", "period_end"])
        await _purge_periods(conn, self.table, sec_type, [period])
        written = await bulk_upsert_async(conn, self.table, rows,
                                          key_columns=self.key_columns)
        print(f"[{self.title}] period {period}: {written} row(s)")
        return written


async def _purge_periods(conn, table: str, sec_type: int,
                         periods: list[int]) -> None:
    """Per-period purge before upsert (the recomputed period's stale
    rows — families/pairs that disappeared — must not survive)."""
    action = f"analysis/signal_rules_audit/steps/{table.split('.', 1)[1]}/purge_period"
    purge_sql = load_sql_write(action)
    await conn.executemany(
        purge_sql, [(sec_type, period) for period in periods])
