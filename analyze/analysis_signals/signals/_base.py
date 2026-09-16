"""Shared signal-engine machinery (analysis_signals.signals).

For each target stat month's trailing 5-year window [lo, hi) of the
(T, C) wide grid — the SAME window, thresholds, cooldown and
full-window history gate the analysis_forecasts bucket engines use —
the per-family engines (mov_rsi / mov_gap / mov_std / px_vol) detect
the extreme days and emit signal rows. This module holds the pieces
every family shares:

  - ConfirmMap — the confirmed-code calibration map passed by
    __main__ (built by gate.fetch_confirm).
  - The full-window live gate, the snapshot-month row mask and the
    calibration-value helpers.
  - PctSignalEngine — the WideEngineBase subclass behind
    compute_rsi_signals / compute_gap_signals (the percentile family;
    the 2026-09-15 GPU pass's reference subclass).

Differences from the forecast engines (by design):
  - No forward-change aggregation, no market-hype split — signals are
    pure detection rows (threshold / reason / params / action /
    confidence).
  - Only days INSIDE the snapshot month M are emitted: each date is
    owned by exactly one monthly snapshot, so the date-level PK never
    conflicts across months. The months are scanned ROLLINGLY
    (ascending, month at a time): each month only its roll-in rows are
    scanned, with the fixed-skip cooldown chain carried across months
    (apply_cooldown_rolling) — a trigger late in month M-1 suppresses
    early-M days exactly as before, but mid-window rows are reused
    instead of re-scanned per month, and the chain no longer resets at
    each month's window start (a live rolling detector carries its
    cooldown across month ends too).
  - confidence = the DRIVING-FACTOR COMPOSITE on the bucket's MIXED
    forecast row (see gate.py): a weighted blend of evidence (t-stat),
    efficiency (sharpe), consistency (probability lift over the base
    rate) and the code's prior mean composite — all computed in the
    SIGNAL'S direction (dir_ave is sign-flipped for top/upper, so a
    buy row's confidence speaks about the upward reversal and a sell
    row's about the downward), all horizon-free and comparable across
    families / sec_types. The mixed row is the forecasts layer's
    FIXED-weight blend of the four horizon rows (5d 0.50 / next 0.30 /
    20d 0.15 / 60d 0.05), so every forecast horizon of the same signal
    trigger contributes to the signal; the period ('mixed') and the
    full factor breakdown are recorded in the row's params JSON
    (conf_period / confidence_factors).

Forecast-confirmation gate (forecast-result rule): a detected day is
RECORDED only when the matching analysis_forecasts bucket (same
code/sec_type/stat_month/window/side/pct|k/cooldown config) qualifies —
the bucket's MIXED forecast_results period row (the weight-blended
forward profile) has reverse_prob
> GATE_RP_MIN (reverse P > 1% — a material reversal probability) AND a
MEAN REVERSAL (dir_ave > 0 — the bucket's mean forward change reverses,
so the signal holds, not just a fat reversal tail) AND PROBABILITY LIFT
(rp above the unconditional blended base_rates probability for the same
side — the conjunct falls back to TRUE without a base_rates row) AND
MAGNITUDE LIFT (dir_ave above the sign-aligned base_ave_change — the
mean reversal must beat the window's own drift; same fallback); see
gate.py. (occurrence / t-stat bars were removed 2026-09 — the
streak-merge leaves pct-width buckets only ~3-6 merged signals, so the
bars dropped ~96% of the strongest-edge pct=1 buckets; occ / std_change
still feed the confidence's evidence / efficiency factors.)
__main__ builds the
confirmed-code sets per (stat_month, window, side) via
analysis_signals.gate.fetch_confirm and passes them as `confirm`; the
engines AND them into the cell mask AFTER cooldown, so detection stays
identical to the forecast buckets and the gate only filters which days
get recorded (NULL / missing forecast = not confirmed). Confidence for
each emitted row is looked up per code from the confirm map.

Device placement (2026-09-15 GPU pass): PctSignalEngine extends
analyze.analysis_forecasts._engine.WideEngineBase — the month live /
in-month masks and the confirmed-code mask are device tensors, the
bucket detection + agreement + cooldown run on the self.xp namespace
(cupy on the GPU path, numpy on CPU), and the host conversion happens
only at the row-emission boundary. Yields (stat_month, rows) so
__main__ can write month-major (one atomic transaction per month,
keeping the month-granular incremental detection crash-safe).
"""
from __future__ import annotations

import json
from datetime import date
from typing import ClassVar, Iterable, Iterator

import numpy as np

from analyze.analysis_forecasts._engine import (
    MaskBatch,
    MonthContext,
    WideEngineBase,
    quantile_threshold,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    apply_cooldown_rolling,
    round6,
)
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    SIDE_ACTION,
    TIER_NAMES,
)

_EPOCH = date(1970, 1, 1)

# Per-column "no accepted trigger yet" chain sentinel (int64 min-ish;
# any real grid row is within cooldown_days of nothing this far away).
_NO_ACCEPT = -(2**62)

# Confirmed-code calibration map passed by __main__: (stat_month,
# matrix_key, side) → tuple of seven aligned 1-D arrays over the
# confirmed codes — (codes, confidences, tier_pts, baselines, ranks,
# periods, factors). matrix_key is the engine's matrix name
# ("rsi_{w}" / "ma_{w}" / "gap_{w}" / the state string). confidence =
# the driving-factor composite on the bucket's MIXED forecast row (see
# gate.py); tier_pts 2/1/0 = proven / proven_dir / standard;
# baseline = the code's prior mean composite (mixed period); rank =
# the within-code percentile floor of the confidence; period = the
# qualifying period string (always 'mixed' — comes from the SQL);
# factors = the confidence_factors JSON object text for the params
# column. NaN = unknown (code history too short). Missing / empty
# entry means "nothing confirmed".
ConfirmMap = dict[
    tuple[date, str, str],
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray,
          np.ndarray, np.ndarray],
]


def confirm_dicts(conf: tuple) -> dict[str, dict]:
    """One ConfirmMap entry → {code: row fields}, the per-code lookup
    every engine builds once per (window, side): confidence / tier_pts
    / code_baseline / code_rank / conf_period / conf_factors (the
    factor JSON parsed). NaN calibrations stay NaN — _cal_or_none maps
    them to DB NULL at write time."""
    codes, conf_vals, tier_vals, base_vals, rank_vals, periods, factors = \
        conf
    out: dict[str, dict] = {}
    for c, cv, tv, bv, rv, p, fj in zip(
        codes, conf_vals, tier_vals, base_vals, rank_vals, periods, factors,
    ):
        try:
            fac = json.loads(fj) if fj is not None else None
        except (TypeError, ValueError):
            fac = None
        out[str(c)] = {
            "confidence": float(cv),
            "tier_pts": int(tv),
            "code_baseline": float(bv),
            "code_rank": float(rv),
            "conf_period": str(p),
            "conf_factors": fac,
        }
    return out


def confirm_row_fields(info: dict | None) -> dict:
    """The per-code confirm entry → the row's calibration fields
    (defaults mirror the pre-gate behavior: confidence 0, standard
    tier, NULL calibrations — only reachable for a code missing from
    its own confirm entry, which the isin-mask makes impossible)."""
    if info is None:
        return {
            "confidence": 0.0,
            "tier": TIER_NAMES[0],
            "code_baseline": None,
            "code_rank": None,
            "conf_period": None,
            "conf_factors": None,
        }
    return {
        "confidence": round6(info["confidence"]),
        "tier": TIER_NAMES.get(info["tier_pts"], TIER_NAMES[0]),
        "code_baseline": _cal_or_none(info["code_baseline"]),
        "code_rank": _cal_or_none(info["code_rank"]),
    }


def _cal_or_none(v: float) -> float | None:
    """Calibration value → DB NULL when unknown (NaN); round6 otherwise."""
    v = float(v)
    return round6(v) if np.isfinite(v) else None


def _ord_to_date(o: int) -> date:
    """Epoch-day ordinal (days since 1970-01-01) → python date."""
    return date.fromordinal(o + _EPOCH.toordinal())


def _in_month_rows(grid_slice: np.ndarray, stat_month: date) -> np.ndarray:
    """Bool row mask of the window slice falling inside the snapshot
    month M (grid dates >= M's first day; the upper bound is implied —
    the window ends at the month-end)."""
    return grid_slice >= (stat_month.replace(day=1) - _EPOCH).days


class PctSignalEngine(WideEngineBase):
    """The percentile-family signal engine behind compute_rsi_signals /
    compute_gap_signals — the top/bottom-pct% extreme-day detection
    over each stat month's trailing 5-year window, scanned ROLLINGLY
    (see the module docstring for the rolling / in-month / confirm-AND
    semantics).

    Constructor (all keyword-only, the WideEngineBase kwargs plus the
    family's emission config):

      mats — wide indicator matrices, one (T, C) ndarray per ``keys``
            entry (rsi_{w} / gap_{w}).
      chg / hype — accepted for WideEngineBase signature parity; the
            percentile scan uses neither (no forward-change
            aggregation, no market-hype split — see the module
            docstring), so hype may be None.
      windows / codes / sec_type / first_ord / grid_ord — as the ABC
            (windows ASCENDING — run() carries state across them).
      confirm — ConfirmMap keyed (stat_month, matrix_key, side).
      keys — matrix keys to emit (e.g. ["rsi_6", ...] /
            ["gap_2", "gap_3"]; "<metric>_<window>" — the window is
            recovered as int(key.rsplit("_", 1)[1])).
      pct — percentile width (1 = top/bottom 1%).
      signal_type — emitted signal_type ("mov_rsi" / "mov_gap").
      sub_type — matrix key → sub_type string (e.g. "rsi_6" → "rsi6").
      param_key — params JSON key for the window ("rsi_window" /
            "gap_window").
      fmt — format spec for the day's indicator value in ``reason``
            ("0-100 RSI" uses .2f, fractional gap returns .4f).
      sides_filter — the family's emission sides (e.g.
            RSI_SIGNAL_SIDES[sec_type]); None → both SIDES.
    """

    # Side names in threshold order (the ABC's config-axis contract;
    # the percentile family's two-sided stack).
    SIDES: ClassVar[tuple[str, ...]] = ("top", "bottom")

    def __init__(
        self,
        *,
        mats: dict[str, np.ndarray],
        chg: dict[str, np.ndarray],
        windows: list[MonthWindow],
        codes: list[str],
        sec_type: str,
        first_ord: np.ndarray,
        grid_ord: np.ndarray,
        confirm: ConfirmMap,
        keys: list[str],
        pct: int,
        signal_type: str,
        sub_type: dict[str, str],
        param_key: str,
        fmt: str,
        sides_filter: tuple[str, ...] | None = None,
        hype: np.ndarray | None = None,
    ) -> None:
        self.mats = mats
        self.chg = chg
        self.windows = windows
        self.codes = codes
        self.C = len(codes)
        self.sec_type = sec_type
        self.hype = hype
        self.first_ord = first_ord
        self.grid_ord = grid_ord
        self.confirm = confirm
        self.keys = keys
        self.pct = pct
        self.signal_type = signal_type
        self.sub_type = sub_type
        self.param_key = param_key
        self.fmt = fmt
        self.sides_filter = sides_filter

    # ------------------------------------------------------------------
    #  ABC hooks unused by the rolling scan
    # ------------------------------------------------------------------

    def emit_batches(self, mc: MonthContext) -> Iterable[MaskBatch]:
        """Unused — the percentile signals detect via the rolling run()
        scan below, not the ABC's MaskBatch aggregation pipeline (no
        forward-change aggregation). Present to satisfy the ABC."""
        raise NotImplementedError(
            "PctSignalEngine detects via the rolling run() scan, not the "
            "MaskBatch pipeline"
        )

    def base_rows(
        self,
        mc: MonthContext,
        carry: object,
        side: str,
        hyped: bool,
        kk: np.ndarray,
        ii: np.ndarray,
        mean_streak: np.ndarray,
    ) -> list[dict]:
        """Unused — see emit_batches (the ABC's bucket-motivation hook;
        the signal rows carry detection params, not bucket keys)."""
        raise NotImplementedError(
            "PctSignalEngine builds signal rows in run(), not via base_rows"
        )

    # ------------------------------------------------------------------
    #  The rolling month scan
    # ------------------------------------------------------------------

    def run(self) -> Iterator[tuple[date, list[dict]]]:
        """Yield (stat_month, signal rows) per stat month, windows
        ASCENDING — the rolling month scan of the module docstring.

        Per month: the percentile thresholds are recomputed from the
        FULL window slice (sort once per key, linear-interpolated
        gathers via quantile_threshold), but the cooldown runs only
        over the month's roll-in rows (the in-month slice — every grid
        day is cooldown-processed exactly once, by its owning
        snapshot) with the per-(key, side) chain carried across months
        via apply_cooldown_rolling. Detection (raw mask + cooldown) is
        gating-independent — the chain advances even for a month
        whose confirm map entry is missing; only the EMISSION is
        in-month-, live- and confirm-ANDed (a (stat_month, key, side)
        missing from the confirm map → no emission for that key/side).
        """
        codes_arr = np.asarray(self.codes)
        col = np.arange(self.C)
        pct_label = f"{self.pct}%"
        emit_sides = (
            self.SIDES if self.sides_filter is None else self.sides_filter
        )
        # Per (matrix key, side) absolute-row cooldown chain, carried
        # across months (_NO_ACCEPT sentinel = nothing accepted yet).
        chains: dict[tuple[str, str], np.ndarray] = {}

        for mw in self.windows:
            lo, hi = mw.lo, mw.hi
            if lo >= hi:
                continue
            g = self.grid_ord[lo:hi]
            in_month = _in_month_rows(g, mw.stat_month)
            if not in_month.any():
                continue  # no grid day belongs to this snapshot month
            # in_month is a suffix mask (grid dates ascend) — its first
            # True row is the month's roll-in slice start.
            m0 = int(np.argmax(in_month))
            # Full-window gate in DATE space (the ABC's month_context
            # precedent — computed inline: it would also slice the
            # forecast change matrices the signals layer never fetches).
            live = self.first_ord < mw.lo_ord

            rows: list[dict] = []
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
                Vin = V[m0:]
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
                    accepted, chain = apply_cooldown_rolling(
                        mask_raw, chain, lo + m0, COOLDOWN_DAYS,
                    )
                    chains[(key, side)] = chain

                    # Adaptive confirmation gate (after cooldown — see
                    # the module docstring): only codes whose matching
                    # bucket clears its calibrated gate, with per-code
                    # driving-factor confidence / tier / baseline / rank.
                    conf = self.confirm.get((mw.stat_month, key, side))
                    if conf is None or conf[0].size == 0:
                        continue
                    conf_info = confirm_dicts(conf)
                    conf_mask = np.isin(
                        codes_arr,
                        np.asarray(conf[0], dtype=codes_arr.dtype),
                    )
                    cells = accepted & live[None, :] & conf_mask[None, :]
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
                            "signal_threshold": round6(float(thr_side[i])),
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
