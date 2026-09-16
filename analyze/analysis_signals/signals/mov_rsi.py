"""mov_rsi signals (analysis_signals.signals) — RSI extreme-percentile
days.

compute_rsi_signals — rsi_{W}days in the top pct% (RSI_PCT = 1 →
action=sell) or bottom pct% (action=buy) of the window's non-NULL
values (linear-interpolated percentile threshold, gathered from the
column-sorted window matrix — the same ``_thresholds`` helper the
forecast RSI engine uses). Threshold / cooldown / gate machinery in
_base.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts._engine import quantile_threshold
from analyze.analysis_forecasts.config import RSI_WINDOWS
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    apply_cooldown_rolling,
    round6,
)
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    RSI_MIN_WINDOW_AGREEMENT,
    RSI_PCT,
    RSI_SIGNAL_SIDES,
    SIDE_ACTION,
    sub_type_rsi,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    PctSignalEngine,
    _NO_ACCEPT,
    _in_month_rows,
    _ord_to_date,
    confirm_dicts,
    confirm_row_fields,
)


class RsiSignalEngine(PctSignalEngine):
    """PctSignalEngine + the RSI_MIN_WINDOW_AGREEMENT emission rule: a
    day is emitted only when the month's cooldown-accepted detections
    cover >= RSI_MIN_WINDOW_AGREEMENT RSI windows on the SAME side (the
    forecast buckets keep the single-window view — this is a
    signal-emission confirmation only). run() is overridden as the
    base's rolling month scan split in two phases: detection (verbatim
    base semantics — per-(key, side) chains carried across months,
    independent of agreement and of the confirm gate) then emission,
    which ANDs the same-side agreement count in BEFORE the confirm
    gate. Row shape / params are the base's verbatim.
    """

    def run(self) -> Iterator[tuple[date, list[dict]]]:
        codes_arr = np.asarray(self.codes)
        col = np.arange(self.C)
        pct_label = f"{self.pct}%"
        emit_sides = (
            self.SIDES if self.sides_filter is None else self.sides_filter
        )
        chains: dict[tuple[str, str], np.ndarray] = {}

        for mw in self.windows:
            lo, hi = mw.lo, mw.hi
            if lo >= hi:
                continue
            g = self.grid_ord[lo:hi]
            in_month = _in_month_rows(g, mw.stat_month)
            if not in_month.any():
                continue  # no grid day belongs to this snapshot month
            m0 = int(np.argmax(in_month))
            live = self.first_ord < mw.lo_ord

            # Phase 1 — detection, the base scan verbatim: full-window
            # thresholds, cooldown over the month's roll-in rows,
            # per-(key, side) chains carried across months.
            accepted: dict[tuple[str, str], np.ndarray] = {}
            thr_by_key: dict[str, dict[str, np.ndarray]] = {}
            vin_by_key: dict[str, np.ndarray] = {}
            for key in self.keys:
                V = self.mats[key][lo:hi]
                valid_n = np.count_nonzero(
                    ~np.isnan(V), axis=0
                ).astype(np.int64)
                if not (valid_n > 0).any():
                    continue  # all-NaN window — no trigger can be raw-True
                S = np.sort(V, axis=0)  # NaN last — quantile gathers
                thr = {
                    "top": quantile_threshold(
                        S, valid_n, col, 1.0 - self.pct / 100.0,
                    ),
                    "bottom": quantile_threshold(
                        S, valid_n, col, self.pct / 100.0,
                    ),
                }
                thr_by_key[key] = thr
                Vin = V[m0:]
                vin_by_key[key] = Vin
                for side in self.SIDES:
                    if side not in emit_sides:
                        continue
                    chain = chains.get((key, side))
                    if chain is None:
                        chain = np.full(self.C, _NO_ACCEPT, dtype=np.int64)
                    with np.errstate(invalid="ignore"):
                        mask_raw = (
                            (Vin >= thr[side][None, :])
                            if side == "top"
                            else (Vin <= thr[side][None, :])
                        )
                    acc, chain = apply_cooldown_rolling(
                        mask_raw, chain, lo + m0, COOLDOWN_DAYS,
                    )
                    chains[(key, side)] = chain
                    accepted[(key, side)] = acc

            # Phase 2 — the same-side agreement count over the accepted
            # detections, computed BEFORE any confirm lookup so a
            # missing / narrow confirm entry cannot shrink another
            # window's agreement count.
            agree: dict[str, np.ndarray] = {}
            for side in emit_sides:
                total: np.ndarray | None = None
                for key in self.keys:
                    acc = accepted.get((key, side))
                    if acc is None:
                        continue
                    total = (
                        acc.astype(np.int64)
                        if total is None
                        else total + acc
                    )
                if total is not None:
                    agree[side] = total >= RSI_MIN_WINDOW_AGREEMENT

            rows: list[dict] = []
            for key in self.keys:
                thr = thr_by_key.get(key)
                if thr is None:
                    continue
                Vin = vin_by_key[key]
                for side in self.SIDES:
                    if side not in emit_sides:
                        continue
                    ag = agree.get(side)
                    if ag is None or not ag.any():
                        continue
                    acc = accepted[(key, side)]
                    conf = self.confirm.get((mw.stat_month, key, side))
                    if conf is None or conf[0].size == 0:
                        continue
                    conf_info = confirm_dicts(conf)
                    conf_mask = np.isin(
                        codes_arr,
                        np.asarray(conf[0], dtype=codes_arr.dtype),
                    )
                    cells = acc & ag & live[None, :] & conf_mask[None, :]
                    ts, cs = np.nonzero(cells)
                    if ts.size == 0:
                        continue

                    op = ">=" if side == "top" else "<="
                    end = mw.stat_month.isoformat()
                    w = int(key.rsplit("_", 1)[1])
                    sub = self.sub_type[key]
                    thr_side = thr[side]
                    for t, i in zip(ts.tolist(), cs.tolist()):
                        v = float(Vin[t, i])
                        row_code = self.codes[i]
                        info = conf_info.get(row_code)
                        fields = confirm_row_fields(info)
                        rows.append({
                            "code": row_code,
                            "sec_type": self.sec_type,
                            "signal_type": self.signal_type,
                            "signal_sub_type": sub,
                            "date": _ord_to_date(int(g[m0 + t])),
                            "action": SIDE_ACTION[side],
                            "signal_threshold":
                                round6(float(thr_side[i])),
                            "confidence": fields["confidence"],
                            "tier": fields["tier"],
                            "code_baseline": fields["code_baseline"],
                            "code_rank": fields["code_rank"],
                            "reason": (
                                f"{sub}={v:{self.fmt}} {op} {side} "
                                f"{pct_label} threshold "
                                f"{float(thr_side[i]):.4f} of trailing "
                                f"5y window ending {end}"
                            ),
                            "params": json.dumps({
                                self.param_key: w, "side": side,
                                "pct": self.pct,
                                "cooldown_days": COOLDOWN_DAYS,
                                "conf_period":
                                    info["conf_period"] if info else None,
                                "confidence_factors":
                                    info["conf_factors"] if info else None,
                            }),
                        })
            if rows:
                yield mw.stat_month, rows


def compute_rsi_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    *,
    rsi_windows: tuple[int, ...] = RSI_WINDOWS,
    pct: int = RSI_PCT,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — RSI family.

    Args:
        mats: wide rsi matrices keyed f"rsi_{w}".
        confirm: keyed (stat_month, "rsi_{w}", side) — see
              RsiSignalEngine (the window key is the matrix key).
        rsi_windows: RSI windows to emit (default: forecasts config).
        pct: percentile width (default RSI_PCT).
    """
    engine = RsiSignalEngine(
        mats=mats,
        chg={},
        windows=windows,
        codes=codes,
        sec_type=sec_type,
        first_ord=first_ord,
        grid_ord=grid_ord,
        confirm=confirm,
        keys=[f"rsi_{w}" for w in rsi_windows],
        pct=pct,
        signal_type="mov_rsi",
        sub_type={f"rsi_{w}": sub_type_rsi(w) for w in rsi_windows},
        param_key="rsi_window",
        fmt="0.2f",
        sides_filter=RSI_SIGNAL_SIDES.get(sec_type),
    )
    return engine.run()