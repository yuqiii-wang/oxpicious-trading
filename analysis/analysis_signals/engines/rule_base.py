"""The signal RULE base class (analysis.analysis_signals.engines.
rule_base) — the redesign's 2026-10-14 unified machinery, consolidated
2026-10-15 into an OOP layout where THIS file is the ABSTRACTION and
the implementation machinery lives in sibling components:

  rule_base.RuleEntry   the ABC every family engine implements —
                        identity, the frame-preparation chain, and the
                        shared self-adaptive CELL LOOP (run_cells)
  evaluation.py         the evaluation contract — CellGate (the plain
                        gate + depth-scaled confidence) and cell_stats
                        (the per-cell measurement); the episode
                        machinery (EpisodeDetector) lives in the
                        consolidated analysis.trends component
  boost.py              the ohlcv boost — the day-0 trust-score
                        collaborator (OhlcvBoost), composed by
                        delegation; a family opts in via OHLCV_BOOST
  analysis.trends       the consolidated TRENDS + EPISODES component
                        (package level): attach_trend (every family's
                        prepared frame) + EpisodeDetector
  signal_families/      the five family IMPLEMENTATIONS (one engine
                        package each)

Every signal family engine is a RuleEntry subclass: it declares its
identity (FAMILY / PACKAGE / SUB_TYPES), implements
``fetch_frame`` (delegating to the surviving fetch/ layer) and ``run``
(building per-(sub_type, action) CANDIDATE trigger masks on the
prepared frame, then calling ``run_cells`` — the shared self-adaptive
cell loop this base owns):

  prepare_frame   fetch + the code's own TREND classification (the
                  SHARED trends module) + the regime join
                  (stats.market_regimes, default calm) + the fixed
                  forward blend (dir_ave axis)
  run_cells       for each candidate: episodes (EpisodeDetector,
                  K-absorb on the code's own traded days, first day =
                  the trigger) → per (code, trend[, regime]) cell
                  measurement (cell_stats) → gate (CellGate) → the
                  per-cell WINNING candidate → the POOLED EVIDENCE
                  TIER (2026-10-16: the (sub_type, action) pooled
                  t-stat + expanding-year consistency — a BUY side
                  whose pooled evidence fails emits NOTHING that run;
                  sell records the evidence ungated, exit timing) →
                  signal_rules rows (rule_params carry the cell's
                  prepared params — SELF-ADAPTIVE per PK) +
                  history_signals rows (the winning cells' triggers;
                  day-0 anchor facts feed the boost collaborator).
                  STARVED cells (episodes but under the sample floor)
                  record as the WAIT population (renamed from
                  no_action 2026-10-16): the extreme-event monotone
                  guarantee (2026-10-16) — a deepest-claim breach
                  never vanishes because its cell is under-floored;
                  history always replays the triggers, the rules
                  watchlist row only for imminent cells (floor-blocked
                  only)

The rule_params therefore DIFFER per PK by construction: the same sub
signal type measures asymmetric populations in different trends (and
regimes, where the family keeps regime splits), so different cells get
different prepared bars/evidence.

Cell axes per family shape:
  trend split + regime split   mov_rsi, bollinger
  trend split, regime pooled   ma_cross (regime_state 4)
  both pooled                  options_wall / options_vix (trend 4 +
                               regime 4 — sparse event populations pool
                               at the gate; per-code stats ride the row)

cudf layout: whole-frame grouped ops only (grouped_shift /
groupby.agg); python loops run ONLY over the structural iterables
(candidates, sub types, the small measured-cell frames, the final row
materialization — the sanctioned host boundary).
"""
from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, time
from typing import Sequence

import numpy as np
import pandas as pd

from _common.df_utils import grouped_shift, host_array, safe_columns
from _common.labels import (
    ACTION_CODE,
    SEC_TYPE_CODE,
    SIDE_CODE,
    SIGNAL_SUB_TYPE_CODE,
    SIGNAL_TYPE_CODE,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.boost import OhlcvBoost
from analysis.analysis_signals.engines.evaluation import (
    CellGate,
    cell_stats,
    pooled_evidence,
)
from analysis.analysis_signals.fetch.regimes import fetch_market_regimes
from analysis.trends import EpisodeDetector, attach_trend

logger = logging.getLogger(__name__)

_DAY_CLOSE = time(15, 0)


@dataclass(frozen=True)
class Candidate:
    """One bar/event variant of a (sub_type, action) rule the engine
    measures per cell — the self-adaptive loop picks the gate-passing
    winner. ``mask`` marks the QUALIFYING trigger-condition days on the
    prepared frame's index (the episode machinery takes the runs' first
    days); ``reference`` is the bar crossed (scalar, or the per-day
    adaptive bar Series on the same index); ``depth`` is the trigger
    day's normalized breach depth (the 0..2 confidence scale);
    ``params`` is the candidate's own rule_params fragment (the bar
    def etc.)."""
    key: str
    mask: pd.Series
    reference: float | pd.Series
    depth: pd.Series
    params: dict = field(default_factory=dict)


class RuleEntry(ABC):
    """The signal rule entry — ONE rule runner. ``rule_entry`` in
    signal_rules stores ``type(self).__name__``; ``rule_params`` stores
    the params this entry prepared for that cell. The implementation
    machinery lives in the sibling components (evaluation.py the
    contract objects, boost.py the trust-score collaborator); this
    class is the family-facing abstraction."""

    # -- identity (every subclass sets these) ---------------------------
    FAMILY: str = ""          # signal_type label ('mov_rsi' | 'bollinger' | 'ma_cross' | 'options_wall' | 'options_vix')
    PACKAGE: str = ""         # dir name under engines/signal_families/
    SUB_TYPES: tuple[str, ...] = ()   # signal_sub_type labels this entry owns
    REGIME_FREE: bool = False         # True → stamp regime 4 (POOLED), no regime split
    USES_TREND_SPLIT: bool = True     # False → sparse families pool, stamp trend 4
    # TREND-AWARE RE-ENTRY (the K-absorb merges only SAME-trend
    # re-entries): a re-entry (>= 2 out-of-band rows) whose trend
    # differs from the trend at the previous breach day starts a FRESH
    # episode — the trend-split cells measure it in the trend it
    # re-entered in, not absorbed into the old trend's population
    TREND_AWARE_REENTRY: bool = False
    # CELL-SPLIT EPISODES (prototype, 2026-10-16): episode detection
    # runs PER (code, cell-context) group instead of once on the full
    # breach mask with first-day cell assignment — a breach day whose
    # (trend[, regime]) differs from the episode's earlier days is a
    # FIRST day of its own cell's episode (the 2023-07-31 class: a
    # bollinger band-walk whose first day landed in a measured-out
    # (rise, calm) cell silently dropped the (rise, quiet) continuation
    # day that carried a LIVE cell). Supersedes TREND_AWARE_REENTRY
    # when both are set; ignored for the pooled families (no split
    # axes). OFF by default until its own A/B graduates it.
    CELL_SPLIT_EPISODES: bool = False
    # The ohlcv boost — DISABLED BY DEFAULT (the merged trust-score
    # pass; every family inherits the machinery off and opts in by
    # flipping this)
    OHLCV_BOOST: bool = False

    def __init__(self, conn, sec_type: str, codes: Sequence[str],
                 boost: bool | None = None):
        self.conn = conn
        self.sec_type = sec_type
        self.codes = list(codes)
        # the boost collaborator: None = the class default (OFF unless
        # the family opted in); the pipeline's --no-boost forces False
        self._boost = OhlcvBoost(
            self.OHLCV_BOOST if boost is None else boost,
        )
        # starved rules-row dedup across this entry's run_cells calls:
        # a PK cell starved on BOTH sides (buy AND sell) emits ONE
        # wait rules row — action collapses the side in the rules
        # PK. First side wins (the engines' buy-before-sell call order —
        # deterministic); the losing side's HISTORY rows still record
        # (history's PK carries no action, and same-axis opposite tails
        # never breach the same day).
        self._starved_rules_pks: set[tuple] = set()

    # -- identity helpers -------------------------------------------------
    @property
    def entry_name(self) -> str:
        return type(self).__name__

    @property
    def signal_type_code(self) -> int:
        return SIGNAL_TYPE_CODE[self.FAMILY]

    @property
    def sec_type_code(self) -> int:
        return SEC_TYPE_CODE[self.sec_type]

    def sub_type_code(self, sub_type: str) -> int:
        return SIGNAL_SUB_TYPE_CODE[sub_type]

    @property
    def cell_keys(self) -> list[str]:
        """The measured cell axes: (code, trend, regime) for the full
        split shape; (code, trend) when regime-free; (code,) for the
        sparse pooled families."""
        if not self.USES_TREND_SPLIT:
            return ["code"]
        return ["code", "trend"] if self.REGIME_FREE \
            else ["code", "trend", "regime"]

    # -- abstract family hooks --------------------------------------------
    @abstractmethod
    async def fetch_frame(self) -> pd.DataFrame:
        """The family's raw input frame via the fetch/ layer: one row per
        (code, date) sorted ASC, carrying at least [code, date, price]
        (float64; datetime64[us]) plus the family's value columns."""

    @abstractmethod
    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        """Build the per-(sub_type, action) candidates on the prepared
        frame and return (signal_rules rows, history_signals rows)."""

    # -- the shared frame preparation --------------------------------------
    async def prepare_frame(self) -> pd.DataFrame:
        df = await self.fetch_frame()
        if df.empty:
            return df
        df = attach_trend(df)
        df = await self._attach_regimes(df)
        df = self._attach_forward(df)
        return df

    async def _attach_regimes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Join stats.market_regimes per (code, date); days with no
        regime row default to calm 0 (a stale build reads as calm).
        The fetch already returns date as datetime64[us] (the DB-read
        convention) — do NOT re-convert: epoch_col_to_dt64 expects
        epoch-SECONDS floats, and feeding it a datetime column
        manufactures far-future dates that match NOTHING (the
        all-calm degenerate split, found + fixed 2026-10-15)."""
        regimes = await fetch_market_regimes(
            self.conn, self.sec_type, since=date(1990, 1, 1),
        )
        if regimes is not None and not regimes.empty:
            df = df.merge(regimes, on=["code", "date"], how="left")
        else:
            df = df.copy()
            df["regime"] = np.nan
        df["regime"] = df["regime"].fillna(0).astype("int8")
        return df

    def _attach_forward(self, df: pd.DataFrame) -> pd.DataFrame:
        """The fixed forward blend dir_ave axis: fwd_h = the close-to-
        close change h traded days after the trigger (delay-0 profile —
        the trigger IS the episode's first day), blended
        0.10/0.15/0.50/0.15/0.10 over (0, 3, 5, 10, 20). ``_blend_ok``
        marks rows whose full 20d future is realized (measurement uses
        only these; the newest triggers still replay into history with
        their cell's prepared confidence)."""
        keys = ["code"]
        blend = pd.Series(0.0, index=df.index, dtype="float64")
        ok = pd.Series(True, index=df.index, dtype="bool")
        for h, w in zip(cfg.FORWARD_BLEND_DAYS, cfg.FORWARD_BLEND_WEIGHTS):
            if h == 0:
                continue
            fwd = grouped_shift(
                df, keys, ["price"], out_names=[f"_fwd{h}"], periods=-h,
            )[f"_fwd{h}"]
            fwd = fwd / df["price"] - 1.0
            df[f"_fwd{h}"] = fwd
            blend = blend + w * fwd.fillna(0.0)
            ok = ok & fwd.notna()
        df["_blend"] = blend
        df["_blend_ok"] = ok
        return df

    # -- the evaluation contract (thin delegates to evaluation.py) --------
    @staticmethod
    def episode_triggers(df: pd.DataFrame, mask: pd.Series, k: int,
                         trend: pd.Series | None = None) -> pd.Series:
        """Episode FIRST days of the breach runs (see
        evaluation.EpisodeDetector — kept as a static delegate for the
        study scripts' call surface)."""
        return EpisodeDetector(k).triggers(df, mask, trend=trend)

    @staticmethod
    def cell_stats(trig: pd.DataFrame, action: str,
                   keys: list[str]) -> pd.DataFrame:
        """Per measured-cell measurement (see evaluation.cell_stats)."""
        return cell_stats(trig, action, keys)

    @staticmethod
    def passes_gate(action: str, n: int, dir_ave: float) -> bool:
        """The plain gate (see evaluation.CellGate)."""
        return CellGate().passes(action, n, dir_ave)

    @staticmethod
    def confidence_pct(base_pct: float, depth_norm: float,
                       factor: float) -> float:
        """Depth-scaled confidence in percent (see evaluation.CellGate)."""
        return CellGate().confidence_pct(base_pct, depth_norm, factor)

    # -- the shared self-adaptive cell loop ------------------------------------
    def run_cells(
        self,
        df: pd.DataFrame,
        sub_type: str,
        action: str,
        candidates: Sequence[Candidate],
        k: int,
        value_col: str,
        *,
        depth_factor: float,
        ungated: bool = False,
        is_typical: pd.Series | None = None,
        extra_params: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Measure every candidate per cell, pick each cell's WINNING
        candidate (the gate-passing one with the best dir_ave; ties →
        the FIRST candidate — the widest bar), run the POOLED EVIDENCE
        TIER, and emit the cell's signal_rules row (rule_params = the
        prepared params + measured evidence) plus history rows for the
        winning cells' episode-first triggers.

        ``ungated`` marks observational populations (the VRP-rejection
        twin): cells emit regardless of the gate (still n >= 1),
        recording the measured evidence as-is (no pooled tier either).

        THE POOLED EVIDENCE TIER (2026-10-16): the per-cell floors
        cannot reach statistical significance (blend sigma ~2.6% ->
        n=3 SE ~1.5%, the 0.25% bar ~0.16 SE), so the BUY edge claim
        lives at the (sub_type, action) POOLED population — all
        winning cells' measurable triggers across codes, judged by
        pooled t-stat + expanding-year sign consistency
        (evaluation.pooled_evidence). A BUY side whose pooled evidence
        FAILS is demoted to measured-out: its trading cells emit no
        rules rows and no history rows that run (the starved wait
        replay is untouched — the monotone guarantee survives the
        pooled tier). SELL records the pooled evidence on its rows but
        is never pooled-gated (exit timing — the equity asymmetry).
        The pooled block rides every winning rules row's
        rule_params.pooled (n, t_stat, hit, the per-horizon decay
        curve, the expanding-year legs).

        STARVED cells also record (2026-10-16, tightened same day): a
        cell whose best candidate holds episodes (n >= 1) but sits
        BELOW the sample floor is a WAIT population — its triggers
        ALWAYS replay into history as wait events (the extreme-event
        monotone guarantee: a deepest-claim breach must never vanish
        because its cell is under-floored; a deeper fluctuation never
        yields LESS record than a shallower one), but its signal_rules
        WAIT row is SELECTIVE — only cells blocked SOLELY by the floor
        (n = floor - 1 with dir_ave already at the side's bar: one
        confirming episode from trading). Populating a rules row for
        every starved cell flooded the panel. The wait row graduates
        to the real action once n clears the floor and the gate passes
        (history re-emission upserts the same PK in place — action is
        not a history PK member). Measured-out cells (n >= floor but
        dir_ave below the bar) still emit NOTHING — that is the gate
        rejecting on evidence, not starvation."""
        action_code = ACTION_CODE[action]
        wait_code = ACTION_CODE["wait"]
        keys = self.cell_keys
        sub_code = self.sub_type_code(sub_type)
        detector = EpisodeDetector(k)
        gate = CellGate()
        # the boost's day-0 axes ride the family frame? (mov_rsi and
        # bollinger fetch them; the options frames do not — the boost
        # then silently stays off for the run)
        if self._boost.enabled and not self._boost.axes_ready:
            self._boost.check_axes(df)

        # the passing winners, plus the STARVED (wait) fallback: a cell
        # with no passing candidate keeps its best under-floored
        # candidate (a passing candidate for the same cell always
        # beats it); its triggers replay into history, its rules row
        # only when imminent (see the emission loop)
        chosen: dict[tuple, tuple[Candidate, dict]] = {}
        starved: dict[tuple, tuple[Candidate, dict]] = {}
        per_cand: list[tuple[Candidate, pd.Series, pd.DataFrame]] = []
        trend_series = df["trend"] if self.TREND_AWARE_REENTRY else None
        # the cell-split context column: the family's own cell axes
        # packed into ONE int group key ((trend, regime) for the full
        # split shape; trend alone when regime-free). Pooled families
        # have no split axes — the flag is a no-op there.
        ctx_series = None
        if self.CELL_SPLIT_EPISODES and self.USES_TREND_SPLIT:
            ctx = df["trend"].astype("int64")
            if not self.REGIME_FREE:
                ctx = ctx * 8 + df["regime"].astype("int64")
            ctx_series = ctx
        for cand in candidates:
            trig_mask = detector.triggers(df, cand.mask,
                                          trend=trend_series,
                                          context=ctx_series)
            per_cand.append((cand, trig_mask, df[trig_mask]))
            trig = per_cand[-1][2]
            if trig.empty:
                continue
            measurable = trig[trig["_blend_ok"].fillna(False)]
            if measurable.empty:
                continue
            for row in cell_stats(measurable, action, keys) \
                    .to_dict("records"):
                cell = tuple(row[kk] for kk in keys)
                n = int(row["n"])
                dir_ave = float(row["dir_ave"])
                passed = (n >= 1) if ungated else gate.passes(
                    action, n, dir_ave,
                )
                if passed:
                    pool = chosen
                elif not ungated and 1 <= n < gate.floors.get(action, 0):
                    pool = starved
                else:
                    continue
                prev = pool.get(cell)
                if prev is None or dir_ave * 100.0 > prev[1]["measure"]["dir_ave_pct"]:
                    std = float(row["dir_std"])
                    t_stat = (dir_ave / (std / math.sqrt(n))
                              if n >= 2 and math.isfinite(std)
                              and std > 1e-12 else None)
                    pool[cell] = (cand, {
                        "measure": {
                            "n_episodes": n,
                            "dir_ave_pct": round(dir_ave * 100.0, 4),
                            # the dispersion pair (2026-10-16): the
                            # per-cell std and its t-stat — honest
                            # small-n evidence alongside the mean bar
                            "dir_std_pct": round(std * 100.0, 4)
                            if math.isfinite(std) else None,
                            "t_stat": round(t_stat, 4)
                            if t_stat is not None else None,
                            "hit_rate": round(float(row["hit"]), 4),
                        },
                    })

        # the emitted winner per cell: a passing candidate overrides the
        # starved fallback of the same cell
        emit = dict(starved)
        emit.update(chosen)
        if not emit:
            return [], []

        rules_rows: list[dict] = []
        rules_chosen: list[bool] = []   # per rules row: a chosen cell?
        for cell, (cand, meas) in emit.items():
            cell_starved = cell not in chosen
            code = str(cell[0])
            trend_code = int(cell[keys.index("trend")]) \
                if "trend" in keys else 4
            regime_code = int(cell[keys.index("regime")]) \
                if "regime" in keys else 4
            if cell_starved:
                # THE SELECTIVE WAIT WATCHLIST (2026-10-16 tightening):
                # a rules row only for cells blocked SOLELY by the
                # sample floor — n = floor - 1 with dir_ave already at
                # the side's bar (one confirming episode from trading).
                # Every other starved cell records through HISTORY
                # replay only (its triggers still emit as wait events
                # below); a rules row per starved cell flooded the
                # panel. Plus one wait row per rules PK (the twin side
                # of the same cell already claimed it — the skipped
                # side's triggers still replay into history).
                m_w = meas["measure"]
                bar_pct = round(cfg.GATE_DIR_AVE_MIN[action] * 100.0, 4) \
                    if action in cfg.GATE_DIR_AVE_MIN else 0.0
                if m_w["n_episodes"] != cfg.SAMPLE_FLOOR.get(action, 0) - 1 \
                        or m_w["dir_ave_pct"] < bar_pct:
                    continue
                pk = (code, self.sec_type_code, self.signal_type_code,
                      sub_code, trend_code, regime_code, wait_code)
                if pk in self._starved_rules_pks:
                    continue
                self._starved_rules_pks.add(pk)
            m = meas["measure"]
            gate_block: dict = {
                "metric": "dir_ave",
                "min_pct": round(
                    cfg.GATE_DIR_AVE_MIN[action] * 100.0, 4)
                if action in cfg.GATE_DIR_AVE_MIN else None,
                "sample_floor": cfg.SAMPLE_FLOOR.get(action),
                "passed": not ungated and not cell_starved,
                "ungated": ungated,
                "starved": cell_starved,
            }
            if cell_starved:
                # the action the cell trades once it graduates past the
                # floor (the row itself records wait)
                gate_block["intended_action"] = action
            params: dict = {
                "axis": f"{self.FAMILY}:{sub_type}",
                "episode_merge_days": k,
                "reentry_trend_split": self.TREND_AWARE_REENTRY,
                "reentry_cell_split": self.CELL_SPLIT_EPISODES,
                "gate": gate_block,
                "measure": m,
                "confidence": {
                    # the base IS the cell's measured dir_ave (pct) —
                    # the same number as the measure block above
                    "base_pct": m["dir_ave_pct"],
                    "depth_factor": depth_factor,
                    "depth_cap": 2.0,
                },
            }
            params.update(cand.params)
            if extra_params:
                params.update(extra_params)
            rules_rows.append({
                "code": code,
                "sec_type": self.sec_type_code,
                "signal_type": self.signal_type_code,
                "signal_sub_type": sub_code,
                "trend": trend_code,
                "regime_state": regime_code,
                # starved cells record observationally — wait is never
                # a trading row (the VRP-twin precedent)
                "action": wait_code if cell_starved else action_code,
                # the per-cell GATE VERDICT (denormalized gate.passed):
                # TRUE only for gate-passed trading cells — negative
                # future returns never validate (measured-out cells do
                # not emit; starved/ungated rows stay observational)
                "is_valid": not ungated and not cell_starved,
                "rule_entry": self.entry_name,
                "rule_params": params,
                # the boost collaborator stamps the passing sides'
                # trust-weighted confidence base in finalize (NULL =
                # the side boosted nothing)
                "ohlcv_boost_conf": None,
            })
            rules_chosen.append(not cell_starved)

        # replay: the winning cells' triggers become history rows,
        # STAGED with their cell verdict — the POOLED EVIDENCE TIER
        # (after the loop) filters before returning
        base_pct = {
            cell: meas["measure"]["dir_ave_pct"]
            for cell, (_, meas) in emit.items()
        }
        history_rows: list[dict] = []
        row_trading: list[bool] = []      # per staged row: chosen cell?
        pooled_facts: list[dict] = []     # the chosen cells' MEASURABLE
                                          # triggers (the pooled tier's
                                          # population)
        staged_anchors: list[dict] = []   # boost facts (dropped when
                                          # the side is pooled-demoted)
        for cand, trig_mask, trig in per_cand:
            if trig.empty:
                continue
            # the triggers whose cell chose THIS candidate
            hit_mask = pd.Series(False, index=trig.index, dtype="bool")
            for cell, (win_cand, _) in emit.items():
                if win_cand is not cand:
                    continue
                m = trig["code"] == cell[0]
                if "trend" in keys:
                    m = m & (trig["trend"] == cell[keys.index("trend")])
                if "regime" in keys:
                    m = m & (trig["regime"] == cell[keys.index("regime")])
                hit_mask = hit_mask | m
            fired = trig[hit_mask.fillna(False)]
            if fired.empty:
                continue
            # positional host boundary: cand.reference/.depth live on the
            # FULL frame index; select with the aligned trigger mask first
            ref_np = host_array(
                cand.reference[trig_mask]
                if isinstance(cand.reference, pd.Series)
                else pd.Series(float(cand.reference), index=trig.index),
            )
            depth_np = host_array(
                cand.depth[trig_mask]
                if isinstance(cand.depth, pd.Series)
                else pd.Series(float(cand.depth), index=trig.index),
            )
            sel_np = host_array(hit_mask.fillna(False)).astype(bool)
            vals_np = host_array(fired[value_col])
            codes_np = host_array(fired["code"])
            trend_np = host_array(fired["trend"]).astype("int64") \
                if "trend" in keys \
                else np.full(len(fired), 4, dtype="int64")
            regime_np = host_array(fired["regime"]).astype("int64") \
                if "regime" in keys \
                else np.full(len(fired), 4, dtype="int64")
            dates_np = host_array(fired["date"]).astype("datetime64[D]")
            py_dates = dates_np.tolist()
            typ_np = host_array(is_typical[trig_mask]) \
                if isinstance(is_typical, pd.Series) else None
            collect = self._boost.enabled and self._boost.axes_ready
            # the pooled tier always needs the blend/ok legs — the
            # boost collect flag no longer gates them
            blend_np = host_array(fired["_blend"])
            ok_np = host_array(fired["_blend_ok"]).astype(bool)
            # the per-horizon legs (the pooled decay curve): host
            # gathers in PERCENT, sign-aligned in the row loop
            fwd_np = {h: host_array(fired[f"_fwd{h}"]) * 100.0
                      for h in cfg.FORWARD_BLEND_DAYS if h > 0}
            sgn = 1.0 if action == "buy" else -1.0
            pat_np = host_array(fired["pattern"]).astype("int64") \
                if collect else None
            vol_np = host_array(fired["vol_state"]).astype("int64") \
                if collect else None
            ps_np = host_array(fired["px_speed"]).astype("int64") \
                if collect else None
            # the turnover-climax grade rides the boost-capable fetches
            # (mov_rsi / bollinger / momentum / ma_cross / rf_break);
            # frames without the column collect a neutral 0 grade
            amt_np = host_array(fired["amt_climax"]).astype("int64") \
                if collect and "amt_climax" in safe_columns(fired) \
                else None
            side_code = np.int16(
                SIDE_CODE["top"] if action == "sell"
                else SIDE_CODE["bottom"],
            )
            years_np = dates_np.astype("datetime64[Y]").astype("int64") \
                + 1970
            for i in range(len(fired)):
                code = str(codes_np[i])
                cell = (code,)
                if "trend" in keys:
                    cell = cell + (int(trend_np[i]),)
                if "regime" in keys:
                    cell = cell + (int(regime_np[i]),)
                if cell not in base_pct:
                    continue
                history_rows.append({
                    "code": code,
                    "sec_type": self.sec_type_code,
                    "signal_type": self.signal_type_code,
                    "signal_sub_type": sub_code,
                    "is_ohlcv_boosted": False,
                    "date": py_dates[i],
                    "time": _DAY_CLOSE,
                    # starved cells' triggers record as wait events;
                    # graduation re-emits the same PK as the real
                    # action (action is no history PK member)
                    "action": wait_code if cell not in chosen
                    else action_code,
                    "signal": round(float(vals_np[i]), 6),
                    "signal_reference": round(float(ref_np[sel_np][i]), 6),
                    # the record column stays on the INTEGER
                    # basis-point scale (×100 the pct confidence)
                    "confidence": round(
                        gate.confidence_pct(
                            base_pct[cell], float(depth_np[sel_np][i]),
                            depth_factor,
                        ) * 100.0,
                    ),
                    "is_day_close_trigger": True,
                    "regime_state": int(regime_np[i]),
                    "is_typical": None if typ_np is None
                    else (None if bool(np.isnan(typ_np[sel_np][i]))
                          else bool(typ_np[sel_np][i])),
                })
                row_trading.append(cell in chosen)
                if cell in chosen:
                    if bool(ok_np[i]):
                        pooled_facts.append({
                            "year": int(years_np[i]),
                            # sign-aligned favorable response, PERCENT
                            "fav": float(blend_np[i]) * 100.0 * sgn,
                            **{f"f{h}": float(fwd_np[h][i]) * sgn
                               for h in cfg.FORWARD_BLEND_DAYS
                               if h > 0},
                        })
                    if collect:
                        # observational (starved) rows never feed the
                        # boost derivation — the measured population of
                        # record is the passing cells' triggers; a
                        # pooled-demoted side's anchors are dropped too
                        staged_anchors.append({
                            "code": code,
                            "sub_type": sub_type,
                            # the rules-row key of the cell this trigger
                            # measured (finalize stamps ohlcv_boost_conf
                            # back onto the matching rule row)
                            "sub_code": sub_code,
                            "trend": int(cell[1]) if "trend" in keys
                            else 4,
                            "regime": int(cell[2]) if "regime" in keys
                            else 4,
                            # measurable = the full 20d future realized
                            # (the 0-padded newest rows never enter the
                            # per-cell boost aggregation)
                            "ok": bool(ok_np[i]),
                            "side": side_code,
                            # the side-aligned depth past the bar
                            # (positive = deeper into the tail; bottom
                            # breaches read value < bar, so the flip)
                            "d": float(vals_np[i] - ref_np[sel_np][i])
                            * (-1.0 if side_code == SIDE_CODE["bottom"]
                               else 1.0),
                            # the sign-aligned favorable response
                            # (bottom: +blend; top: -blend —
                            # cell_stats's convention)
                            "y_bp": float(blend_np[i]) * 100.0
                            * (1.0 if side_code == SIDE_CODE["bottom"]
                               else -1.0),
                            "year": int(years_np[i]),
                            "pattern": int(pat_np[i]),
                            "vol_state": int(vol_np[i]),
                            "px_speed": int(ps_np[i]),
                            "amt": int(amt_np[i]) if amt_np is not None
                            else 0,
                        })
        # THE POOLED EVIDENCE TIER (2026-10-16) — the two-tier gate's
        # statistical layer: the (sub_type, action) pooled population
        # of the winning cells' measurable triggers. A BUY failure
        # demotes the side to measured-out (its trading rules rows and
        # history replay are dropped — the starved wait rows survive,
        # preserving the monotone guarantee); SELL records the evidence
        # ungated (exit timing — the equity asymmetry). The demoted
        # side's boost anchors never feed the derivation.
        pooled: dict | None = None
        if chosen and not ungated:
            pooled = pooled_evidence(pooled_facts)
        pooled_demoted = bool(pooled is not None and action == "buy"
                              and not pooled["passed"])
        if pooled_demoted:
            history_rows = [r for r, t in zip(history_rows, row_trading)
                            if not t]
            rules_rows = [r for r, t in zip(rules_rows, rules_chosen)
                          if not t]
            logger.warning(
                f"[{self.FAMILY}] {sub_type}/{action}: POOLED evidence "
                f"REJECTED — {len(chosen)} winning cell(s) emit nothing "
                f"(pooled n={pooled['n']} "
                f"dir_ave={pooled['dir_ave_pct']:+.2f}% "
                f"t={pooled['t_stat']} years_positive="
                f"{pooled['years']['positive']})",
            )
        elif staged_anchors:
            self._boost.facts.extend(staged_anchors)
        # stamp the pooled block on the TRADING rows (the demoted
        # branch already filtered rules_rows; rules_chosen still refers
        # to the pre-filter alignment, so never stamp under demotion)
        if pooled is not None and not pooled_demoted:
            for row, trading in zip(rules_rows, rules_chosen):
                if trading:
                    row["rule_params"]["pooled"] = {
                        **pooled, "gating": action == "buy",
                    }
        return rules_rows, history_rows

    # -- the ohlcv boost (delegated to the OhlcvBoost collaborator) --------
    @property
    def boost_enabled(self) -> bool:
        """The boost toggle (the class default; the pipeline's
        --no-boost forces False)."""
        return self._boost.enabled

    def finalize_boost(self, rules_rows: list[dict],
                       history_rows: list[dict]) \
            -> tuple[list[dict], list[dict], dict]:
        """Derive the boost coefficients SELF-ADAPTIVELY from this run's
        own anchor population and stamp the per-cell trust-weighted
        confidence base onto the rule rows (see boost.OhlcvBoost.finalize
        — the collaborator owns the machinery; history rows are returned
        untouched, the twins era is over)."""
        return self._boost.finalize(rules_rows, history_rows)
