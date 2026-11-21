"""Ad-hoc action module — the strategy's SELF-PROPOSED buy/sell actions.

First action (user-directed 2026-11-18): the position-aware, profit-aware
TRIM. When multiple buys have accumulated the position above a
profit-dependent cap, and the price shows a SMALL RISE that has
stabilized, sell a fraction to bring the position back under the cap —
sell into strength, never into a dip. A book at a loss or with only
little gain keeps a LOW cap; only a healthy-profit book may run high
(scaling by STRATEGY PERFORMANCE — the Part 6 U4 lesson; the Part 2/3
position-STATE overlay failed because its buy encouragement added
exposure in falling markets; this module never buys).

2-pass protocol (driven by the collector, which owns the engine — the
same shape as the equity guard):

1. pass A runs the engine on the FINAL blended + dust-tuned frame
   (stub reasons — pass-A decisions feed the plan only);
2. :class:`AdhocActionPlanner` walks each code's pass-A daily rows
   chronologically with a SHADOW book (shadow qty / shadow PnL updated
   by every planned trim, so consecutive trims cannot over-trim) and
   emits :class:`TrimAction` rows for bars where NO family signal
   fired;
3. :func:`inject_trim_actions` writes the planned negative
   ``signal_confidence`` (a partial SELL — the engine sells
   ``conf/100`` of the position) onto the zero-signal bars and tags
   the row with an ``adhoc_payload`` column;
4. the collector's final engine run (with :func:`wrap_reason_fn`)
   produces the returned decisions, reason ``__ADHOC__<json>__<human>``.

Approximation (documented): the shadow models only the TRIM's effect on
the path — family BUY/SELL decisions are unchanged between the passes
(buys are untouched by a SELL-only injection), and family SELLs sell a
fraction of a slightly different absolute position in the final pass.
Same genre as the equity guard's 2-pass.

cudf layout: trailing features are computed per-code on a dedup'd
(code, date) frame in whole-block pandas (the VolStateScaling shape);
the sequential shadow walk is a host-array boundary core (the engine's
own genre — a state machine has no vectorized form); the injection is
a small plan frame built FROM the big frame's own (code, date) columns
and merged back (the documented ctor-accident caution), then an
elementwise ``where``-chain. This module never imports the engine.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from strategy.factors_and_algos._comb.config import AdhocActionsCfg
from strategy.factors_and_algos._comb.data_definitions import TrimAction

logger = logging.getLogger(__name__)

#: Column the reason wrapper reads (JSON payload of the planned action).
ADHOC_PAYLOAD_COL = "adhoc_payload"


class AdhocActionPlanner:
    """Plans self-proposed trims from a pass-A engine run.

    One instance per plan (stateless beyond the config/thresholds); the
    caller supplies the pass-A artifacts per code (daily rows + decisions,
    both keyed by trade/exec date).
    """

    def __init__(
        self,
        cfg: AdhocActionsCfg,
        conf_threshold: float,
        min_holding_period: int,
    ) -> None:
        self.cfg = cfg
        self.conf_threshold = float(conf_threshold)
        self.min_hp = int(min_holding_period)

    # ------------------------------------------------------------------
    def build_plan(
        self,
        df: pd.DataFrame,
        daily_rows_by_code: Dict[str, List[Dict[str, Any]]],
        decisions_by_code: Dict[str, List[Dict[str, Any]]],
    ) -> List[TrimAction]:
        """Walk every code's pass-A path and emit the trim plan."""
        plan: List[TrimAction] = []
        for code, block in self._feature_blocks(df):
            actions = self._walk_code(
                code, block,
                daily_rows_by_code.get(code) or [],
                decisions_by_code.get(code) or [],
            )
            if actions:
                logger.info("[adhoc] %s: planned %d trim(s)", code, len(actions))
                plan.extend(actions)
        return plan

    # ------------------------------------------------------------------
    def _feature_blocks(
        self, df: pd.DataFrame,
    ) -> List[Tuple[str, Dict[str, np.ndarray]]]:
        """Per-code host-array blocks carrying the trailing features.

        The dedup frame is built from the big frame's OWN (code, date)
        columns (the cudf ctor-accident caution — never from python-date
        lists); features are whole-block pandas per code (the
        VolStateScaling shape); each block unwraps ONCE via ``host_array``.
        """
        du = (df[["code", "date", "close_price", "signal_confidence"]]
              .drop_duplicates(subset=["code", "date"])
              .sort_values(["code", "date"]).reset_index(drop=True))
        rise_w = self.cfg.rise_window_rows
        vol_w = self.cfg.vol_window_rows
        ref_w = self.cfg.vol_ref_window_rows
        blocks: List[Tuple[str, Dict[str, np.ndarray]]] = []
        for code, block in du.groupby("code", sort=False):
            cl = block["close_price"].reset_index(drop=True)
            ret1 = cl.pct_change()
            vol = ret1.rolling(vol_w, min_periods=vol_w).std()
            feat = {
                "date": host_array(block["date"]),
                "close": host_array(cl).astype(np.float64),
                "sig": host_array(
                    block["signal_confidence"].reset_index(drop=True)
                    .fillna(0.0)).astype(np.float64),
                "r5": host_array(
                    cl.pct_change(periods=rise_w)).astype(np.float64),
                "sma": host_array(
                    cl.rolling(rise_w, min_periods=rise_w).mean()
                ).astype(np.float64),
                "vol": host_array(vol).astype(np.float64),
                "vol_med": host_array(
                    vol.rolling(ref_w, min_periods=ref_w).median()
                ).astype(np.float64),
            }
            blocks.append((str(code), feat))
        return blocks

    # ------------------------------------------------------------------
    def _cap_for(self, pnl_ratio: float) -> float:
        """The profit-aware position cap (lots)."""
        if pnl_ratio >= self.cfg.healthy_profit_bar:
            return self.cfg.healthy_cap_lots
        return self.cfg.low_cap_lots

    def _walk_code(
        self,
        code: str,
        feat: Dict[str, np.ndarray],
        daily_rows: Sequence[Dict[str, Any]],
        decisions: Sequence[Dict[str, Any]],
    ) -> List[TrimAction]:
        """Sequential shadow walk over one code's pass-A path.

        Host-array boundary core (a state machine — the engine's genre).
        Shadow state: ``planned_removed`` (qty units sold by planned
        trims) and ``planned_realized`` (their PnL at the bar's
        normalized close) — the shadow qty/PnL at bar ``i`` is the
        pass-A daily-row value minus/plus these, so consecutive trims
        size against the ALREADY-trimmed book.
        """
        daily = {row["trade_date"]: row for row in daily_rows}
        dec_by_date = {d["exec_date"]: d for d in decisions}
        dates = feat["date"]
        n = len(dates)
        actions: List[TrimAction] = []

        planned_removed = 0.0
        planned_realized = 0.0
        last_buy_pos: Optional[int] = None
        cooldown_until = -1

        for i in range(n):
            day = dates[i]
            dec = dec_by_date.get(day)
            if dec is not None and dec.get("side") == "BUY":
                last_buy_pos = i
            row = daily.get(day)
            if row is None:
                continue  # before the first BUY / invalid-close bars
            # The family owns its own bars — ad hoc fills silence only.
            if feat["sig"][i] != 0.0:
                continue
            if last_buy_pos is None or i - last_buy_pos < self.min_hp:
                continue
            if i < cooldown_until:
                continue

            qty1 = float(row.get("total_qty") or 0.0)
            shadow_qty = qty1 - planned_removed
            if shadow_qty <= 0.0:
                continue
            lots = shadow_qty / 100.0
            basis = float(row.get("cost_basis_norm") or 0.0)
            capital = lots * basis
            pnl_ratio = (
                (float(row.get("total_pnl") or 0.0) + planned_realized) / capital
                if capital > 1e-9 else 0.0
            )
            cap = self._cap_for(pnl_ratio)
            if lots <= cap:
                continue

            # Small rise, once stable: positive but bounded recent
            # return, price holding above its short mean, and vol no
            # higher than its own trailing norm (NaN comparisons are
            # False — warmup bars never fire).
            r5 = float(feat["r5"][i])
            if not (0.0 < r5 <= self.cfg.rise_max):
                continue
            if not (float(feat["close"][i]) >= float(feat["sma"][i])):
                continue
            if not (float(feat["vol"][i]) <= float(feat["vol_med"][i])):
                continue

            frac = min((lots - cap) / lots, self.cfg.max_trim_frac)
            conf = frac * 100.0
            if conf < self.conf_threshold:
                continue  # dust — below the blend gate; natural dead-band

            actions.append(TrimAction(
                code=code, trade_date=day, conf=conf, lots=lots, cap=cap,
                pnl_ratio=pnl_ratio, r5=r5, sell_frac=frac,
            ))
            sold = shadow_qty * frac
            planned_removed += sold
            planned_realized += (sold / 100.0) * (
                float(row.get("normalized_close") or 0.0) - basis)
            cooldown_until = i + self.cfg.cooldown_rows
        return actions


# ----------------------------------------------------------------------
# Injection + reason contract
# ----------------------------------------------------------------------
def inject_trim_actions(
    df: pd.DataFrame, plan: List[TrimAction],
) -> pd.DataFrame:
    """Write the planned trims onto the frame as negative
    ``signal_confidence`` (partial SELLs) on ZERO-signal bars only —
    family signals are never overridden. The ``adhoc_payload`` column
    (JSON, one string per planned bar) stays on the frame for
    :func:`wrap_reason_fn`; the caller drops it after the engine run.
    """
    if not plan:
        return df
    plan_map = {(a.code, a.trade_date): a for a in plan}
    # Lookup frame built FROM the big frame's own (code, date) columns
    # (the documented cudf ctor accident — never from python-date lists).
    du = df[["code", "date"]].drop_duplicates().reset_index(drop=True)
    codes_host = host_array(du["code"])
    dates_host = host_array(du["date"])
    conf = np.zeros(len(du), dtype=np.float64)
    payload = np.full(len(du), None, dtype=object)
    for i in range(len(du)):
        day = dates_host[i]
        day = day.item() if hasattr(day, "item") else day
        action = plan_map.get((str(codes_host[i]), day))
        if action is not None:
            conf[i] = action.conf
            payload[i] = json.dumps(action.payload(), ensure_ascii=False)
    du = du.assign(adhoc_conf=conf, adhoc_payload=payload)
    df = df.merge(du, on=["code", "date"], how="left")
    sig = df["signal_confidence"].fillna(0.0)
    fire = (df["adhoc_conf"].fillna(0.0) > 0.0) & (sig == 0.0)
    df["signal_confidence"] = sig.mask(
        fire, -df["adhoc_conf"].fillna(0.0))
    df["signal_value"] = df["signal_confidence"].abs()
    return df.drop(columns=["adhoc_conf"])


def wrap_reason_fn(
    fn: Callable[[Any, str, dict, float], str],
) -> Callable[[Any, str, dict, float], str]:
    """Wrap a ``signal_reason_fn`` so planned ad-hoc bars render the
    ``__ADHOC__<json>__<human>`` reason instead of the (all-zero)
    mixed-mode breakdown; every other bar delegates unchanged."""
    def wrapped(row, side: str, params: dict, confidence: float) -> str:
        raw = row.get(ADHOC_PAYLOAD_COL) if hasattr(row, "get") else None
        if raw:
            try:
                data = json.loads(str(raw))
            except (TypeError, ValueError):
                data = None
            if data:
                human = (
                    f"ADHOC TRIM: sell {confidence:.0f}% of position "
                    f"(lots {data.get('lots')} > cap {data.get('cap')}, "
                    f"pnl {float(data.get('pnl', 0.0)):+.1%}, "
                    f"r5 {float(data.get('r5', 0.0)):+.1%})"
                )
                return (f"__ADHOC__{json.dumps(data, ensure_ascii=False)}__"
                        f"{human}")
        return fn(row, side, params, confidence)
    return wrapped


__all__ = [
    "ADHOC_PAYLOAD_COL",
    "AdhocActionPlanner",
    "inject_trim_actions",
    "wrap_reason_fn",
]
