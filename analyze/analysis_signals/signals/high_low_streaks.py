"""high_low_streaks signals (analysis_signals.signals) — MA-Spread
High/Low streak MEAN-MID anchor days.

The forecast buckets of analysis_forecasts.high_low_streaks audit every
band-break excursion streak of analysis.mov_ave_high_low_pct_streaks at
its MEAN-MID anchor day (the ((day_count-1)//2 + 1)-th trading day of
the span — an 8-day streak anchors its 4th day). This engine emits
exactly those anchor days as signals: the detection reuses the
FORECASTS ENGINE'S OWN MACHINERY verbatim —
analysis_forecasts.fetch.fetch_high_low_streaks (streaks + SQL-derived
side, unrounded end close vs the end month band) and
analysis_forecasts.compute_high_low_streaks.build_streak_anchor_cells
(the (T, C) cumulative own-row anchor resolution) — so the signal days
are the bucket trigger days 1:1.

Side semantics (mean reversion — the forecasts study's finding):
  side top    — ABOVE-band excursion (close on end_date > the end
                month band's high_val) → action sell: above-band
                streaks drift DOWN from the mid anchor;
  side bottom — BELOW-band excursion (close < low_val) → action buy:
                below-band streaks drift UP.

Differences from the mov_* engines (by design):
  - EX-POST anchor, write-once months: the streak length — hence its
    mid — is known only after the streak closes, and signal months are
    never refreshed, so __main__ targets a stat_month M only once the
    forecasts table has months >= HL_STREAKS_RESOLVE_LAG_MONTHS beyond
    M (every streak anchored in M final by then), and this engine
    additionally drops anchors whose streak END sits within
    HL_STREAKS_GAP_TOLERANCE + 1 grid rows of the fetched data edge
    (belt-and-braces against a stale streaks table).
  - No cooldown (one trigger per streak; streaks are inherently
    separated by a 6+-day in-band gap or a side switch).
  - signal_threshold = the streak side's band edge in PRICE space
    (high_val for top / low_val for bottom — the level the close
    crossed to be out-of-band); the day-close live mirror records the
    anchor day's close against it (mov_std's price-space pattern).
  - confidence = the driving-factor composite at the matching forecast
    bucket's argmax period (ConfirmMap keyed (stat_month,
    "{band_period}_{pct_type}", side)); the factor breakdown rides in
    params JSON (see _base / gate.py).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_forecasts.compute_high_low_streaks import COMBOS
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    date_ordinals,
    round6,
)
from analyze.analysis_signals.config import (
    HL_STREAKS_GAP_TOLERANCE,
    HL_STREAKS_SIDE_ACTION,
    SIGNAL_TYPE_HL_STREAKS,
    sub_type_hls,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    confirm_dicts,
    confirm_row_fields,
    _in_month_rows,
    _ord_to_date,
)


def hls_extra_cols(streaks_df: pd.DataFrame) -> dict[str, np.ndarray]:
    """The signals family's per-streak payloads for
    build_streak_anchor_cells' ``extra_cols`` — arrays over the streaks
    frame's ROWS, carried through the builder's validity filter into
    the cells (the join back to streaks_df is NOT 1:1: end_t collapses
    end dates onto the previous grid row wherever the forecast frame
    dropped rows the streaks step saw, so the payloads ride the mask
    instead of being re-joined):

      band_edge  — the streak side's band edge in PRICE space (the end
                   month band's high_val for top / low_val for bottom —
                   fetch_high_low_streaks' band_high / band_low), the
                   emitted signal_threshold;
      start_ord / end_ord — the span dates as epoch-day ordinals (the
                   reason text + params JSON's streak context).
    """
    band_high = host_array(
        streaks_df["band_high"].to_numpy()
    ).astype(np.float64)
    band_low = host_array(
        streaks_df["band_low"].to_numpy()
    ).astype(np.float64)
    top = host_array(streaks_df["side"].to_numpy()) == "top"
    return {
        "band_edge": np.where(top, band_high, band_low),
        "start_ord": date_ordinals(streaks_df["start_date"]),
        "end_ord": date_ordinals(streaks_df["end_date"]),
    }


def compute_hls_signals(
    streaks_df: pd.DataFrame,
    cells: dict[str, np.ndarray],
    price_mat: np.ndarray,
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — the
    high_low_streaks family.

    Args:
      streaks_df: fetch_high_low_streaks' frame — the cells'
          provenance: __main__ resolves the cells with
          build_streak_anchor_cells(..., extra_cols=hls_extra_cols(
          streaks_df)), so the factory consumes the CARRIED arrays
          (band_edge / start_ord / end_ord) instead of re-joining this
          frame (not 1:1 — see hls_extra_cols).
      cells: build_streak_anchor_cells output (ABSOLUTE grid rows),
          extended with the carried "band_edge" / "start_ord" /
          "end_ord" keys.
      price_mat: (T, C) wide price matrix (scatter_column of the input
          frame's "price") — the anchor day's close for the reason /
          live-mirror value.
      windows: resolved MonthWindow list for the target months.
      codes: sorted code list (matrix column order).
      sec_type: emitted into every row.
      first_ord: (C,) per-code first data date as ABSOLUTE epoch-day
          ordinals — full-window history gate (first data strictly
          before the window start).
      grid_ord: (T,) int64 day ordinals of the FULL grid (build_grid)
          — also the closed-streak guard's data edge (T - 1).
      confirm: (stat_month, "{band_period}_{pct_type}", side) → the
          confirmed-code calibration entry from gate.fetch_confirm on
          analysis_forecasts.high_low_streaks.
    """
    codes_arr = np.asarray(codes)
    T_total = len(grid_ord)
    # A streak whose end has fewer than GAP_TOLERANCE + 1 grid rows
    # after it may still extend (its mid would shift) — provisional,
    # dropped (see the module docstring).
    edge = T_total - 1 - (HL_STREAKS_GAP_TOLERANCE + 1)

    t_all = cells["t"]
    c_all = cells["c"]
    closed = cells["end_t"] <= edge

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        live = first_ord < mw.lo_ord
        if not live.any():
            continue
        g = grid_ord[lo:hi]
        in_month = _in_month_rows(g, mw.stat_month)

        # Anchor cells of CLOSED streaks, inside the snapshot month,
        # live codes. The in_month gather is clipped + guarded by the
        # in-window mask (the raw t - lo is out of range for anchors
        # outside this window — python evaluates the index before the
        # & masking, so the gather itself must be safe).
        in_win = (t_all >= lo) & (t_all < hi)
        t_loc = np.clip(t_all - lo, 0, hi - lo - 1)
        win = in_win & closed & in_month[t_loc] & live[c_all]
        if not win.any():
            continue
        t_w = t_all[win]
        c_w = c_all[win]
        combo_w = cells["combo"][win]
        side_w = cells["side_ord"][win]
        edge_w = cells["band_edge"][win]
        dc_w = cells["day_count"][win]
        pos_w = cells["anchor_pos"][win]
        sd_w = cells["start_ord"][win]
        ed_w = cells["end_ord"][win]

        rows: list[dict] = []
        for ci, (period, pct_t) in enumerate(COMBOS):
            sel = combo_w == ci
            if not sel.any():
                continue
            for side in ("top", "bottom"):
                conf = confirm.get(
                    (mw.stat_month, f"{period}_{pct_t}", side))
                if conf is None or conf[0].size == 0:
                    continue
                conf_info = confirm_dicts(conf)
                conf_mask = np.isin(
                    codes_arr,
                    np.asarray(conf[0], dtype=codes_arr.dtype),
                )
                s_sel = sel & (
                    side_w == (1 if side == "top" else -1)
                ) & conf_mask[c_w]
                if not s_sel.any():
                    continue
                sub = sub_type_hls(period, pct_t)
                end = mw.stat_month.isoformat()
                for j in np.nonzero(s_sel)[0].tolist():
                    t = int(t_w[j])
                    i = int(c_w[j])
                    row_code = codes[i]
                    info = conf_info.get(row_code)
                    fields = confirm_row_fields(info)
                    close = float(price_mat[t, i])
                    edge_v = float(edge_w[j])
                    n_dc = int(dc_w[j])
                    pos = int(pos_w[j])
                    sd = _ord_to_date(int(sd_w[j]))
                    ed = _ord_to_date(int(ed_w[j]))
                    op = ">" if side == "top" else "<"
                    rows.append({
                        "code": row_code,
                        "sec_type": sec_type,
                        "signal_type": SIGNAL_TYPE_HL_STREAKS,
                        "signal_sub_type": sub,
                        "date": _ord_to_date(int(grid_ord[t])),
                        "action": HL_STREAKS_SIDE_ACTION[side],
                        "signal_threshold": round6(edge_v),
                        "confidence": fields["confidence"],
                        "tier": fields["tier"],
                        "code_baseline": fields["code_baseline"],
                        "code_rank": fields["code_rank"],
                        "reason": (
                            f"hls {sub} {side}: streak day {pos}/{n_dc} "
                            f"(mean-mid anchor of {sd.isoformat()}"
                            f"→{ed.isoformat()}), close {close:.2f} "
                            f"{op} band {edge_v:.2f}, window ending {end}"
                        ),
                        "params": json.dumps({
                            "band_period": period,
                            "pct_type": pct_t,
                            "side": side,
                            "day_count": n_dc,
                            "anchor_pos": pos,
                            "start_date": sd.isoformat(),
                            "end_date": ed.isoformat(),
                            "anchor_close": round6(close),
                            "band_val": round6(edge_v),
                            "conf_period":
                                info["conf_period"] if info else None,
                            "confidence_factors":
                                info["conf_factors"] if info else None,
                        }),
                    })
        if rows:
            yield mw.stat_month, rows