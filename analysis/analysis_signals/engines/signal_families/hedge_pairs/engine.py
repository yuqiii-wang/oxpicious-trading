"""hedge_pairs — the negative-correlation pair family (fresh
2026-10-24; SIMPLIFIED the same day, user-directed: "simple conditions
when three curves resonate and make portfolio signal trigger, the
triggered portfolio signals can set cell level signals — does not need
to source from existing short term tech signals"), the PORTFOLIO
tier's one family.

THE TYPICAL SOLUTION on the trio structure: TWO negatively-correlated
leg curves (the offset-opposite industry composites, greedy-disjoint
by trailing opposite score) + ONE broad-market curve (000300). ONE
shipped sub type, ONE simple condition — the RESONANCE:

  hp_entry_trio (BUY)  all THREE curves sit in their own Wilder-RSI
      capitulation tails the same day (each vs its own per-code
      adaptive percentile bar — the mov_rsi bar convention, computed
      inline by the family's own fetch). The candidates span the
      WINDOW x TAIL grid (2026-10-25, user-directed "also add rsi10
      as trigger as well" then "ensure rsi10 be shown up"):
      HP_LEG_RSI_WINDOWS (10 first — the faster RSI(10) is the
      PRIMARY trigger wherever its candidate meets the floor, its
      pooled evidence measuring as strong as the classic's; 14 — the
      classic slower wash, the fallback) x
      HP_LEG_TAIL_PCTS (1..20% after the widenings — 25/30 measured
      out), AT-LEAST populations per window: the per-curve conditions
      are monotone within a window, so a deeper wash also satisfies a
      shallower bar, and the cell's bar is its winner's tail
      percentile with the deeper days firing inside it (deeper = more
      confidence via depth). The per-cell winner is run_cells'
      FREQUENCY-FLOOR policy (HP_MIN_EVENTS_PER_YEAR = 1.0,
      user-directed "the signals are too rare, loosen it to at least
      once per year") under the LOOSE GATE (HP_GATE_DIR_AVE_MIN /
      HP_POOLED_DIR_AVE_MIN = 0, user-directed "loosen the gate, only
      obviously bad signals should get rejected"): each pair's bar =
      the FIRST gate-passing candidate in the window-major,
      deepest-first order meeting >=1 trigger/yr over the pair's own
      horizon; a candidate is measured out ONLY when its measured
      forward mean is negative (the pooled verdict keeps t >= 1 + the
      expanding-year majority). The winner's tail + window ride
      rule_params.bar_def.tail_pct / .window. The market-wide wash is
      bought THROUGH the pair: the inverse-vol long-long construction
      is the hedge that makes the rebound's drawdown shallower than
      either leg's own — the audited max-drawdown-reduction benefit
      (history_portfolio_signal_performance). The recorded value is
      the winning window's rsi_mean (the three curves' mean RSI) vs
      the trio bar mean.

  the cell-level signals  the triggered portfolio signal SETS them
      itself: each event's member records are the family's own
      per-curve WITNESS records (the per-ROLE payload labels
      hp_rsi_witness_mkt / _leg_a / _leg_b — 2026-10-25; no rule rows
      behind them; hp_rsi_witness stays for old-history decode), one
      per curve, each at its
      OWN day (the start of the curve's deepest-alive breach run) —
      no sourcing from the standalone mov_rsi tier, no component-code
      rule rows at all. Each witness's confidence LINKS to the
      portfolio event's own basis-point record by the witness's
      RELATIVE SIGNAL STRENGTH (its own breach depth over the
      coordinated witnesses' mean depth, capped 2x — see
      trio/witnesses.py). The cell_signals linkage array stays empty
      (every member is a pair-frame facet — the documented 06 case).

  the sell mirror   all three curves at their RSI exhaustion tails —
      the blow-off — runs as the MEASURED ASYMMETRY CHECK
      (trio/mirror.py) and emits NOTHING: measured 2026-10-24 the
      mirror is structurally thin exit timing (27 cells / 33 events /
      +1.90% vs the buy side's 48 / 120 / +4.82%) — coordinated tops
      CONTINUE. The hp_exit_trio label (5829) stays registered; no
      writer emits it.

  hp_entry_spread (BUY, 5834 — 2026-10-25, user-directed "one buy one
      sell by the spread between two negative corr curves,
      disregarding the benchmark curve"; study temp_scripts/
      spread_pair_study.py): the pair's long-short SPREAD curve
      (sr = w_a*r_a - w_b*r_b compounded — the fetch's own lagged
      inverse-vol weights, leg A long / leg B short, NO market curve)
      in its own Wilder-RSI(14/10) BOTTOM tail vs its own adaptive
      percentile bar — the classic pairs CONVERGENCE trade: BUY the
      laggard leg A, SELL the leader leg B. Response axis = the
      spread value's forward blend (the long-A/short-B PnL — the
      family declares the spread in FORWARD_PRICE_COLS). The measured
      record: the RAW population is NEGATIVE (pooled -0.08..-0.17%
      across every window x tail — the 2026-10-20 ratio-z negative
      reproduces on the RSI axis), the edge lives in the per-pair
      WINNERS the self-adaptive gate picks (25/48 pairs, pooled
      +0.55% t=4.70 hit 61% 5/6 years — the rules-era cell mode
      verbatim); the winner is run_cells' FREQUENCY-FLOOR policy
      (HP_MIN_EVENTS_PER_YEAR — the trio's convention, since the
      2026-10-25 evening the PER-YEAR COVERAGE semantics: >= floor
      triggers in EVERY eligible calendar year, the horizon AVERAGE
      retired — it left 21/44 spread winners with a whole empty year)
      over the same WINDOW x TAIL AT-LEAST grid. The top-tail
      CONTINUATION (buy the leader — +0.46% t=6.15 6/6 years over its
      winners) stays the honest positive recorded for a future sub,
      NOT shipped. Each event's member records are set by the
      triggered signal
      itself: ONE member per leg at the event day — leg A the bought
      laggard (hp_spread_buy_leg 5835), leg B the sold leader
      (hp_spread_sell_leg 5836) — carrying the leg's OWN RSI vs its
      own bar on the sub's winner window (its breach-run start when
      the leg sits in its own tail), confidence linked by the leg's
      role-aligned breach depth over the two members' mean (the
      trio's relative-strength link; a leg at no tail floors at 1bp
      — the trade rides the spread's own tail, not the leg's).

  hp_exit_spread (SELL, 5837 — 2026-10-25 evening, user-directed
      "2024-04-18 can also be a good event date to pick, re-study the
      rule why failed, and fix it"; studies temp_scripts/
      diag_spread_20240418.py + spread_peryear_study.py): the TOP-TAIL
      EXIT — the same long-short SPREAD curve at its RSI EXHAUSTION
      tail (the rotation blow-off): SELL the leader leg A / BUY the
      laggard leg B, the one-sell-one-buy MIRROR of the entry (exit
      timing — never pooled-gated, the equity asymmetry). The
      diagnosis that prompted it: the bottom-only era could never
      fire the rotation blow-offs — the showcase pair's 2024-04-16..19
      dividend-over-tech exhaustion (srsi10 75..82 past its own top
      t5/t2 bars, the spread peaking exactly 2024-04-18/19 then
      rolling over) was measured by the discarded mirror check and
      thrown away, while the same days' DILUTE bottom washes (t15/
      t20 depth only) sat below the deep avg-floor winners of the
      washed pairs. The measured record under the per-year floor
      policy: 44 winners / 850 events, pooled +0.20% t=3.41 hit 56%
      6/6 measurable years (the raw-grid negative stands as the
      honest background — relative strength persists; the edge lives
      in the per-pair winners, the entry's evidence structure
      verbatim). The members mirror the entry's roles: leg A the
      SOLD leader (hp_spread_sell_leg), leg B the BOUGHT laggard
      (hp_spread_buy_leg), each read at its own role-side tail.

The orchestrating rows (signal_type 7 at the 'A~B' pair codes, pooled
both axes) carry the portfolio fragments run_family peels into the
portfolio tables: _portfolio_rule_params (the construction),
_portfolio_performance (the benefit audit + gate digest + dd lens),
_portfolio_event_members (the witness records).

THE CONDITION HARD HOOK (2026-10-26, user-directed "rule condition
text is concat from cell signal names and actions"): the stored
condition texts are BUILT FROM the actual emitted rows, never the
config grids — the sub's trigger sentence from the row's own winner
bar_def, each witness record's condition from its mapped registered
cells' own (window, tail) vocabulary (fragments.py builders) — so the
text cannot drift from the cell signals the rule maps (the drift it
fixes: the old grid-recited text claimed RSI(10/14) x tails 1..20
while the mapped signals showed rsi6 and no 15/20%).

The PORTFOLIO SUB SPLIT (2026-10-25, user-directed "add
portfolio_sub_type in portfolio_signal_rules, then migrate trio wash
and spread to as portfolio_sub_type"): each orchestrating sub maps to
ONE portfolio rule — hp_entry_trio -> trio_wash, the spread subs ->
spread (config HP_PORTFOLIO_SUB_TYPES) — and the pair carries SEPARATE
portfolio_signal_rules rows per rule, each with its OWN ids array,
per-sub coordination spec and audit. The REGISTERED mov_rsi cells
(the is_in_portfolio TRUE tier — real signal_rules rows, a SEPARATE
row from any standalone twin because is_in_portfolio is a PK member
there) never sit IN the portfolio signals: each portfolio rule MAPS
them through its signal_rule_ids only (user-directed "rsi signals
themselves should not sit in portfolio signals — by portfolio
signal_rule_ids to map them"): the shared MARKET cells ride every
pair's trio_wash row only (the spread disregards the benchmark curve),
a LEG cell rides its own pair's trio_wash AND spread rows (the legs
witness both rules).

LAYOUT: this module is the family ENTRY only (the registry class + the
fetch/wipe/run orchestration). The sub behaviour is IMPLEMENTED AS
MIXINS MERGED INTO this class — trio/ (hp_entry_trio + the mirror
check + the event witnesses, composed as TrioSub), spread/
(hp_entry_spread / hp_exit_spread + the leg members, SpreadSub),
standalone/ (the registered mov_rsi cells, StandaloneSub) and
fragments.py (the portfolio-fragment writer, PortfolioFragmentsMixin)
— over the shared _commons/ (constants, bar_def builders, pair grids
+ dd lenses, benefit/gate audit, member-record helpers). Every run
step is a method of HedgePairTrioEntry; _commons stays leaf-level
free helpers.
"""
from __future__ import annotations

import pandas as pd

from _common.labels import SIGNAL_TYPE_CODE

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import RuleEntry
from analysis.analysis_signals.fetch.inputs._hedge_pairs import (
    fetch_hedge_pair_inputs,
    resolve_hedge_pair_universe,
)

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    HP_TYPE,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.fragments import (
    PortfolioFragmentsMixin,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.spread import (
    SpreadSub,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.standalone import (
    StandaloneSub,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.trio import (
    TrioSub,
)


class HedgePairTrioEntry(TrioSub, SpreadSub, StandaloneSub,
                         PortfolioFragmentsMixin, RuleEntry):
    """The pair-native trio entry — the family's ONE registry entry:
    it owns the fetch, the single wipe scope and the whole shipped
    vocabulary (the orchestrating trio cells at the 'A~B' codes). The
    portfolio signal's member records are set by the event itself
    (per-curve witnesses), never sourced from another family's tier.
    The blow-off mirror runs as the measured asymmetry check and
    emits nothing (rows discarded — see trio/mirror.py)."""

    FAMILY = "hedge_pairs"
    PACKAGE = "hedge_pairs"
    # THREE shipped sub types: the trio co-wash (the market-wide wash
    # bought through the pair) + the SPREAD convergence entry (2026-10-25,
    # user-directed one-buy-one-sell on the two curves' spread, no
    # benchmark) + the SPREAD top-tail EXIT (the same day, user-directed
    # "2024-04-18 can also be a good event date to pick" — the showcase
    # pair's mid-April-2024 blow-off top, previously only the discarded
    # mirror check). The trio blow-off mirror stays the measured
    # asymmetry check (never shipped — coordinated tops continue).
    SUB_TYPES = ("hp_entry_trio", "hp_entry_spread", "hp_exit_spread")
    REGIME_FREE = True
    USES_TREND_SPLIT = False
    WRITES_TREND_DAYS = False        # composite pair codes are not
                                     # securities
    OHLCV_BOOST = False
    # the TWO response axes: the pair VALUE (the trio's cells — the
    # inverse-vol long-long portfolio) and the long-short SPREAD curve
    # (the spread sub's cells — the long-A/short-B PnL); prepare_frame
    # attaches each one's forward-blend set
    FORWARD_PRICE_COLS = ("price", "spread")

    def __init__(self, conn, sec_type, codes, boost: bool | None = None):
        super().__init__(conn, sec_type, codes, boost=boost)
        self._pairs: list[dict] = []
        self._comp: pd.DataFrame | None = None

    async def fetch_frame(self) -> pd.DataFrame:
        self._pairs = await resolve_hedge_pair_universe(self.conn)
        pair_frame, comp = await fetch_hedge_pair_inputs(
            self.conn, self._pairs,
        )
        # the COMPONENT frame stashed for the registered standalone
        # cells (the mov_rsi grid measured on the market + legs)
        self._comp = comp
        return pair_frame

    def rule_wipe_scopes(self) -> list[tuple[int, list[str], bool | None]]:
        """The family's scopes: its own type at the pair codes, both
        tiers (the composite scopes own their codes outright) PLUS the
        REGISTERED STANDALONE cells' tier — the mov_rsi type at the
        component codes, is_in_portfolio TRUE only (the sourced layer's
        own tier; the standalone mov_rsi runs' FALSE tier at their own
        codes is never touched)."""
        comp_codes = sorted(
            {cfg.HP_MARKET_CODE}
            | {p["leg_a"] for p in self._pairs}
            | {p["leg_b"] for p in self._pairs},
        )
        return [
            (HP_TYPE, self.codes, None),
            (SIGNAL_TYPE_CODE["mov_rsi"], comp_codes, True),
        ]

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        pair_info = {p["code"]: p for p in self._pairs}
        # the subs, in the shipped order: the trio co-wash (+ the
        # measured blow-off mirror check inside it), the spread
        # convergence entry, the spread top-tail exit; then the
        # registered standalone mov_rsi cells (the sourced layer) and
        # the portfolio fragments on the orchestrating rows
        trio_rules, trio_history = self._run_trio(frame)
        rules += trio_rules
        history += trio_history
        entry_rules, entry_history = self._run_spread_entry(frame)
        rules += entry_rules
        history += entry_history
        exit_rules, exit_history = self._run_spread_exit(frame)
        rules += exit_rules
        history += exit_history
        s_cells, s_events = self._register_standalone()
        rules += s_cells
        history += s_events
        self._stamp_portfolio_fragments(rules, history, frame, pair_info)
        return rules, history
