"""DataFrame-native signal base (analyze.analysis_signals.signals
.df_base).

``DfSignalEngine`` — the cudf.pandas replacement for the numpy
``PctSignalEngine``: the per-code indicator history is LOADED AS ONE
LONG DATAFRAME (the forecasts fetch contract: ``::float8`` numerics,
epoch→datetime64 dates) and every detection step is a vectorized
DataFrame op:

  - the per-(code, window, side) trigger bars are the linearly-
    interpolated window quantiles, resolved by the same rank+gather
    sort the forecasts df engine uses (pos = q·(valid_n−1) — the exact
    ``_engine.quantile_threshold`` semantics) — no numpy wide matrices;
  - the side masks / in-month filter / live gate / confirm gate are
    boolean column algebra + keyed joins;

except the one inherently SEQUENTIAL piece: the fixed-skip cooldown is
a greedy scan (an accepted day depends on the previous accepted day),
kept as the existing ``apply_cooldown_rolling`` on the month's
(dates × codes) bool pivot — pandas pivot in, numpy chain state out,
accepted cells melted back into the frame. Chains carry across months
(the rolling-scan contract).

Subclasses declare the metric's emission config (signal_type /
sub_type / param_key / fmt / sides) and the ADAPTIVE TRIGGER GRADE —
the forecast bucket config the live signal keys off (rsi: the 1%
tail, std: 2σ of ma60, … see analysis_signals.config).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date, timedelta

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_forecasts._dfengine import _finite_mask
from analyze.analysis_forecasts.config import WINDOW_YEARS
from analyze.analysis_forecasts.wide.months import _shift_years
from analyze.analysis_signals.config import COOLDOWN_DAYS, SIDE_ACTION
from analyze.analysis_signals.signals._base import (
    confirm_row_fields,
)
import json
from analyze.analysis_forecasts.wide import round6
from analyze.analysis_forecasts.wide import apply_cooldown_rolling

_NO_ACCEPT = -(2 ** 62)


def quantile_bars(
    values: pd.DataFrame,
    group_cols: list[str],
    q_spec: pd.DataFrame,
) -> pd.DataFrame:
    """Per-(group, q) linearly-interpolated quantile bars — the
    forecasts df engine's sort + cumcount-rank + keyed-gather (the
    cudf-native ``_engine.quantile_threshold``)."""
    v = values.sort_values(group_cols + ["value"]).reset_index(drop=True)
    g = v.groupby(group_cols, sort=False)
    v["_r"] = g.cumcount()
    ranks = v[group_cols + ["_r", "value"]]
    n_tbl = g.size().rename("_n").reset_index()
    pos = q_spec.merge(n_tbl, on=group_cols, how="inner")
    n = pos["_n"]
    p = pos["q"] * (n - 1)
    pos["_i0"] = np.floor(p).astype("int64")
    pos["_i1"] = np.minimum(pos["_i0"] + 1, n - 1).astype("int64")
    pos["_frac"] = p - pos["_i0"]
    b = pos.merge(
        ranks, left_on=group_cols + ["_i0"],
        right_on=group_cols + ["_r"], how="left",
    ).rename(columns={"value": "_v0"}).drop(columns=["_r"])
    b = b.merge(
        ranks, left_on=group_cols + ["_i1"],
        right_on=group_cols + ["_r"], how="left",
    ).rename(columns={"value": "_v1"}).drop(columns=["_r"])
    b["bar"] = b["_v0"] + b["_frac"] * (b["_v1"] - b["_v0"])
    return b.drop(columns=["_i0", "_i1", "_frac", "_n", "_v0", "_v1"])


class DfSignalEngine(ABC):
    """Rolling month-scan signal detection on DataFrames.

    Subclasses implement ``detect(win, bars)`` — the metric's raw
    trigger frame from the month window (code, date, key, side,
    value, bar) — and declare the emission config. The base resolves
    the window bars, runs the cooldown chains, filters to the snapshot
    month, and hands the ACCEPTED trigger frame back per month.
    """

    def __init__(
        self,
        *,
        df: pd.DataFrame,
        codes: list[str],
        sec_type: str,
        first_dates: dict[str, date],
        windows: list,            # MonthWindow-like: stat_month
        pct: int,
        cooldown_days: int,
        confirm: dict = None,     # (stat_month, key, side) -> confirmed set
        signal_type: str = "",
        sub_type: dict = None,    # value col -> sub_type string
        param_key: str = "",
        fmt: str = ".2f",
        sides_filter: tuple | None = None,
    ) -> None:
        self.df = df
        self.codes = codes
        self.sec_type = sec_type
        self.first_dates = first_dates
        self.windows = windows
        self.pct = pct
        self.cooldown_days = cooldown_days
        self.confirm = confirm or {}
        self.signal_type = signal_type
        self.sub_type = sub_type or {}
        self.param_key = param_key
        self.fmt = fmt
        self.sides_filter = sides_filter
        self._cal: pd.DataFrame | None = None

    # -- hooks ---------------------------------------------------------

    @abstractmethod
    def value_cols(self) -> list[str]:
        """The indicator columns of the fetched frame, one per key
        (e.g. ["rsi_6days", ...])."""

    @abstractmethod
    def detect(self, win: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
        """The metric's raw trigger frame: filter/join the window rows
        against the resolved ``bars`` (code, key, side, bar)."""

    # -- shared machinery -----------------------------------------------

    def run(self) -> Iterator[tuple[date, pd.DataFrame]]:
        """Yield (stat_month, accepted-trigger frame) per snapshot
        month — detection vectorized, cooldown chains carried across
        months."""
        cal = (
            self.df[["date"]]
            .drop_duplicates()
            .sort_values("date")
            .reset_index(drop=True)
        )
        cal["_t"] = cal.index.astype("int64")
        self._cal = cal
        df = self.df.merge(cal, on="date", how="left").sort_values(
            ["code", "date"]).reset_index(drop=True)

        codes_frame = pd.DataFrame({
            "code": list(self.first_dates.keys()),
            "first_date": pd.to_datetime(list(self.first_dates.values())),
        })
        chains: dict[tuple, np.ndarray] = {}
        for mw in self.windows:
            # the trailing WINDOW_YEARS window ending at the month-end
            lower_d = _shift_years(mw.stat_month, -WINDOW_YEARS)                 + timedelta(days=1)
            lower, upper = np.datetime64(lower_d), np.datetime64(mw.stat_month)
            in_month_from = np.datetime64(mw.stat_month.replace(day=1))
            live = codes_frame[
                codes_frame["first_date"] < np.datetime64(mw.lower)
            ]["code"]
            win = df[
                (df["date"] > lower) & (df["date"] <= upper)
                & df["code"].isin(live)
            ]
            if win.empty:
                continue
            win = win[win["date"] >= in_month_from]
            if win.empty:
                continue

            # ---- the window's quantile bars (vectorized, all keys)
            long = win.melt(
                id_vars=["code", "date", "_t"],
                value_vars=self.value_cols(),
                var_name="_wcol", value_name="value",
            )
            long["key"] = long["_wcol"].map(
                {c: k for c, k in zip(self.value_cols(), self.value_cols())}
            )
            long = long[_finite_mask(long["value"])]
            bars = self.detect_window_bars(long)
            trig = self.detect(win, bars)
            if trig.empty:
                continue

            # ---- the sequential cooldown per (key, side) — the month's
            # (dates × codes) bool pivot through the greedy scan
            accepted_parts = []
            g_dates = win[["date"]].drop_duplicates().sort_values("date")
            g_dates["_row"] = range(len(g_dates))
            lo_row = int(g_dates["_row"].iloc[0])
            for (key, side), grp in trig.groupby(["key", "side"]):
                piv = grp.pivot_table(
                    index="date", columns="code", values="raw",
                    aggfunc="max", fill_value=False,
                ).astype(bool)
                piv = piv.reindex(g_dates["date"], fill_value=False)
                chain = chains.get((key, side))
                if chain is None:
                    chain = np.full(piv.shape[1], _NO_ACCEPT, dtype=np.int64)
                acc, chain = apply_cooldown_rolling(
                    piv.to_numpy(), chain, lo_row, self.cooldown_days,
                )
                chains[(key, side)] = chain
                acc_df = pd.DataFrame(acc, index=piv.index,
                                      columns=piv.columns)
                st = acc_df.stack(future_stack=True)
                cells = st[st].reset_index()[["date", "code"]]
                if cells.empty:
                    continue
                cells["key"] = key
                cells["side"] = side
                accepted_parts.append(
                    cells.merge(
                        grp[["code", "date", "key", "side", "value",
                             "bar"]],
                        on=["code", "date", "key", "side"], how="left",
                    )
                )
            if not accepted_parts:
                continue
            acc = pd.concat(accepted_parts, ignore_index=True)
            yield mw.stat_month, self._signal_rows(mw, acc)


    def _signal_rows(self, mw, acc: pd.DataFrame) -> list[dict]:
        """The signal rows of one month — the confirm gate ANDed after
        the cooldown (a missing confirm entry suppresses that (key,
        side) emission while its chain still advanced), then the row
        contract of the legacy PctSignalEngine.run()."""
        rows: list[dict] = []
        end = mw.stat_month.isoformat()
        for r in acc.to_dict("records"):
            key, side = r["key"], r["side"]
            if self.sides_filter is not None and side not in self.sides_filter:
                continue
            conf = self.confirm.get((mw.stat_month, key, side))
            if not conf:
                continue
            confirmed = {str(c) for c in conf[0]}
            if r["code"] not in confirmed:
                continue
            conf_info = confirm_dicts(conf)
            info = conf_info.get(r["code"])
            w = int(key.rsplit("_", 1)[1])
            sub = self.sub_type[key]
            op = ">=" if side == "top" else "<="
            fields = confirm_row_fields(info)
            rows.append({
                "code": r["code"],
                "sec_type": self.sec_type,
                "signal_type": self.signal_type,
                "signal_sub_type": sub,
                "date": r["date"],
                "action": SIDE_ACTION[side],
                "signal_threshold": round6(float(r["bar"])),
                "confidence": fields["confidence"],
                "tier": fields["tier"],
                "code_baseline": fields["code_baseline"],
                "code_rank": fields["code_rank"],
                "reason": (
                    f"{sub}={r['value']:{self.fmt}} {op} {side} "
                    f"{self.pct}% threshold {float(r['bar']):.4f} of "
                    f"trailing 5y window ending {end}"
                ),
                "params": json.dumps({
                    self.param_key: w, "side": side, "pct": self.pct,
                    "cooldown_days": self.cooldown_days,
                    "conf_period": info["conf_period"] if info else None,
                    "confidence_factors":
                        info["conf_factors"] if info else None,
                }),
            })
        return rows


class PctDfSignalEngine(DfSignalEngine):
    """The percentile family (mov_rsi / mov_gap): the ADAPTIVE TRIGGER
    GRADE is the extreme tail — a day triggers when its value sits in
    the top ``pct``% (side top) / bottom ``pct``% (side bottom) of the
    trailing 5-year window, per (code, key)."""

    def detect_window_bars(self, long: pd.DataFrame) -> pd.DataFrame:
        q_spec = long[["key", "code"]].drop_duplicates()
        sides = ("top", "bottom")
        q_small = pd.DataFrame({
            "key": [k for k in self.value_cols() for _ in sides],
            "side": [s for _ in self.value_cols() for s in sides],
            "q": [
                (1.0 - self.pct / 100.0 if s == "top"
                 else self.pct / 100.0)
                for _ in self.value_cols() for s in sides
            ],
        })
        q_spec = q_spec.merge(q_small, on="key", how="inner")
        return quantile_bars(
            long[["key", "code", "value"]],
            group_cols=["key", "code"], q_spec=q_spec,
        )

    def detect(self, win: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
        bars = bars[bars["bar"].notna()]
        if bars.empty:
            return pd.DataFrame()
        cand = bars.merge(
            win.melt(id_vars=["code", "date", "_t"],
                     value_vars=self.value_cols(),
                     var_name="key", value_name="value")
            .assign(key=lambda d: d["key"]),
            on=["key", "code"], how="inner",
        )
        top = (cand["side"] == "top") & (cand["value"] >= cand["bar"])
        bot = (cand["side"] == "bottom") & (cand["value"] <= cand["bar"])
        out = cand[top | bot].copy()
        out["raw"] = True
        return out
