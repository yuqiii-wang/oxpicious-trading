"""FramePrimitives — the shared vectorized primitives of SignalEngine
(analyze.analysis_signals.engines._primitives).

action_of / frame_records / window_start (+ the private date
materialization _dates_to_python and the side→action constants): the
native cudf ops the frame machinery (_frame) AND every family's
build() use — stateless, so they live on one mixin mixed into the
SignalEngine base.

Every frame stays NATIVE-DTYPE ONLY (see _base for the contract): the
python-date conversions in frame_records are the SANCTIONED scalar
materializations at the COPY boundary (numpy scalars unwrap to python
scalars; date columns convert via the native .dt accessors and never
stream through to_dict).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

# The side→action constants are single-sourced in config (also read by
# the engines._quality breach check).
from analyze.analysis_signals.config import (
    ABOVE_ACTION,
    BELOW_ACTION,
    SELL_SIDES,
)


class FramePrimitives:
    """action_of / frame_records / window_start — the shared vectorized
    helpers of the machinery and the family builds (see module
    docstring)."""

    def window_start(self, month: date) -> date:
        """The forecast period's first day: the bucket's trailing-window
        start (stat_month − 5y) + 1 day. stat_month is always a COMPLETED
        month-end, so the day after (M − 5y) is exactly the first of the
        next month."""
        year, mon = month.year - 5, month.month
        if mon == 12:
            return date(year + 1, 1, 1)
        return date(year, mon + 1, 1)

    def action_of(self, side: pd.Series) -> pd.Series:
        """The strategy action by the bucket's OWN stored side (sell for
        top/upper, buy otherwise) — vectorized, no CASE anywhere."""
        action = pd.Series(BELOW_ACTION, index=side.index)
        return action.mask(side.isin(SELL_SIDES), ABOVE_ACTION)

    def frame_records(
        self, df: pd.DataFrame, date_cols: tuple[str, ...] = (),
    ) -> list[dict]:
        """The COPY-boundary materialization: NATIVE-dtype frame → row
        dicts (NaN floats sweep to None inside _copy_clean_value at the
        COPY; numpy scalars unwrap to python scalars; the date columns
        are converted by _dates_to_python and never streamed through
        to_dict)."""
        core = df.drop(columns=list(date_cols)) if date_cols else df
        records: list[dict] = core.to_dict("records")
        out: list[dict] = [
            {k: (v.item() if isinstance(v, np.generic) else v)
             for k, v in rec.items()}
            for rec in records
        ]
        for col in date_cols:
            for rec, d in zip(out, self._dates_to_python(df[col])):
                rec[col] = d
        return out

    # ---- private helpers --------------------------------------------------------

    def _dates_to_python(self, s: pd.Series) -> list[date]:
        """A datetime64 column → python dates (SANCTIONED scalar
        materialization via the native .dt int accessors — unit-proof, no
        datetime64[D] casts)."""
        years = s.dt.year.to_numpy().tolist()
        months = s.dt.month.to_numpy().tolist()
        days = s.dt.day.to_numpy().tolist()
        return [
            date(int(y), int(m), int(d))
            for y, m, d in zip(years, months, days)
        ]
