"""The hp_entry_trio run — the trio resonance measured as ONE union
cell per pair (the both-windows population) under the family's
frequency floor + loose gate, plus the per-window tail pre-selection
that decides each window's at-least bar. The methods are MERGED INTO
the signal base class: TrioEntryMixin is composed into
HedgePairTrioEntry (via trio.TrioSub)."""
from __future__ import annotations

import logging

import pandas as pd

from _common.df_utils import host_array

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.evaluation import cell_stats
from analysis.analysis_signals.engines.rule_base import (
    Candidate,
    RuleEntry,
    eligible_freq_years,
)
from analysis.analysis_signals.fetch.inputs._hedge_pairs import bar_suffix
from analysis.trends.episodes import EpisodeDetector

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    DEPTH_SCALE,
    PAIR_K,
    WEIGHTING,
)

logger = logging.getLogger(__name__)


class TrioEntryMixin(RuleEntry):
    """The trio resonance sub's methods: the union-cell run and the
    per-window tail pre-selection. A partial HedgePairTrioEntry —
    never instantiated alone."""

    def _run_trio(self, frame: pd.DataFrame,
                  ) -> tuple[list[dict], list[dict]]:
        """The trio resonance sub: BOTH RSI windows surviving in ONE
        shipped condition (2026-10-25, user-directed "both rsi10 and
        rsi14 are under the same trio wash rule sub type ... rsi14
        have three cell signals, but also rsi10 another three cell
        signals"). Per window the tail pre-selection runs the
        frequency floor under the loose gate (the deepest at-least
        tail meeting >=1 trigger/yr, the best-dir_ave fallback), and
        the CELL's population is the UNION of the windows' picks — a
        day fires when EITHER window's trio resonates (the primary
        window takes precedence on overlap); the K-absorb merges
        same-wash episodes across windows. The emitted bar_def
        carries BOTH windows' picks (bar_def.windows — the UI's
        trio decoder renders them; the witnesses detect the event's
        own window)."""
        # the per-pair ELIGIBLE YEARS (the frequency floor's PER-YEAR
        # coverage semantics — the shared rule_base helper)
        elig = eligible_freq_years(frame)
        picks: dict[int, dict[str, int]] = {
            w: self._pick_window_tails(frame, w, elig)
            for w in cfg.HP_LEG_RSI_WINDOWS
        }
        # the union population: per row the picked tail's own trio
        # gauges — merge the per-pair picks (the small picks frames
        # materialize via the REAL DataFrame ctor), then where-chain
        # the per-tail columns (cudf-native; the primary window takes
        # precedence where both fire)
        work = frame
        for w in cfg.HP_LEG_RSI_WINDOWS:
            pw = picks[w]
            picks_df = pd.DataFrame({
                "code": list(pw.keys()),
                f"_pick{bar_suffix(w)}": list(pw.values()),
            })
            work = work.merge(picks_df, on="code", how="left")
        w_list = list(cfg.HP_LEG_RSI_WINDOWS)
        masks: dict[int, pd.Series] = {}
        refs: dict[int, pd.Series] = {}
        depths: dict[int, pd.Series] = {}
        vals: dict[int, pd.Series] = {}
        for w in w_list:
            suf = bar_suffix(w)
            pick = work[f"_pick{suf}"]
            m_w = None
            ref_w = None
            d_w = None
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
                trio = (work[f"bb{pct}{suf}_a"] & work[f"bb{pct}{suf}_b"]
                        & work[f"bb{pct}{suf}_m"])
                sel = trio & (pick == pct)
                m_w = sel if m_w is None else (m_w | sel)
                bar_t = work[f"trio_bar_b{pct}{suf}"]
                d_t = (bar_t - work[f"rsi_mean{suf}"]) / DEPTH_SCALE
                ref_w = bar_t.where(sel, ref_w) if ref_w is not None \
                    else bar_t.where(sel)
                d_w = d_t.where(sel, d_w) if d_w is not None \
                    else d_t.where(sel)
            masks[w] = m_w.fillna(False)
            refs[w] = ref_w
            depths[w] = d_w
            vals[w] = work[f"rsi_mean{suf}"]
        mask = masks[w_list[-1]]
        ref = refs[w_list[-1]]
        depth = depths[w_list[-1]]
        val = vals[w_list[-1]]
        for w in reversed(w_list[:-1]):
            mask = masks[w] | mask
            ref = refs[w].where(masks[w], ref)
            depth = depths[w].where(masks[w], depth)
            val = vals[w].where(masks[w], val)
        work["_trio_rsi_mean"] = val
        candidates = [Candidate(
            key="trio",
            mask=mask,
            reference=ref,
            depth=depth,
            # the recorded value is the triggering window's own
            # rsi_mean (rsi14 vs rsi10 means are different scales)
            value_col="_trio_rsi_mean",
            # the coarse def — each emitted row's bar_def is
            # overwritten with its pair's per-window picks below
            params={"bar_def": {"kind": "trio_rsi_tail",
                                "bar_window_days":
                                    cfg.HP_LEG_BAR_WINDOW_DAYS,
                                "shift": 1}},
        )]
        rules, history = self.run_cells(
            work, "hp_entry_trio", "buy", candidates, k=PAIR_K,
            value_col="_trio_rsi_mean", depth_factor=0.5,
            # the 2026-10-25 FREQUENCY FLOOR (user-directed "the
            # signals are too rare, loosen it to at least once per
            # year") + the LOOSE GATE (user-directed "loosen the gate,
            # only obviously bad signals should get rejected"): the
            # union cell is judged under the family's own bars — a
            # candidate is measured out ONLY when its measured mean is
            # negative; the pooled verdict keeps t >= 1 + the year
            # majority with the mean bar at "not negative"
            freq_floor_per_year=cfg.HP_MIN_EVENTS_PER_YEAR,
            gate_min=cfg.HP_GATE_DIR_AVE_MIN,
            pooled_dir_ave_min=cfg.HP_POOLED_DIR_AVE_MIN,
            extra_params={
                "witnesses": ["leg_a", "leg_b", "market"],
                "market": cfg.HP_MARKET_CODE,
                "pair_weighting": WEIGHTING,
            },
        )
        # the per-pair bar_def: BOTH windows' self-picked tails ride
        # the emitted rows (the primary window first)
        for row in rules:
            wins = [{"window": w, "tail_pct": picks[w][row["code"]]}
                    for w in w_list if row["code"] in picks[w]]
            row["rule_params"]["bar_def"] = {
                "kind": "trio_rsi_tail",
                "windows": wins,
                "window": wins[0]["window"] if wins
                else w_list[0],
                "tail_pct": wins[0]["tail_pct"] if wins else None,
                "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
                "shift": 1,
            }
        logger.info(f"[hedge_pairs] hp_entry_trio/buy: {len(rules)} "
                    f"orchestrating cells, {len(history)} events")
        self._check_sell_mirror(frame)
        return rules, history

    def _pick_window_tails(self, frame: pd.DataFrame, w: int,
                           elig: dict[str, list[int]],
                           ) -> dict[str, int]:
        """One window's per-pair tail pre-selection: the DEEPEST
        at-least tail whose episode-first population covers EVERY
        eligible year (>= HP_MIN_EVENTS_PER_YEAR triggers in each —
        the 2026-10-25 per-year coverage semantics; the prior
        horizon-average floor retired the same day: it let events
        cluster while whole years stayed empty) under the loose gate
        (dir_ave >= 0 — only a negative mean is obviously bad), the
        best-dir_ave fallback for pairs no tail carries to the floor.
        The same policy run_cells applies per candidate, scoped to
        THIS window so both windows' picks survive into the union
        cell."""
        suf = bar_suffix(w)
        detector = EpisodeDetector(PAIR_K)
        picks: dict[str, int] = {}
        best: dict[str, tuple[float, int]] = {}
        for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
            mask = (frame[f"bb{pct}{suf}_a"] & frame[f"bb{pct}{suf}_b"]
                    & frame[f"bb{pct}{suf}_m"])
            trig = frame[detector.triggers(frame, mask)]
            if trig.empty:
                continue
            meas = trig[trig["_blend_ok"].fillna(False)]
            counts: dict[str, dict[int, int]] = {}
            t_codes = host_array(trig["code"]).tolist()
            t_years = host_array(trig["date"]) \
                .astype("datetime64[D]").astype("datetime64[Y]") \
                .astype("int64").tolist()
            for i, c in enumerate(t_codes):
                cell_years = counts.setdefault(c, {})
                # datetime64[Y] ints are years SINCE 1970 (the shared
                # +1970 epoch convention, same as rule_base's counting)
                y = int(t_years[i]) + 1970
                cell_years[y] = cell_years.get(y, 0) + 1
            stats = (cell_stats(meas, "buy", ["code"]).set_index("code")
                     if not meas.empty else None)
            for c in counts:
                row = (stats.loc[c]
                       if stats is not None and c in stats.index
                       else None)
                dir_ave = float(row["dir_ave"]) if row is not None \
                    else None
                if dir_ave is None or dir_ave < 0.0:
                    continue     # obviously bad (or unmeasurable)
                if c not in best or dir_ave > best[c][0]:
                    best[c] = (dir_ave, pct)
                if c in picks:
                    continue     # the deepest floor-meeter already won
                years_n = counts[c]
                if all(years_n.get(y, 0) >= cfg.HP_MIN_EVENTS_PER_YEAR
                       for y in elig.get(c, ())):
                    picks[c] = pct
        for c, (_d, pct) in best.items():
            picks.setdefault(c, pct)
        return picks
