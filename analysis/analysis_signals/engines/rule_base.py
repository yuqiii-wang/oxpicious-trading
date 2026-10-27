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
  _ohlcv_boost/         the ohlcv boost — the day-0 trust-score
                        collaborator (OhlcvBoost, measurement.py),
                        composed by delegation; a family opts in via
                        OHLCV_BOOST — plus the shared VARIANT-GATE
                        stack (_variant_base.VariantGate + the
                        _pe_boost / _ohlcv_boost axis packages): the
                        boosted-variant populations the engines run
                        beside their plain cells
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
                  CONSTANT rule only — SELF-ADAPTIVE per PK — plus the
                  measured half on the row's _performance payload (the
                  cell's OWN measure block; slimmed 2026-10-20), peeled
                  by run_family into
                  analysis_signals.history_signal_performance as the
                  run's dated snapshot; the 2026-10-19 split) +
                  history_signals rows (the winning cells' triggers;
                  day-0 anchor facts feed the boost collaborator).
                  STARVED cells (under the sample floor) TRADE since
                  2026-10-19 (user-directed): the floor no longer
                  blocks — the dir_ave bar is the only verdict, and a
                  thin cell's confidence is scaled by its sample
                  completeness min(n / floor, 1) (the starvation wait
                  logic retired; the extreme-event record is now a
                  REDUCED-CONFIDENCE trading row, never a wait row —
                  the only remaining wait rows are the ungated
                  observational twins, options_wall's VRP-fail `_rej`)

The rule_params therefore DIFFER per PK by construction: the same sub
signal type measures asymmetric populations in different trends (and
regimes, where the family keeps regime splits), so different cells get
different prepared bars. The MEASURED evidence (the cell's own measure
block) is NOT rule_params — it is the dated per-run snapshot of
analysis_signals.history_signal_performance (never purged: a cell's
evidence evolution, graduation and death survive the rules tier's
per-run wipes).

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
from analysis.analysis_signals.engines._ohlcv_boost import OhlcvBoost
from analysis.analysis_signals.engines.evaluation import (
    CellGate,
    cell_stats,
    pooled_evidence,
)
from analysis.analysis_signals.fetch.regimes import fetch_market_regimes
from analysis.trends import EpisodeDetector, attach_trend

logger = logging.getLogger(__name__)

_DAY_CLOSE = time(15, 0)

# the frequency floor's warmup guard: calendar days before which a
# code's trailing-bar population cannot be complete (HP_LEG_BAR_MIN_
# PERIODS ~ 255 traded rows ≈ 400 calendar days) — the year containing
# a code's first ~400 days can never carry triggers, so it is not an
# eligible year (2026-10-25, the per-year coverage floor)
FREQ_WARMUP_DAYS = 400


def eligible_freq_years(df: pd.DataFrame) -> dict[str, list[int]]:
    """The per-code ELIGIBLE YEARS for the frequency floor (the
    2026-10-25 PER-YEAR COVERAGE semantics): calendar years that carry
    rows, whose Jan-1 sits past the trailing-bar warmup (first date +
    FREQ_WARMUP_DAYS), COMPLETE years only (the running year is exempt
    until it ends — a January rebuild must not loosen every pair to the
    shallowest tail for a year that has barely started). ONE host
    unwrap of code/date, then a single pass — the tiny per-code
    summary is the sanctioned host boundary."""
    if df.empty:
        return {}
    codes = host_array(df["code"]).tolist()
    dates = host_array(df["date"]).astype("datetime64[D]")
    years = dates.astype("datetime64[Y]").astype("int64") + 1970
    first: dict[str, np.datetime64] = {}
    last: dict[str, np.datetime64] = {}
    present: dict[str, set] = {}
    for i, c in enumerate(codes):
        f = first.get(c)
        if f is None:
            first[c] = dates[i]
            last[c] = dates[i]
            present[c] = {int(years[i])}
        else:
            if dates[i] < f:
                first[c] = dates[i]
            if dates[i] > last[c]:
                last[c] = dates[i]
            present[c].add(int(years[i]))
    out: dict[str, list[int]] = {}
    for c, lo in first.items():
        y0 = int((lo + np.timedelta64(FREQ_WARMUP_DAYS, "D"))
                 .astype("datetime64[Y]").astype("int64")) + 1970
        last_y = int(last[c].astype("datetime64[Y]").astype("int64")) \
            + 1970
        out[c] = sorted(y for y in present[c] if y0 <= y < last_y)
    return out


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
    # the candidate's own recorded-value column override (the history
    # rows' ``signal``): None = the run_cells call's ``value_col`` —
    # the hedge trio's window grid records the winning window's own
    # rsi_mean (rsi14 vs rsi10 means are different scales)
    value_col: str | None = None


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
    # The PORTFOLIO-tier flag (2026-10-24): True on entries whose rows
    # are SOURCED by a portfolio family — every emitted rule/history
    # row then rides is_in_portfolio TRUE, the PK's tier axis (a
    # sourced variant of a rule is a SEPARATE row from the standalone
    # one; the live active set and the standalone families' wipe
    # scopes exclude the tier). The hedge family's source layer
    # (_ComponentRsiEntry) is the writer; standalone families keep the
    # default FALSE and the ORCHESTRATING composite rows carry FALSE
    # (the group IS the composite).
    PORTFOLIO_TIER: bool = False
    # Whether the family's frame codes belong in analysis.trend_days
    # (run_family's _write_trend_days). Composite-scope families whose
    # codes are not securities opt OUT.
    WRITES_TREND_DAYS: bool = True
    # The forward-blend RESPONSE price columns (the multi-response
    # extension, 2026-10-18): a family that measures cells against
    # more than the frame's own ``price`` declares the extra price
    # columns here — prepare_frame attaches EACH one's forward-blend
    # set (the primary ``price`` keeps the legacy names _blend /
    # _blend_ok / _fwd{h}; extras carry the column suffix —
    # _blend_{col} / _blend_ok_{col} / _fwd{h}_{col}), and
    # run_cells(response_col=...) measures that call's cells against
    # the named set.
    FORWARD_PRICE_COLS: tuple[str, ...] = ("price",)

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
        # the performance snapshot's AS-OF date (the frame's data
        # horizon), cached at first run_cells call — the dated key of
        # the history_signal_performance row each emitted rule cell
        # carries (the 2026-10-19 constant/performance split)
        self._perf_as_of: date | None = None

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

    def rule_wipe_scopes(self) -> list[tuple[int, list[str], bool | None]]:
        """The (signal_type code, codes, tier) scopes this run's
        signal_rules rows live at — run_family's PRE-INSERT WIPE.
        CODE-SCOPED (concurrent writers of the SAME signal_type at
        other codes never collide) and TIER-PARTITIONED (the PK's
        is_in_portfolio axis since 2026-10-23: standalone runs own the
        FALSE tier, a portfolio family's source layer owns the TRUE
        tier of the family vocabulary it stamps; None = both tiers —
        the composite scopes that own their codes outright). Default:
        the family's own type at the run's codes, FALSE tier."""
        return [(self.signal_type_code, self.codes, False)]

    def history_purge_scopes(self) -> list[tuple[int, list[str], bool | None]]:
        """The (signal_type code, codes, tier) scopes this family owns
        in history_signals — run_family's --force purge, partitioned
        exactly like the rule wipe scopes."""
        return self.rule_wipe_scopes()

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
        all-calm degenerate split, found + fixed 2026-10-15).
        REGIME_FREE families stamp the constant pooled 4 and skip the
        fetch entirely — their cells never split by the column (the
        joined values would be dead weight), so the whole sec_type's
        regime table must not be pulled for them."""
        if self.REGIME_FREE:
            df = df.copy()
            df["regime"] = np.full(len(df), 4, dtype="int8")
            return df
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
        """The fixed forward blend dir_ave axes — ONE set per declared
        FORWARD_PRICE_COLS response column (the primary ``price`` the
        legacy ``_blend``/``_blend_ok``/``_fwd{h}`` names; extras the
        ``_{col}``-suffixed twins): fwd_h = the close-to-close change
        h traded days after the trigger (delay-0 profile — the
        trigger IS the episode's first day), blended
        0.10/0.15/0.50/0.15/0.10 over (0, 3, 5, 10, 20). ``_blend_ok``
        marks rows whose full 20d future is realized (measurement uses
        only these; the newest triggers still replay into history with
        their cell's prepared confidence)."""
        for price_col in self.FORWARD_PRICE_COLS:
            self._attach_forward_for(df, price_col)
        return df

    @staticmethod
    def _blend_col_names(price_col: str) -> tuple[str, str, str]:
        """(blend, ok, fwd-template) column names for one response
        price column — the legacy unsuffixed names for the primary
        ``price`` (every pre-multi-response contract reads them)."""
        if price_col == "price":
            return "_blend", "_blend_ok", "_fwd{h}"
        return f"_blend_{price_col}", f"_blend_ok_{price_col}", \
            f"_fwd{{h}}_{price_col}"

    def _attach_forward_for(self, df: pd.DataFrame,
                            price_col: str) -> None:
        """One response column's forward-blend set (see
        _attach_forward — the shared loop over FORWARD_PRICE_COLS
        calls this per column)."""
        keys = ["code"]
        fwd_tmpl = self._blend_col_names(price_col)[2]
        blend = pd.Series(0.0, index=df.index, dtype="float64")
        ok = pd.Series(True, index=df.index, dtype="bool")
        for h, w in zip(cfg.FORWARD_BLEND_DAYS, cfg.FORWARD_BLEND_WEIGHTS):
            if h == 0:
                continue
            fwd_name = fwd_tmpl.format(h=h)
            fwd = grouped_shift(
                df, keys, [price_col], out_names=[fwd_name], periods=-h,
            )[fwd_name]
            fwd = fwd / df[price_col] - 1.0
            df[fwd_name] = fwd
            blend = blend + w * fwd.fillna(0.0)
            ok = ok & fwd.notna()
        blend_col, ok_col, _ = self._blend_col_names(price_col)
        df[blend_col] = blend
        df[ok_col] = ok

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
        """The evidence bar (see evaluation.CellGate — the dir_ave bar
        only; ``n`` shapes the sample_scale confidence, never the
        verdict)."""
        return CellGate().passes(action, n, dir_ave)

    @staticmethod
    def confidence_pct(base_pct: float, depth_norm: float,
                       factor: float,
                       sample_scale: float = 1.0) -> float:
        """Depth-scaled confidence in percent (see evaluation.CellGate;
        ``sample_scale`` the starvation haircut min(n/floor, 1))."""
        return CellGate().confidence_pct(base_pct, depth_norm, factor,
                                         sample_scale)

    # -- the shared self-adaptive cell loop ------------------------------------
    def _performance_as_of(self, df: pd.DataFrame) -> date:
        """The performance snapshot's AS-OF date — the prepared frame's
        data horizon (max date), the day the cells were measured
        THROUGH. Cached per entry (ONE host unwrap of the date column
        across the entry's run_cells calls); the terminal [D] cast is
        the sanctioned python-date boundary (the _write_trend_days
        pattern)."""
        if self._perf_as_of is None:
            self._perf_as_of = host_array(df["date"]).max() \
                .astype("datetime64[D]").astype("O")
        return self._perf_as_of

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
        response_col: str = "price",
        freq_floor_per_year: float | None = None,
        gate_min: dict[str, float] | None = None,
        pooled_dir_ave_min: float | None = None,
        signal_type_code: int | None = None,
        portfolio_tier: bool | None = None,
        pe_boosted: bool = False,
        ohlcv_boosted: bool = False,
    ) -> tuple[list[dict], list[dict]]:
        """Measure every candidate per cell, pick each cell's WINNING
        candidate (the gate-passing one with the best dir_ave; or,
        under ``freq_floor_per_year``, the deepest gate-passer meeting
        the frequency floor; ties → the FIRST candidate — the widest
        bar), run the POOLED EVIDENCE
        TIER, and emit the cell's signal_rules row (rule_params = the
        CONSTANT rule definition only — axis, K, the reentry flags, the
        family's bar_def/params, the gate bar constants, the confidence
        scale) plus history rows for the winning cells' episode-first
        triggers. The MEASURED items ride each row's ``_performance``
        payload — the cell's OWN measure block only (n / dir_ave /
        std / t / hit + the as-of date): run_family peels it into
        analysis_signals.history_signal_performance as the run's dated
        snapshot.

        ``ungated`` marks observational populations (the VRP-rejection
        twin): cells emit regardless of the gate (still n >= 1),
        recording the measured evidence as-is (no pooled tier either).

        ``response_col`` selects WHICH declared FORWARD_PRICE_COLS
        response this call's cells measure against (default "price" —
        the frame's own price). The blend set for that column must
        exist on the frame — prepare_frame attaches every
        FORWARD_PRICE_COLS member's set.

        ``freq_floor_per_year`` (optional) switches the winner policy
        to the FREQUENCY FLOOR — the 2026-10-25 PER-YEAR COVERAGE
        semantics (user-directed "portfolio event should be at least
        once per year"): among the gate-passing candidates (iterated
        in the caller's order — deepest bar first) the cell picks the
        FIRST whose episode-first triggers cover EVERY eligible
        calendar year of the code (>= ``floor`` triggers in each year
        past the trailing-bar warmup; the running year exempt),
        loosening the bar just enough; the best-dir_ave winner stays
        the fallback for cells no candidate carries to the floor. The
        prior horizon-AVERAGE semantics (n/yr over the whole span,
        2026-10-25 morning) retired the same day: it let a pair's
        events cluster in the wash years while whole calendar years
        stayed empty (21/44 hedge spread winners carried an empty
        year). None (default) = the best-dir_ave winner everywhere.
        The measured population is the winner's own — the emitted
        evidence describes exactly what fires.

        ``gate_min`` / ``pooled_dir_ave_min`` (optional) override the
        evidence bars for THIS call (family-scoped loosening — the
        config globals stay the standalone families' discipline): the
        per-candidate dir_ave bar and the pooled tier's mean bar; the
        pooled statistics legs (t bar, expanding-year majority) stay
        the shared contract.

        ``signal_type_code`` / ``portfolio_tier`` (optional) override
        the row identity for THIS call: the registered STANDALONE
        cells stamp ANOTHER family's type (the hedge trio's sourced
        mov_rsi cells — signal_type mov_rsi) and the PORTFOLIO tier
        (is_in_portfolio TRUE — the sourced layer's own axis); the
        default is the entry's own identity.

        ``pe_boosted`` (optional) stamps the row identity's PE-
        CONDITIONING axis (signal_rules / history_signals /
        history_signal_performance PK member is_pe_boosted, 2026-10-26
        user-directed, the is_ohlcv_boosted/is_in_portfolio
        PK-member precedent): TRUE = this call's cells are the
        PE-gated variant of the sub type (the candidate masks are
        AND-gated by the code's own PE-percentile condition) — a
        SEPARATE row set from the unconstrained variant's, whose call
        runs with the default False. The gate definition rides the
        candidates' rule_params (bar_def.pe_gate).

        ``ohlcv_boosted`` (optional) stamps the row identity's
        OHLCV-boost VARIANT axis (signal_rules PK member
        is_ohlcv_boosted since 2026-10-26, user-directed — mirrored
        from history_signals, where it has keyed since the boost-twin
        era; history_signal_performance carries it in its PK too):
        TRUE = this call's cells are the OHLCV-boosted variant of the
        sub type — a SEPARATE row set from the unconstrained one.
        RETIRED as a live variant 2026-10-16 (the twins became the
        per-cell trust-weighted measurement): no emitter passes True
        today, the flag rides FALSE on every row and exists for the
        key's shape — the kwarg keeps the two variant axes symmetric.

        THE POOLED EVIDENCE TIER (2026-10-16; starvation-trading since
        2026-10-19): the per-cell floors cannot reach statistical
        significance (blend sigma ~2.6% -> n=3 SE ~1.5%, the 0.25% bar
        ~0.16 SE), so the BUY edge claim lives at the (sub_type,
        action) POOLED population — all winning cells' measurable
        triggers across codes, judged by pooled t-stat + expanding-year
        sign consistency (evaluation.pooled_evidence). A BUY side whose
        statistics legs FAIL (the dir_ave bar, the t bar, the year
        majority) is demoted to measured-out: its cells emit no rules
        rows and no history rows that run. A side under POOLED_MIN_N
        but past POOLED_TRADE_FLOOR with the statistics legs clear
        TRADES at the pooled completeness scale min(n / POOLED_MIN_N, 1)
        of the confidence (folded into every row's history-record
        scale — an in-run haircut since 2026-10-20; the pooled block
        no longer rides the cell snapshot). SELL records the pooled
        evidence on its side but is never pooled-gated (exit timing —
        the equity asymmetry).

        STARVED cells TRADE since 2026-10-19 (user-directed; retired
        the 2026-10-16 wait replay): the sample floor is no longer a
        verdict — ``CellGate.passes`` judges the dir_ave bar only, and
        a cell holding fewer episodes than its floor emits its REAL
        action at the sample-completeness confidence
        ``min(n / floor, 1)`` (the starvation haircut; readers
        recompute it from the snapshot's n_episodes + the rule's
        gate.sample_floor constant — the digest no longer rides the
        cell snapshot since 2026-10-20). A deepest-claim breach at an
        extreme corner now records as a REDUCED-CONFIDENCE trading
        row — the extreme-event monotone guarantee kept, without the
        not-trading-yet wait population. Measured-out cells (dir_ave
        below the bar, any n) still emit NOTHING — that is the gate
        rejecting on evidence. ``ungated`` populations (the VRP-fail
        twin) stay observational wait rows: every cell with episodes
        emits, confidence flat."""
        action_code = ACTION_CODE[action]
        # the row-identity overrides (the registered STANDALONE cells
        # stamp another family's type + the PORTFOLIO tier)
        st_code = self.signal_type_code if signal_type_code is None \
            else signal_type_code
        tier = self.PORTFOLIO_TIER if portfolio_tier is None \
            else portfolio_tier
        keys = self.cell_keys
        sub_code = self.sub_type_code(sub_type)
        # this call's response axis: the declared FORWARD_PRICE_COLS
        # column whose blend set the cells measure against (primary
        # "price" the legacy unsuffixed names)
        blend_col, ok_col, fwd_tmpl = self._blend_col_names(response_col)
        detector = EpisodeDetector(k)
        gate = CellGate(gate_min=gate_min)
        # the boost's day-0 axes ride the family frame? (mov_rsi and
        # bollinger fetch them; the options frames do not — the boost
        # then silently stays off for the run)
        if self._boost.enabled and not self._boost.axes_ready:
            self._boost.check_axes(df)

        # the winners: the best bar-clearing candidate per cell — the
        # dir_ave bar is the ONLY verdict (2026-10-19; the sample floor
        # discounts the confidence instead of blocking, see the
        # emission loop). Ungated populations (the VRP twin) take any
        # candidate with episodes.
        chosen: dict[tuple, tuple[Candidate, dict]] = {}
        freq_chosen: dict[tuple, tuple[Candidate, dict]] = {}
        per_cand: list[tuple[Candidate, pd.Series, pd.DataFrame]] = []
        # the frequency floor's per-code ELIGIBLE YEARS (one shared
        # whole-frame helper — the same map hedge_pairs'
        # _pick_window_tails reads)
        freq_elig: dict[str, list[int]] = {}
        cand_year_counts: dict[int, dict[tuple, dict[int, int]]] = {}
        if freq_floor_per_year is not None:
            freq_elig = eligible_freq_years(df)
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
        for ci, cand in enumerate(candidates):
            trig_mask = detector.triggers(df, cand.mask,
                                          trend=trend_series,
                                          context=ctx_series)
            per_cand.append((cand, trig_mask, df[trig_mask]))
            trig = per_cand[-1][2]
            if trig.empty:
                continue
            if freq_floor_per_year is not None:
                # the candidate's per-cell PER-YEAR trigger counts
                # (episode FIRST days — all of them, the newest
                # unmeasurable included: the frequency counts what
                # fires) — the small trigger frames are the sanctioned
                # boundary
                counts: dict[tuple, dict[int, int]] = {}
                t_codes = host_array(trig["code"]).tolist()
                t_years = host_array(trig["date"]) \
                    .astype("datetime64[D]").astype("datetime64[Y]") \
                    .astype("int64").tolist()
                t_trend = host_array(trig["trend"]).astype("int64") \
                    .tolist() if "trend" in keys else None
                t_regime = host_array(trig["regime"]).astype("int64") \
                    .tolist() if "regime" in keys else None
                for i in range(len(t_codes)):
                    c = (str(t_codes[i]),)
                    if t_trend is not None:
                        c = c + (t_trend[i],)
                    if t_regime is not None:
                        c = c + (t_regime[i],)
                    cell_years = counts.setdefault(c, {})
                    # datetime64[Y] ints are years SINCE 1970 — the
                    # shared +1970 epoch convention (the pooled facts
                    # line below does the same)
                    y = int(t_years[i]) + 1970
                    cell_years[y] = cell_years.get(y, 0) + 1
                cand_year_counts[ci] = counts
            measurable = trig[trig[ok_col].fillna(False)]
            if measurable.empty:
                continue
            for row in cell_stats(measurable, action, keys,
                                  blend_col=blend_col) \
                    .to_dict("records"):
                cell = tuple(row[kk] for kk in keys)
                n = int(row["n"])
                dir_ave = float(row["dir_ave"])
                passed = (n >= 1) if ungated else gate.passes(
                    action, n, dir_ave,
                )
                if not passed:
                    # measured-out: below the side's dir_ave bar — the
                    # gate rejecting on evidence, any n
                    continue
                std = float(row["dir_std"])
                t_stat = (dir_ave / (std / math.sqrt(n))
                          if n >= 2 and math.isfinite(std)
                          and std > 1e-12 else None)
                meas = {"measure": {
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
                }}
                prev = chosen.get(cell)
                if prev is None or dir_ave * 100.0 \
                        > prev[1]["measure"]["dir_ave_pct"]:
                    chosen[cell] = (cand, meas)
                if freq_floor_per_year is None or cell in freq_chosen:
                    continue
                years_n = cand_year_counts.get(ci, {}).get(cell, {})
                elig = freq_elig.get(cell[0], [])
                if elig and all(years_n.get(y, 0) >= freq_floor_per_year
                                for y in elig):
                    # the PER-YEAR COVERAGE FLOOR: the FIRST
                    # gate-passing candidate (the caller iterates
                    # deepest-bar first) whose triggers cover EVERY
                    # eligible year wins — loosen just enough
                    freq_chosen[cell] = (cand, meas)

        if freq_chosen:
            # the frequency-floor picks override the best-dir_ave
            # fallback wherever a candidate carried the cell to the
            # floor
            chosen = {**chosen, **freq_chosen}

        # the emitted winners — every chosen cell is a trading row
        # (starved cells emit their REAL action at the reduced
        # confidence; only the ungated twins stay observational wait)
        if not chosen:
            return [], []

        # THE POOLED EVIDENCE TIER (2026-10-16; verdict-first since the
        # 2026-10-19 pooled starvation retreat) — the two-tier gate's
        # statistical layer: the (sub_type, action) pooled population
        # of the winning cells' MEASURABLE triggers across codes. A BUY
        # side that fails the statistics legs (the dir_ave bar, the
        # t-stat bar, the measurable-year sign majority) AND the n
        # floors is demoted to measured-out (no rules rows, no history
        # that run); a side under POOLED_MIN_N but past
        # POOLED_TRADE_FLOOR with the statistics legs clear TRADES at
        # the pooled completeness scale min(n / POOLED_MIN_N, 1) of the
        # confidence — folded into every row's sample scale below (the
        # live tier reads the same total from the snapshot's
        # gate.conf_scale). SELL records the evidence ungated (exit
        # timing — the equity asymmetry). Host boundary per the row
        # loop's convention (the frames are the small chosen-trigger
        # subsets).
        pooled: dict | None = None
        pooled_scale = 1.0
        if not ungated:
            sgn0 = 1.0 if action == "buy" else -1.0
            fwd_hs = [h for h in cfg.FORWARD_BLEND_DAYS if h > 0]
            facts: list[dict] = []
            for cand, trig_mask, trig in per_cand:
                if trig.empty:
                    continue
                hit_mask = pd.Series(False, index=trig.index,
                                     dtype="bool")
                for cell, (win_cand, _) in chosen.items():
                    if win_cand is not cand:
                        continue
                    m = trig["code"] == cell[0]
                    if "trend" in keys:
                        m = m & (trig["trend"]
                                 == cell[keys.index("trend")])
                    if "regime" in keys:
                        m = m & (trig["regime"]
                                 == cell[keys.index("regime")])
                    hit_mask = hit_mask | m
                fired = trig[hit_mask.fillna(False)]
                if fired.empty:
                    continue
                ok_np = host_array(fired[ok_col]).astype(bool)
                blend_np = host_array(fired[blend_col])
                fwd_np = {h: host_array(fired[fwd_tmpl.format(h=h)])
                          * 100.0 for h in fwd_hs}
                years_np = host_array(fired["date"]) \
                    .astype("datetime64[D]") \
                    .astype("datetime64[Y]").astype("int64") + 1970
                for i in range(len(fired)):
                    if not ok_np[i]:
                        continue
                    facts.append({
                        "year": int(years_np[i]),
                        # sign-aligned favorable response, PERCENT
                        "fav": float(blend_np[i]) * 100.0 * sgn0,
                        **{f"f{h}": float(fwd_np[h][i]) * sgn0
                           for h in fwd_hs},
                    })
            pooled = pooled_evidence(facts, dir_ave_min=pooled_dir_ave_min)
        pooled_demoted = bool(pooled is not None and action == "buy"
                              and not pooled["passed"])
        if pooled_demoted:
            logger.warning(
                f"[{self.FAMILY}] {sub_type}/{action}: POOLED evidence "
                f"REJECTED — {len(chosen)} winning cell(s) emit nothing "
                f"(pooled n={pooled['n']} "
                f"dir_ave={pooled['dir_ave_pct']:+.2f}% "
                f"t={pooled['t_stat']} years_positive="
                f"{pooled['years']['positive']})",
            )
            return [], []
        if pooled is not None and action == "buy" and pooled["starved"]:
            pooled_scale = float(pooled["conf_scale"])
            logger.info(
                f"[{self.FAMILY}] {sub_type}/{action}: POOLED evidence "
                f"STARVED-TRADING — pooled n={pooled['n']} < floor "
                f"{pooled['floor']} at conf_scale={pooled_scale} "
                f"(dir_ave={pooled['dir_ave_pct']:+.2f}% "
                f"t={pooled['t_stat']} years_positive="
                f"{pooled['years']['positive']})",
            )

        rules_rows: list[dict] = []
        for cell, (cand, meas) in chosen.items():
            code = str(cell[0])
            trend_code = int(cell[keys.index("trend")]) \
                if "trend" in keys else 4
            regime_code = int(cell[keys.index("regime")]) \
                if "regime" in keys else 4
            m = meas["measure"]
            # the 2026-10-19 CONSTANT/PERFORMANCE split: the gate's BAR
            # constants (+ the observational designation) ride the
            # rule; the VERDICT is the rule's is_valid boolean (the
            # unified dir_ave test)
            gate_bars: dict = {
                "metric": "dir_ave",
                "min_pct": round(
                    cfg.GATE_DIR_AVE_MIN[action] * 100.0, 4)
                if action in cfg.GATE_DIR_AVE_MIN else None,
                "sample_floor": cfg.SAMPLE_FLOOR.get(action),
                "ungated": ungated,
            }
            params: dict = {
                "axis": f"{self.FAMILY}:{sub_type}",
                "episode_merge_days": k,
                "reentry_trend_split": self.TREND_AWARE_REENTRY,
                "reentry_cell_split": self.CELL_SPLIT_EPISODES,
                "gate": gate_bars,
                "confidence": {
                    # the measured BASE (= the cell's dir_ave pct) lives
                    # on the performance snapshot (dir_ave_pct); the
                    # rule carries the SCALE only
                    "depth_factor": depth_factor,
                    "depth_cap": 2.0,
                },
            }
            params.update(cand.params)
            if extra_params:
                params.update(extra_params)
            # the VARIANT-AXIS stamp (2026-10-27, user-directed "update
            # rule params if ohlcv and pe is used to boost"): a boosted
            # variant's params name the axes beside the DB's
            # is_pe_boosted / is_ohlcv_boosted columns — the
            # self-describing JSON convention (the portfolio sub_type
            # precedent); the plain population carries no key (the
            # gate CONDITIONS ride bar_def.pe_gate / bar_def.ohlcv_gate)
            if pe_boosted:
                params["pe_boosted"] = True
            if ohlcv_boosted:
                params["ohlcv_boosted"] = True
            rules_rows.append({
                "code": code,
                "sec_type": self.sec_type_code,
                "signal_type": st_code,
                "signal_sub_type": sub_code,
                "trend": trend_code,
                "regime_state": regime_code,
                # starved cells TRADE at the reduced confidence — the
                # only wait rows left are the ungated twins (the twin's
                # own action argument is "wait")
                "action": action_code,
                # the per-cell GATE VERDICT (denormalized gate.passed):
                # TRUE for bar-clearing trading cells — negative future
                # returns never validate (measured-out cells do not
                # emit; the ungated twin rows stay observational)
                "is_valid": not ungated,
                # the schema's tier flag (a signal_rules PK member):
                # TRUE on the portfolio families' SOURCE-layer rows
                # (the PORTFOLIO_TIER entry flag), FALSE on the
                # standalone families' rows and the orchestrating
                # composites (the group IS the composite)
                "is_in_portfolio": tier,
                # the schema's PE-conditioning flag (a signal_rules PK
                # member): TRUE on this call's PE-gated variant cells
                # (the pe_boosted kwarg), FALSE on the unconstrained
                # population
                "is_pe_boosted": pe_boosted,
                # the schema's OHLCV-boost VARIANT flag (a signal_rules
                # PK member since 2026-10-26 — mirrored from
                # history_signals): TRUE only on a future boosted-
                # variant call (the ohlcv_boosted kwarg); the twins era
                # is retired and every emitter ships FALSE today
                "is_ohlcv_boosted": ohlcv_boosted,
                "rule_entry": self.entry_name,
                "rule_params": params,
                # the MEASURED half of the former rule_params (the
                # 2026-10-19 split, the cell's OWN measure): run_family
                # peels this payload into analysis_signals.
                # history_signal_performance as the run's dated snapshot
                "_performance": {
                    "date": self._performance_as_of(df),
                    "n_episodes": m["n_episodes"],
                    "dir_ave_pct": m["dir_ave_pct"],
                    "dir_std_pct": m["dir_std_pct"],
                    "t_stat": m["t_stat"],
                    "hit_rate": m["hit_rate"],
                },
            })

        # replay: the winning cells' triggers become history rows at
        # their cell's confidence (starved cells at the reduced scale)
        base_pct = {
            cell: meas["measure"]["dir_ave_pct"]
            for cell, (_, meas) in chosen.items()
        }
        scales = {
            cell: (1.0 if ungated
                   else gate.sample_scale(
                       action, meas["measure"]["n_episodes"],
                   ) * pooled_scale)
            for cell, (_, meas) in chosen.items()
        }
        history_rows: list[dict] = []
        staged_anchors: list[dict] = []   # boost facts (dropped when
                                          # the side is pooled-demoted)
        for cand, trig_mask, trig in per_cand:
            if trig.empty:
                continue
            # the triggers whose cell chose THIS candidate
            hit_mask = pd.Series(False, index=trig.index, dtype="bool")
            for cell, (win_cand, _) in chosen.items():
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
            vals_np = host_array(fired[cand.value_col or value_col])
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
            # the boost + the pooled tier need the blend/ok legs — the
            # boost collect flag no longer gates them (the pooled facts
            # were collected verdict-first, above)
            blend_np = host_array(fired[blend_col])
            ok_np = host_array(fired[ok_col]).astype(bool)
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
                    "signal_type": st_code,
                    "signal_sub_type": sub_code,
                    # the boost-variant flag (a history_signals PK
                    # member): this call's ohlcv_boosted identity —
                    # FALSE on every emitter since the twins' retirement
                    "is_ohlcv_boosted": ohlcv_boosted,
                    "date": py_dates[i],
                    "time": _DAY_CLOSE,
                    # the schema's tier flag (a history_signals PK
                    # member): TRUE on the portfolio families' SOURCE-
                    # layer events (the entry's PORTFOLIO_TIER flag),
                    # FALSE on the standalone families' events and the
                    # orchestrating composite events
                    "is_in_portfolio": tier,
                    # the schema's PE-conditioning flag (a
                    # history_signals PK member): mirrors the rules
                    # row's is_pe_boosted for this call's cells
                    "is_pe_boosted": pe_boosted,
                    # starved cells record their REAL action at the
                    # reduced confidence (the 2026-10-19 starvation
                    # retreat — no wait replay any more)
                    "action": action_code,
                    "signal": round(float(vals_np[i]), 6),
                    "signal_reference": round(float(ref_np[sel_np][i]), 6),
                    # the record column stays on the INTEGER
                    # basis-point scale (×100 the pct confidence)
                    "confidence": round(
                        gate.confidence_pct(
                            base_pct[cell], float(depth_np[sel_np][i]),
                            depth_factor, scales[cell],
                        ) * 100.0,
                    ),
                    "is_day_close_trigger": True,
                    "regime_state": int(regime_np[i]),
                    "is_typical": None if typ_np is None
                    else (None if bool(np.isnan(typ_np[sel_np][i]))
                          else bool(typ_np[sel_np][i])),
                })
                if collect:
                    # every emitted trading row feeds the boost
                    # derivation (the starved observational era is
                    # over); a pooled-demoted side's anchors are
                    # dropped with its rows
                    staged_anchors.append({
                            "code": code,
                            "sub_type": sub_type,
                            # the rules-row key of the cell this
                            # trigger measured (the boost derivation's
                            # population key — the per-cell stamp was
                            # retired with its column, 2026-10-20)
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
        if staged_anchors:
            self._boost.facts.extend(staged_anchors)
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
