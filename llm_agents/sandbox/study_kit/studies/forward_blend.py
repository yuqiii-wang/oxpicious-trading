"""study_kit.studies.forward_blend — the measured dir_ave forward blend."""
from __future__ import annotations

import pandas as pd

# The repo's measured forward blend (dir_ave): horizon → weight.
FORWARD_BLEND_WEIGHTS: dict[int, float] = {
    0: 0.10, 3: 0.15, 5: 0.50, 10: 0.15, 20: 0.10,
}


def forward_blend(
    close: pd.Series,
    weights: dict[int, float] | None = None,
) -> pd.Series:
    """The repo's measured forward blend (dir_ave): the weighted mix of
    the 0/3/5/10/20d forward returns — what every analysis_signals cell
    is measured against. Rows without the full longest-horizon future
    are NaN. Pass ``weights`` to blend a custom horizon set."""
    w = FORWARD_BLEND_WEIGHTS if weights is None else weights
    blended = pd.Series(0.0, index=close.index)
    for horizon, weight in w.items():
        blended = blended + weight * (close.shift(-horizon) / close - 1.0)
    realized = close.shift(-max(w)).notna()
    return blended.where(realized)
