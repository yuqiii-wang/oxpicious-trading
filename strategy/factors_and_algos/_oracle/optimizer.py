"""The ORACLE's own optimization engine (2026-11-20, user-directed
"oracle optimization engine is totally isolated from Omega calmar,
just confidence scaling").

Sweeps the replay's confidence-scaling grid (scaling.py — curve power
/ grade floor / null size / exit mode) against DIRECT profit/risk
metrics and reports the champion. DELIBERATELY ISOLATED:

  - NO ``_optm_engine`` imports — not the Omega/Calmar regime losses,
    not the Optuna machinery, not the trainer's spaces. The objective
    is the replay's own economics: total return and the pooled equity
    drawdown, computed here from the same definitions
    (total_pnl / peak capital; max peak-to-trough of the cross-code
    daily equity path, peak starting at 0).
  - NO persistence — in-memory, DB read-only; the matrix lands in
    ``temp_scripts/oracle_conf_scale_results.csv`` and the champion is
    pinned as ``DEFAULT_PARAMS["conf_scale"]`` in algo.py by hand
    (the champion-in-code convention, never a trainer upsert).
  - NO live-path authority — the optimizer does not change what a run
    does; it measures. What a run does is whatever params the run
    carries.

Entry: ``python -m strategy.factors_and_algos._oracle`` (see
``__main__.py``; the 10 study indices, index scope, by default).

Selection rule ("max profits, min risks", fixed and explicit):
champion = max FULL total_return among configs whose FULL max_dd is
within DD_TOL of the identity replay's (risk never materially worse
than the raw-grade baseline); the min-DD champion and the full ranked
matrix are reported alongside. The whole oracle is a HINDSIGHT
reference — IS/OOS columns are reported as stability context (the
recent-regime reading), not as an out-of-sample claim.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd

from strategy.factors_and_algos._oracle.algo import OracleSignalAlgo
from strategy.factors_and_algos._oracle.scaling import ConfidenceScaleCfg

__all__ = ["OracleScaleOptimizer", "build_scale_grid", "SegmentMetrics"]


#: The exhaustive grid (no search — the space is tiny and the engine
#: run is cheap; a sampler would only add variance).
GRID_POWERS = (0.5, 0.75, 1.0, 1.5, 2.0)
GRID_FLOORS = (0.0, 15.0, 30.0)
GRID_NULLS = (60.0, 100.0)
GRID_EXITS = ("swing", "full")

#: Statics mirrored from the study harness (temp_scripts/
#: sig_comb_study.run_segment): the replay's engine assumptions. The
#: fidelity flags pin min_holding_period to 1 inside run_backtest; a
#: conf_threshold would be inert (DUST_FILTER False).
ENGINE_STATICS = {
    "buy_notional": 100_000.0,
    "skip_final_liquidation": False,
    "fault_tolerance": 0,
    "ft_annotate": False,
}

#: Segment split — the trainer's positional per-code 80/20 convention,
#: implemented locally (no _optm_engine import; thin segments fall
#: back to the full series, same rule).
OOS_FRAC = 0.2
MIN_SEG_ROWS = 60

#: Champion DD tolerance (fraction of peak capital) vs the identity
#: replay — "risk never materially worse than the raw-grade baseline".
DD_TOL = 0.005


def build_scale_grid() -> List[ConfidenceScaleCfg]:
    """The scaling grid as cfg objects (identity config included)."""
    return [
        ConfidenceScaleCfg(
            grade_power=p, grade_floor=f, null_conf=n, exit_mode=e,
        )
        for p in GRID_POWERS
        for f in GRID_FLOORS
        for n in GRID_NULLS
        for e in GRID_EXITS
    ]


#: The creative PNL grid (2026-11-20, user-directed "be creative, push
#: realized pnl to 700+"): side-split scaling — concave-boosted buys
#: with lifted buy floors (a bigger deployed book), sells kept
#: grade-proportional or full. Ranked by ABSOLUTE realized PnL (not
#: return-%): bigger books raise PnL at constant efficiency, so the
#: capital and dd columns ride alongside for the honest reading.
PNL_BUY_POWERS = (0.25, 0.5, 0.75, 1.0)
PNL_BUY_FLOORS = (0.0, 30.0, 60.0, 100.0)
#: sell forms: (sell_power, sell_floor) for swing, or full exits.
#: Convex sell powers (> 1) shrink weak tops' exit fractions — the
#: book persists through conf-13 wiggles and realizes at strong tops.
PNL_SELL_FORMS = (
    ("swing", 0.5, 0.0),
    ("swing", 0.5, 60.0),
    ("swing", 1.0, 0.0),
    ("swing", 1.0, 60.0),
    ("swing", 1.5, 0.0),
    ("swing", 2.0, 0.0),
    ("swing", 3.0, 0.0),
    ("swing", 4.0, 0.0),
    ("full", None, None),
)


def build_pnl_grid() -> List[ConfidenceScaleCfg]:
    """The pnl grid, led by the two reference cells: NAIVE (every
    event at full size ±100 — the pre-grade flat replay) and IDENTITY
    (the raw stored grade)."""
    cells: List[ConfidenceScaleCfg] = [
        ConfidenceScaleCfg(
            buy_floor=100.0, sell_floor=100.0, exit_mode="swing",
        ),  # NAIVE: buys 100, sells exit 100% of the book
        ConfidenceScaleCfg(),  # IDENTITY: the raw grade
    ]
    for bp in PNL_BUY_POWERS:
        for bf in PNL_BUY_FLOORS:
            # buy_floor 100 lifts every buy to 100 — buy_power is then
            # inert; keep ONE power at that floor (no duplicates)
            if bf >= 100.0 and bp != PNL_BUY_POWERS[0]:
                continue
            for ex, sp, sf in PNL_SELL_FORMS:
                cells.append(ConfidenceScaleCfg(
                    buy_power=bp,
                    buy_floor=bf,
                    sell_power=sp,
                    sell_floor=sf,
                    exit_mode=ex,
                ))
    return cells


@dataclass
class SegmentMetrics:
    """Direct profit/risk metrics for one segment (one config's run).

    Definitions mirror the repo's pooled conventions exactly:
    ``total_return`` = Σ realized PnL / Σ peak capital deployed;
    ``max_dd`` = max peak-to-trough of the Σ-per-date cross-code daily
    equity path (running peak starts at 0), as a fraction of the same
    capital base. ``total_pnl`` / ``total_capital`` carry the ABSOLUTE
    units too — realized PnL and return-% are DIFFERENT objectives
    (bigger deployed books raise PnL at constant %; the pnl grid ranks
    by the absolute, the scale grid by the ratio).
    """

    total_return: float = 0.0
    total_pnl: float = 0.0
    total_capital: float = 0.0
    max_dd: float = 0.0
    ret_over_dd: float = 0.0
    n_decisions: int = 0
    n_trades: int = 0
    n_codes_positive: int = 0
    n_codes: int = 0
    per_code: Dict[str, float] = field(default_factory=dict)


class OracleScaleOptimizer:
    """Fetch once, replay the grid, rank by direct metrics."""

    def __init__(self, sec_type: str, codes: List[str]) -> None:
        self.sec_type = sec_type
        self.codes = list(codes)
        self.algo = OracleSignalAlgo()

    # ------------------------------------------------------------------
    # Fetch (the production seam — the algo's own SQL)
    # ------------------------------------------------------------------
    async def fetch_frames(self) -> Dict[str, pd.DataFrame]:
        from _common.db_commons import get_db_connection_async

        conn = await get_db_connection_async()
        try:
            df = await self.algo.fetch_signal_data(
                conn, self.sec_type, self.codes,
            )
        finally:
            await conn.close()
        return {
            code: g.sort_values("date").reset_index(drop=True)
            for code, g in df.groupby("code", sort=False)
        }

    # ------------------------------------------------------------------
    # One config × one segment -> metrics
    # ------------------------------------------------------------------
    def run_segment(
        self, frames: Dict[str, pd.DataFrame], cfg: ConfidenceScaleCfg,
    ) -> SegmentMetrics:
        """One config × one segment, through the PRODUCTION replay
        seam (``algo.run_backtest`` — the fidelity flags pin
        min_holding_period 1 and skip the dust filter / delays inside;
        FT annotation off via the statics)."""
        from strategy._trading.engine import (
            compute_daily_rows,
            compute_total_buy_cost,
        )

        # No top-level exit_mode key: from_params would give it
        # precedence over the group, and the grid varies the group.
        params = {**ENGINE_STATICS, "conf_scale": cfg.model_dump()}

        per_code: Dict[str, dict] = {}
        for code, df in frames.items():
            decisions = sorted(
                self.algo.run_backtest(df, params, self.sec_type, [code]),
                key=lambda d: d["exec_date"],
            )
            pnl = sum(
                float(d.get("realized_pnl") or 0.0)
                for d in decisions if d["side"] == "SELL"
            )
            first_buy = next(
                (d for d in decisions if d["side"] == "BUY"), None,
            )
            daily_rows: List[dict] = []
            if first_buy is not None:
                daily_rows = compute_daily_rows(
                    df, decisions, float(first_buy["fill_price"]),
                )
            per_code[code] = {
                "decisions": decisions,
                "daily_rows": daily_rows,
                "pnl": pnl,
                "capital": compute_total_buy_cost(decisions),
            }
        return self._metrics(per_code)

    def _metrics(self, per_code: Dict[str, dict]) -> SegmentMetrics:
        """Pool per-code outputs into the direct profit/risk metrics."""
        by_date: Dict[object, float] = {}
        total_pnl = 0.0
        total_capital = 0.0
        n_decisions = 0
        n_trades = 0
        rets: Dict[str, float] = {}
        for code, res in per_code.items():
            total_pnl += res["pnl"]
            total_capital += res["capital"]
            n_decisions += len(res["decisions"])
            n_trades += sum(
                1 for d in res["decisions"] if d["side"] == "SELL"
            )
            if res["capital"] > 0.0:
                rets[code] = res["pnl"] / res["capital"]
            for row in res["daily_rows"]:
                d = row.get("trade_date")
                if d is None:
                    continue
                by_date[d] = (
                    by_date.get(d, 0.0)
                    + float(row.get("total_pnl") or 0.0)
                )
        # max peak-to-trough of the pooled equity path (peak >= 0)
        peak = 0.0
        max_dd_abs = 0.0
        for d in sorted(by_date):
            v = by_date[d]
            if v > peak:
                peak = v
            if peak - v > max_dd_abs:
                max_dd_abs = peak - v

        m = SegmentMetrics()
        m.n_decisions = n_decisions
        m.n_trades = n_trades
        m.n_codes = len(per_code)
        m.n_codes_positive = sum(1 for r in rets.values() if r > 0.0)
        m.per_code = rets
        m.total_pnl = total_pnl
        m.total_capital = total_capital
        if total_capital > 0.0:
            m.total_return = total_pnl / total_capital
            m.max_dd = max_dd_abs / total_capital
            m.ret_over_dd = (
                m.total_return / m.max_dd if m.max_dd > 0.0 else 0.0
            )
        return m

    # ------------------------------------------------------------------
    # The sweep
    # ------------------------------------------------------------------
    def split_segments(
        self, frames: Dict[str, pd.DataFrame],
    ) -> tuple:
        """Positional per-code 80/20 (thin codes fall back to full)."""
        is_d: Dict[str, pd.DataFrame] = {}
        oos_d: Dict[str, pd.DataFrame] = {}
        for code, df in frames.items():
            n = len(df)
            cut = int(round(n * (1.0 - OOS_FRAC)))
            if cut < MIN_SEG_ROWS or (n - cut) < MIN_SEG_ROWS:
                is_d[code] = df
                oos_d[code] = df
                continue
            is_d[code] = df.iloc[:cut].copy()
            oos_d[code] = df.iloc[cut:].copy()
        return is_d, oos_d

    def run(
        self, grid: List[ConfidenceScaleCfg] | None = None,
    ) -> List[dict]:
        """Sweep the grid; return the matrix rows (ranked by the
        caller). Also asserts the identity guard: the identity config
        and a params-absent run must agree decision-for-decision."""
        import asyncio

        grid = grid or build_scale_grid()
        frames = asyncio.run(self.fetch_frames())
        n_pivots = sum(
            int((g["oracle_action"].fillna(-1) >= 0).sum())
            for g in frames.values()
        )
        print(
            f"codes={len(frames)} pivot rows={n_pivots} "
            f"grid={len(grid)}",
            flush=True,
        )
        self._identity_guard(frames)

        is_d, oos_d = self.split_segments(frames)
        rows: List[dict] = []
        t0 = time.time()
        for i, cfg in enumerate(grid, start=1):
            full = self.run_segment(frames, cfg)
            is_m = self.run_segment(is_d, cfg)
            oos_m = self.run_segment(oos_d, cfg)
            row = {
                "grade_power": cfg.grade_power,
                "buy_power": cfg.buy_power,
                "sell_power": cfg.sell_power,
                "grade_floor": cfg.grade_floor,
                "buy_floor": cfg.buy_floor,
                "sell_floor": cfg.sell_floor,
                "null_conf": cfg.null_conf,
                "exit_mode": cfg.exit_mode,
                "identity": cfg.is_identity,
                "full_ret": full.total_return,
                "full_pnl": full.total_pnl,
                "full_capital": full.total_capital,
                "full_dd": full.max_dd,
                "full_rodd": full.ret_over_dd,
                "full_trades": full.n_trades,
                "full_codes": full.n_codes,
                "full_codes_pos": full.n_codes_positive,
                "is_ret": is_m.total_return,
                "is_dd": is_m.max_dd,
                "oos_ret": oos_m.total_return,
                "oos_dd": oos_m.max_dd,
                "oos_rodd": oos_m.ret_over_dd,
                "oos_codes_pos": oos_m.n_codes_positive,
            }
            rows.append(row)
            print(
                f"[{i:>2}/{len(grid)}] bp={row['buy_power'] or row['grade_power']:<4} "
                f"bf={row['buy_floor'] or 0:<4} sp={row['sell_power'] or row['grade_power']:<4} "
                f"sf={row['sell_floor'] or 0:<4} ex={cfg.exit_mode:<5} "
                f"FULL pnl={row['full_pnl']:+8.1f} ret={row['full_ret']:+.1%} "
                f"dd={row['full_dd']:.1%} | OOS ret={row['oos_ret']:+.1%} "
                f"| trades={row['full_trades']}",
                flush=True,
            )
        print(f"sweep done in {time.time() - t0:.0f}s", flush=True)
        return rows

    # ------------------------------------------------------------------
    # Identity guard — the bit-identity A/B bar
    # ------------------------------------------------------------------
    def _identity_guard(
        self, frames: Dict[str, pd.DataFrame],
    ) -> None:
        """The identity cfg and a params-absent replay must produce
        IDENTICAL decisions (dates, sides, qty) — the refactor's
        no-behavior-change proof on real frames."""
        plain = {
            **ENGINE_STATICS,
            # no conf_scale key at all -> from_params identity
        }
        ident = {
            **ENGINE_STATICS,
            "conf_scale": ConfidenceScaleCfg().model_dump(),
        }
        for code, df in frames.items():
            a = self.algo.run_backtest(df, plain, self.sec_type, [code])
            b = self.algo.run_backtest(df, ident, self.sec_type, [code])
            ka = [(d["exec_date"], d["side"], round(float(d["qty"]), 6))
                  for d in a]
            kb = [(d["exec_date"], d["side"], round(float(d["qty"]), 6))
                  for d in b]
            if ka != kb:
                raise AssertionError(
                    f"identity guard FAILED on {code}: "
                    f"{len(ka)} vs {len(kb)} decisions differ"
                )
        print("identity guard: PASS (params-absent == identity cfg)",
              flush=True)
