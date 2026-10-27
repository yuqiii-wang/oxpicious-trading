"""hedge_pairs event-member record helpers — the two pieces the trio's
witness records and the spread's leg records share: the event day's
row lookup in the pair grid and the RELATIVE SIGNAL STRENGTH
confidence link (2026-10-25, user-directed "cell signals under
portfolio should also have non-zero confidence")."""
from __future__ import annotations

import numpy as np

from analysis.analysis_signals import config as cfg


def locate_date(grid: dict, date) -> int | None:
    """The event day's row in the pair grid (host searchsorted on the
    sorted datetime64[D] array), None when the day is not one of the
    pair's own traded days."""
    pos = int(np.searchsorted(
        grid["date_d"], np.datetime64(date), side="right",
    ) - 1)
    if not (0 <= pos < len(grid["date_d"])) \
            or grid["date_d"][pos] != np.datetime64(date):
        return None
    return pos


def link_confidence(members: list[dict], depths: list[float],
                    row: dict) -> None:
    """Stamp each member's confidence by the relative-strength link:
    the anchor is the portfolio row's own basis-point record (the
    portfolio cell's depth-scaled record) and each member carries it
    scaled by its own breach depth over the coordinated members' MEAN
    depth — an average-strength curve carries the portfolio's full
    confidence, a deeper one up to 2x (the depth-cap convention), a
    shallower one proportionally less. The 1bp floor keeps a
    razor-thin breach (depth a rounding-hair past the bar) on a weak
    event strictly positive on the integer basis-point scale."""
    mean_depth = sum(depths) / len(depths) if depths else 0.0
    pf_bp = float(row["confidence"])
    for member, depth in zip(members, depths):
        rel = min(max(depth / mean_depth, 0.0), 2.0) \
            if mean_depth > 1e-9 else 1.0
        member["confidence"] = max(1, int(round(min(
            pf_bp * rel, cfg.CONFIDENCE_CAP_PCT * 100.0))))
