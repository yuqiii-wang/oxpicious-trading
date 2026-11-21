"""CombPipeline — the ONLY orchestrator of the comb stack.

Stage-order contract (mirrors the 2026-11-16/17 study stack exactly;
skill ``singleton-strategy-tuning`` Parts 6+10-12; stage 0 added
2026-11-18 per Part 16, stage 0b the same day per Part 17) on the
unified mixed frame's ``sig_<family>`` columns:

    pre-blend, per family column:
      (0) RejectOffset        — family-local same-side self-suppression
                                on the family's own reject bars
                                (default OFF; cross-family offsets are
                                structurally absent — measured harmful)
      (0b) NoActionOffset     — family-local same-side suppression of
                                MARKED FIRED days (the no-action
                                overlay tier; default OFF — the reject
                                tier's day-level mirror)
      (1) RegimeGate          — thesis-matched BUY gating (SMA-w)
      (2) QuantileSizing      — evidence percentile sizing (own fam+code)
      (3) VolStateScaling     — EWMA-vol z-state exposure (buys only)
      (3b) AmplifierScaling   — signal-amplification evidence sizing
                                (the amplifier tier's per-day bindings
                                scale the family's own signed signal;
                                default ON, family-local both sides)
      (4) EquityGuard         — pass-2 only, buys × slope factor
    blend:
      (5) per-date family weights (EdgeWeightModel / equal), the
          num/Σ|w| re-normalized weighted sum, clipped to ±100;
          ``algo_w_<family>`` columns carry the effective per-bar
          weights for the collector's __MIX__ reason contract
    post-blend:
      (6) BreadthFinisher     — cross-code capitulation finishing

The dust gate (``tune_signals``) stays with the collector — it gates the
FINAL consolidated signal, after stage (6).

Port-fidelity note (A/B vs the study harness, 2026-11-17): identical
except the early-history starvation window — the study's mean-known
weight fallback spans the 8-family census while production spans the
selected families, so on the first weeks of history (before every
selected family has ``floor_events`` completed events) production falls
to exact equal weights where the study blended census-mean weights.
Measured impact on the champion pair: 2-3 rows per code (Feb-Mar 2020
only), full-series return within 0.1pp, Calmar within 0.002.

This module imports siblings only — never the engine, never the DB. The
collector calls :meth:`CombPipeline.blend`; the 2-pass guard protocol is
driven by the collector (which owns the engine) via ``guard_state``.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from strategy.factors_and_algos._comb.amplifier import (
    AmpStash,
    AmplifierScaling,
)
from strategy.factors_and_algos._comb.config import CombParams
from strategy.factors_and_algos._comb.data_definitions import (
    GuardState,
    WeightTable,
)
from strategy.factors_and_algos._comb.edge import EdgeWeightModel
from strategy.factors_and_algos._comb.events import EventDataset
from strategy.factors_and_algos._comb.guard import EquityGuard
from strategy.factors_and_algos._comb.no_action import NaStash, NoActionOffset
from strategy.factors_and_algos._comb.reject import RejStash, RejectOffset
from strategy.factors_and_algos._comb.transforms import (
    BreadthFinisher,
    QuantileSizing,
    RegimeGate,
    VolStateScaling,
)

BpStash = Dict[str, Dict[Tuple[str, date], float]]


class CombPipeline:
    """Applies the configured comb stack and blends the consolidated
    ``signal_confidence``. One instance per blend call (stateless)."""

    def __init__(self, cfg: CombParams) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------
    def blend(
        self,
        df: pd.DataFrame,
        families: Sequence[str],
        bp_stash: Optional[BpStash] = None,
        guard_state: Optional[GuardState] = None,
        rej_stash: Optional[RejStash] = None,
        na_stash: Optional[NaStash] = None,
        amp_stash: Optional[AmpStash] = None,
    ) -> pd.DataFrame:
        """Transform the per-family columns, blend, and return the frame
        carrying ``signal_confidence`` / ``signal_value`` /
        ``algo_w_<family>`` (the transformed ``sig_<family>`` columns
        stay on the frame — the collector renames them to
        ``algo_conf_<family>`` for the reason builder).

        ``rej_stash`` is the reject-tier evidence (``_comb/reject.py``)
        — consumed by stage (0) only when ``cfg.reject.enabled``.
        ``na_stash`` is the no-action overlay evidence
        (``_comb/no_action.py``) — consumed by stage (0b) only when
        ``cfg.no_action.enabled``. ``amp_stash`` is the signal-
        amplification evidence (``_comb/amplifier.py``) — consumed by
        stage (3b) only when ``cfg.amplifier.enabled``."""
        families = list(families)
        dataset = EventDataset.from_frame(df, families, bp_stash)

        # (0) reject self-suppression (family-local, same-side only)
        df = RejectOffset(self.cfg.reject).apply(df, families, rej_stash)
        # (0b) no-action suppression of MARKED FIRED days (the reject
        # tier's day-level mirror — same-side only, family-local)
        df = NoActionOffset(self.cfg.no_action).apply(df, families, na_stash)
        # (1) regime gate
        if self.cfg.regime_gate.enabled:
            df = RegimeGate(self.cfg.regime_gate).apply(df, families)
        # (2) evidence quantile sizing
        if self.cfg.quantile_sizing.enabled:
            df = QuantileSizing(
                self.cfg.quantile_sizing, dataset, bp_stash or {},
            ).apply(df, families)
        # (3) vol-state exposure
        if self.cfg.vol_state.enabled:
            df = VolStateScaling().apply(df, families)
        # (3b) signal-amplification evidence sizing (the U2 sibling —
        # the amplifier tier's per-day bindings scale the family's own
        # signed signal; family-local, both sides)
        if self.cfg.amplifier.enabled:
            df = AmplifierScaling(
                self.cfg.amplifier, amp_stash).apply(df, families)
        # (4) equity guard (pass-2 only — state built by the collector)
        if guard_state is not None:
            df = EquityGuard(self.cfg.equity_guard).apply(
                df, families, guard_state)

        # (5) weights + blend
        df = self._blend_weighted(df, families, dataset)

        # (6) breadth finishing (post-blend)
        if self.cfg.breadth.enabled:
            df = BreadthFinisher(dataset).apply(df)
        return df

    # ------------------------------------------------------------------
    def _weight_table(
        self, families: Sequence[str], dataset: EventDataset,
    ) -> WeightTable:
        """Equal weights, or the edge model's adaptive table."""
        if self.cfg.weights.mode == "equal":
            per = {f: 1.0 for f in families}
            weights = {d: dict(per) for d in dataset.union_dates}
            return WeightTable(weights)
        return EdgeWeightModel(self.cfg.weights, dataset).build()

    def _blend_weighted(
        self,
        df: pd.DataFrame,
        families: Sequence[str],
        dataset: EventDataset,
    ) -> pd.DataFrame:
        """Weighted blend with re-normalization over the selected
        families: conf = Σ w·sig / Σ|w| (study ``apply_edge_weights``),
        clipped to ±100; writes the per-bar ``algo_w_<family>`` columns
        (w/Σ|w| — the effective weights the reason builder shows)."""
        table = self._weight_table(families, dataset)
        du = df[["date"]].drop_duplicates().sort_values("date").reset_index(drop=True)
        dates_host = host_array(du["date"])

        w_cols: Dict[str, str] = {}
        for fam in families:
            col = f"w_{fam}"
            du = du.assign(**{col: np.array([
                table.at(d if isinstance(d, date) else
                         d.astype("datetime64[D]").item()).get(fam, 0.0)
                for d in dates_host
            ], dtype=np.float64)})
            w_cols[fam] = col
        df = df.merge(du, on="date", how="left")
        for col in w_cols.values():
            df[col] = df[col].fillna(0.0)

        num = pd.Series(0.0, index=df.index)
        den = pd.Series(0.0, index=df.index)
        for fam, col in w_cols.items():
            w = df[col]
            sig = df[f"sig_{fam}"].fillna(0.0)
            num = num + w * sig
            den = den + w.abs()
        conf = (num / den.replace(0.0, np.nan)).fillna(0.0).clip(-100.0, 100.0)
        df["signal_confidence"] = conf
        df["signal_value"] = conf.abs()

        den_safe = den.replace(0.0, np.nan)
        for fam, col in w_cols.items():
            df[f"algo_w_{fam}"] = (df[col] / den_safe).fillna(0.0)
        return df.drop(columns=list(w_cols.values()))


__all__ = ["CombPipeline"]
