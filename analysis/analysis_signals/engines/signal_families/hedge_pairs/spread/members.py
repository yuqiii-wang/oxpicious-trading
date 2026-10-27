"""The spread events' leg member records — the cell-level records the
triggered spread portfolio signals set themselves (one per leg at the
event day; payload-only vocabulary, no rule rows behind them). The
method is MERGED INTO the signal base class: SpreadMembersMixin is
composed into HedgePairTrioEntry (via spread.SpreadSub)."""
from __future__ import annotations

from _common.labels import ACTION_CODE

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import RuleEntry
from analysis.analysis_signals.fetch.inputs._hedge_pairs import bar_suffix

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    DEPTH_SCALE,
    SPREAD_MEMBER_SUBS,
    WITNESS_TYPE,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.members import (
    link_confidence,
    locate_date,
)


class SpreadMembersMixin(RuleEntry):
    """The spread event members' method. A partial
    HedgePairTrioEntry — never instantiated alone."""

    def _spread_event_members(self, row: dict, grid: dict | None,
                              window: int | None = None,
                              top_side: bool = False,
                              ) -> list[dict]:
        """The SPREAD subs' one-buy-one-sell member records — SET BY THE
        PORTFOLIO SIGNAL ITSELF (no sourcing): ONE member per leg, each
        at the event day carrying the leg's OWN RSI vs its own adaptive
        bar on the sub's winner window (the leg's breach-run start when
        it sits in its own role-side tail; tail_pct None when it does
        not). The ENTRY (top_side=False): leg A the bought laggard
        (hp_spread_buy_leg), leg B the sold leader (hp_spread_sell_
        leg). The EXIT (top_side=True, the spread at its exhaustion
        tail): the roles flip — leg A the SOLD leader, leg B the
        BOUGHT laggard. No rule rows sit behind these records
        (cell_signals stays empty).

        Each member's confidence LINKS to the portfolio event's own
        basis-point record by the member's role-aligned breach depth
        over the two members' mean (the trio's relative-strength
        link): the buy leg's strength is its bar − rsi, the sell leg's
        its rsi − bar, in DEPTH_SCALE units. A leg at NO tail has a
        non-positive depth — its relative share clamps to 0 and the
        record floors at 1bp (the trade rides the SPREAD's own tail,
        not the leg's; both members always emit — the trade IS both
        legs, and the member invariant needs both codes)."""
        if grid is None:
            return []
        w = window if window is not None else cfg.HP_LEG_RSI_WINDOW
        suf = bar_suffix(w)
        pair = row["code"].split("~")
        # the trade's fixed role directions: the ENTRY buys leg A /
        # sells leg B; the EXIT (the spread's exhaustion tail) mirrors
        # both — sell the leader leg A, buy the laggard leg B
        leg_a_act = ACTION_CODE["sell"] if top_side \
            else ACTION_CODE["buy"]
        leg_b_act = ACTION_CODE["buy"] if top_side \
            else ACTION_CODE["sell"]
        roles = [
            (pair[0], "a",
             SPREAD_MEMBER_SUBS["sell" if top_side else "buy"],
             leg_a_act),
            (pair[1], "b",
             SPREAD_MEMBER_SUBS["buy" if top_side else "sell"],
             leg_b_act),
        ]
        pos = locate_date(grid, row["date"])
        if pos is None:
            return []
        members: list[dict] = []
        depths: list[float] = []
        for code, role, sub_code, act_code in roles:
            # the leg's own role-side tail state at the event day (buy
            # leg: its bottom tail; sell leg: its top tail)
            side = "bb" if act_code == ACTION_CODE["buy"] else "bs"
            bar_side = "b" if act_code == ACTION_CODE["buy"] else "s"
            deepest = None
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS):   # 1 the deepest
                if bool(grid[f"{side}{pct}{suf}_{role}"][pos]):
                    deepest = pct
                    break
            if deepest is None:
                # the leg sits at no tail: the record describes its
                # state at the event day itself (reference = the
                # SHALLOWEST tail's bar — the closest one above),
                # tail_pct None
                start = pos
                bar = float(
                    grid[f"bar_{bar_side}{max(cfg.HP_LEG_TAIL_PCTS)}"
                         f"{suf}_{role}"][start])
            else:
                breach = grid[f"{side}{deepest}{suf}_{role}"]
                start = pos
                while start > 0 and bool(breach[start - 1]):
                    start -= 1
                bar = float(
                    grid[f"bar_{bar_side}{deepest}{suf}_{role}"][start])
            val = float(grid[f"rsi{suf}_{role}"][start])
            members.append({
                "code": code,
                "signal_type": WITNESS_TYPE,
                "signal_sub_type": sub_code,
                "action": act_code,
                "date": str(grid["date_d"][start]),
                "signal": round(val, 6),
                "signal_reference": round(bar, 6),
                "confidence": None,   # stamped below — the strength
                                      # link
                "tail_pct": deepest,
            })
            depth_sign = 1.0 if act_code == ACTION_CODE["buy"] else -1.0
            depths.append((bar - val) * depth_sign / DEPTH_SCALE)
        link_confidence(members, depths, row)
        return members
