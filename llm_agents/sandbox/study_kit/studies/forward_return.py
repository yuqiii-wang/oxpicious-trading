"""study_kit.studies.forward_return — realized forward returns (one file)."""
from __future__ import annotations

from typing import Iterable

import pandas as pd


def forward_return(
    close: pd.Series,
    horizons: Iterable[int] = (1, 3, 5, 10, 20),
) -> pd.DataFrame:
    """Forward close-to-close returns per horizon, as fractions — the
    realized future, so only rows with the FULL horizon realized are
    measurable (the tail rows are NaN by construction, not missing
    data). Any horizon set via ``horizons``.
    """
    out = {f"fwd{h}": close.shift(-h) / close - 1.0
           for h in horizons}
    return pd.DataFrame(out, index=close.index)
