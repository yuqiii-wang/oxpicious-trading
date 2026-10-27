"""The trio event's WITNESS records — the cell-level member records
the triggered portfolio signal sets itself (one per curve, each at its
own day; payload-only vocabulary, no rule rows behind them). The
method is MERGED INTO the signal base class: TrioWitnessesMixin is
composed into HedgePairTrioEntry (via trio.TrioSub)."""
from __future__ import annotations

from _common.labels import ACTION_CODE

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import RuleEntry
from analysis.analysis_signals.fetch.inputs._hedge_pairs import bar_suffix

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    DEPTH_SCALE,
    WITNESS_SUBS,
    WITNESS_TYPE,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.members import (
    link_confidence,
    locate_date,
)


class TrioWitnessesMixin(RuleEntry):
    """The trio event witnesses' method. A partial
    HedgePairTrioEntry — never instantiated alone."""

    def _event_witnesses(self, row: dict, grid: dict | None,
                         ) -> list[dict]:
        """The event's cell-level member records — SET BY THE
        PORTFOLIO SIGNAL ITSELF (no sourcing): one WITNESS record per
        curve (market + both legs), each at its OWN day — the start of
        the curve's deepest-alive RSI-tail breach run at the event day
        — carrying that day's RSI vs its own bar and the curve's
        PER-ROLE witness sub type (hp_rsi_witness_mkt / _leg_a /
        _leg_b). The union cell fires on EITHER window's resonance —
        the event's OWN window is DETECTED here (the first window in
        HP_LEG_RSI_WINDOWS whose trio holds at the event day — the
        composition's primary-window precedence) and rides the payload
        (``window``); the breach runs read THAT window's masks. No
        rule rows sit behind these records (cell_signals stays empty).

        Each witness's confidence LINKS to the portfolio event's own
        record by the witness's RELATIVE SIGNAL STRENGTH (2026-10-25,
        user-directed "cell signals under portfolio should also have
        non-zero confidence"): the anchor is the portfolio row's own
        basis-point confidence (the trio cell's depth-scaled record),
        and each witness carries it scaled by its own breach depth
        (its RSI past its own bar at its own day — bar − rsi for the
        buy side, mirrored for the sell) over the coordinated
        witnesses' MEAN depth. The trio's own depth IS the per-curve
        mean on the event day (trio_bar / rsi_mean are the three
        curves' means), so the ratio reads directly as the witness's
        share of the event's average strength; the witnesses sit at
        their OWN days, so the mean here is over the event's own
        witnesses. An average-strength curve carries the portfolio's
        full confidence, a deeper one up to 2x (the depth-cap
        convention), a shallower one proportionally less — never zero
        (every witness is in breach by construction; a razor-thin
        breach on a weak event floors at the 1bp scale minimum)."""
        if grid is None:
            return []
        pair = row["code"].split("~")
        roles = [(cfg.HP_MARKET_CODE, "m"), (pair[0], "a"),
                 (pair[1], "b")]
        side = "bb" if row["action"] == ACTION_CODE["buy"] else "bs"
        bar_side = "b" if row["action"] == ACTION_CODE["buy"] else "s"
        # the depth sign: a buy witness's strength is bar − rsi (the
        # value below its bar); a sell's is rsi − bar
        depth_sign = 1.0 if row["action"] == ACTION_CODE["buy"] else -1.0
        pos = locate_date(grid, row["date"])
        if pos is None:
            return []
        # the event's OWN window: the first window whose trio holds at
        # the event day (the union composition's primary precedence)
        w = None
        for cand_w in cfg.HP_LEG_RSI_WINDOWS:
            cand_suf = bar_suffix(cand_w)
            if any(all(bool(grid[f"bb{pct}{cand_suf}_{role}"][pos])
                       for _c, role in roles)
                   for pct in sorted(cfg.HP_LEG_TAIL_PCTS)):
                w = cand_w
                break
        if w is None:
            return []   # a day no window's trio holds cannot witness
        suf = bar_suffix(w)
        members: list[dict] = []
        depths: list[float] = []
        for code, role in roles:
            deepest = None
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS):   # 1 the deepest
                if bool(grid[f"{side}{pct}{suf}_{role}"][pos]):
                    deepest = pct
                    break
            if deepest is None:
                continue  # a curve not in breach cannot witness
            breach = grid[f"{side}{deepest}{suf}_{role}"]
            start = pos
            while start > 0 and bool(breach[start - 1]):
                start -= 1
            val = float(grid[f"rsi{suf}_{role}"][start])
            bar = float(
                grid[f"bar_{bar_side}{deepest}{suf}_{role}"][start])
            members.append({
                "code": code,
                "signal_type": WITNESS_TYPE,
                "signal_sub_type": WITNESS_SUBS[role],
                "action": row["action"],
                "window": w,
                "date": str(grid["date_d"][start]),
                "signal": round(val, 6),
                "signal_reference": round(bar, 6),
                "confidence": None,   # stamped below — the strength
                                      # link
                "tail_pct": deepest,
            })
            depths.append((bar - val) * depth_sign / DEPTH_SCALE)
        link_confidence(members, depths, row)
        return members
