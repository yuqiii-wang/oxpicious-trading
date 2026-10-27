"""The portfolio-fragment writer — the run_family-peeled payload the
orchestrating pair rows and events carry: the per-pair benefit/gate/dd
audit on the RULE rows (_portfolio_rule_params / _portfolio_
performance / _portfolio_sub_type) and each event's member records
(_portfolio_event_members), dispatched to the trio witnesses or the
spread leg members through SELF (the composed class). The subs-spec
grammar (condition / coordination / cell_signals with mapped_signals)
mirrors the 03_portfolio_signal_rules SQL header. The method is MERGED
INTO the signal base class: PortfolioFragmentsMixin is a direct base
of HedgePairTrioEntry."""
from __future__ import annotations

import pandas as pd

from _common.labels import (
    ACTION_CODE,
    ACTION_LABEL,
    PORTFOLIO_SUB_TYPE_LABEL,
    SIGNAL_SUB_TYPE_LABEL,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import RuleEntry

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.benefit import (
    benefit,
    gate_digest,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    HP_PORTFOLIO_SUBS,
    PAIR_K,
    TRIO_WASH_CODE,
    UNIVERSE,
    WEIGHTING,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.grids import (
    combine_lens,
    dd_lens,
    pair_grids,
)


def _tail_side_text(facts: list[tuple[int, int]], buy: bool) -> str:
    """The tail-condition sentence built FROM the actual signal rows'
    own (window, tail_pct) facts — never from the config grids (the
    HARD HOOK, 2026-10-26, user-directed "rule condition text is
    concat from cell signal names and actions"): the text can only
    state the vocabulary of the rows it was built from, so it cannot
    drift from the cell signals the rule maps. CONCISE (2026-10-27,
    user-directed "collapse to rsi(14/10/6) if all these rsi follow
    the same rule conditions, rather than every rsi duplicate the
    description"): the window list collapses over the groups, the
    skeleton is stated ONCE — windows whose surviving tail sets are
    identical share one "< own bottom T% tail" clause; divergent tail
    sets render as one parenthetical per-group mapping instead of
    repeating the skeleton per window."""
    wins: dict[int, set[int]] = {}
    for w, p in facts:
        wins.setdefault(w, set()).add(p)
    ordered = sorted(wins, reverse=True)
    groups: dict[tuple[int, ...], list[int]] = {}
    for w in ordered:
        groups.setdefault(tuple(sorted(wins[w])), []).append(w)
    cmp = "< own bottom" if buy else "> own top"
    all_txt = "/".join(str(w) for w in ordered)
    if len(groups) == 1:
        tails = next(iter(groups))
        return (f"RSI({all_txt}) {cmp} "
                f"{'/'.join(str(p) for p in tails)}% tail")
    parts = " · ".join(
        f"{'/'.join(str(w) for w in ws)}: "
        f"{'/'.join(str(p) for p in tails)}%"
        for tails, ws in groups.items())
    return f"RSI({all_txt}) {cmp} tail ({parts})"


def _bar_side_text(bar_def: dict) -> str:
    """The witness record's bar line, from an ACTUAL bar_def (a mapped
    cell's own, or the row winner's)."""
    days = bar_def.get("window_days", cfg.HP_LEG_BAR_WINDOW_DAYS)
    shift = bar_def.get("shift", 1)
    return (f"trailing {days}d bar · "
            + ("prior day" if shift else "same day"))


def _trio_sub_condition(wins: list[dict]) -> str:
    """The trio rule's own trigger sentence, from the row's WINNER
    picks (bar_def.windows) — the fired bar, not the candidate grid."""
    wins_txt = "/".join(str(w["window"]) for w in wins)
    tail_set = sorted({int(w["tail_pct"]) for w in wins})
    tails_part = (f"tails {tail_set[0]}%" if len(tail_set) == 1 else
                  " · ".join(f"RSI({w['window']}) tail "
                             f"{w['tail_pct']}%" for w in wins))
    return ("leg A + leg B + the broad market in their own "
            f"RSI({wins_txt}) capitulation tails the same day · "
            f"{tails_part}")


def _spread_sub_condition(win: int, tail: int, top: bool) -> str:
    """The spread rules' own trigger sentence, from the row's WINNER
    bar (bar_def.window / .tail_pct)."""
    if top:
        side = ("top tail — SELL leg A (the leader), "
                "BUY leg B (the laggard)")
    else:
        side = ("bottom tail — BUY leg A (the laggard), "
                "SELL leg B (the leader)")
    return ("the pair spread (long leg A / short leg B, inverse-vol "
            f"weights) in its own RSI({win}) {side} · tail {tail}%")


class PortfolioFragmentsMixin(RuleEntry):
    """The portfolio-fragment writer's method. A partial
    HedgePairTrioEntry — never instantiated alone."""

    def _stamp_portfolio_fragments(
        self, rules: list[dict], history: list[dict],
        frame: pd.DataFrame, pair_info: dict[str, dict],
    ) -> None:
        """Attach the run_family-peeled fragments to the orchestrating
        rows: the per-cell dd lens + the per-pair benefit/gate on the
        RULE rows (_portfolio_rule_params / _portfolio_performance)
        and each event's per-curve WITNESS records on the HISTORY rows
        (_portfolio_event_members — the portfolio signal sets its own
        cell-level member records; cell_signals stays empty, every
        member a pair-frame facet). Each orchestrating row additionally
        stamps its PORTFOLIO RULE (_portfolio_sub_type — the 2026-10-25
        sub split: trio wash vs spread, config HP_PORTFOLIO_SUB_TYPES;
        run_family groups the portfolio tables by it) and the events
        carry the same stamp. The rule rows' subs spec also
        carries the STORED RULE CONDITIONS (2026-10-25, user-directed
        "all UI data must be sourced from DB"): the sub's
        plain-language condition line + the per-witness cell_signals
        records the UI parses verbatim. THE HARD HOOK (2026-10-26,
        user-directed "rule condition text is concat from cell signal
        names and actions" — the stored text had drifted from the
        registered cells: it recited the candidate grids while the
        mapped signals showed rsi6 and no 15/20% tails): every text is
        now BUILT FROM the actual emitted rows — a witness record's
        condition states its own mapped registered cells' (window,
        tail) vocabulary and their bar_def's bar line; the sub's
        trigger sentence states the row's own winner bar_def (the
        fired bar, not the scanned grid — the candidate grids stay in
        the coordination block, their documented home)."""
        # per-pair host lookup grids: (dates, trough, dd gauges, the
        # per-curve rsi / bars / breach bools) — the sanctioned
        # boundary (one unwrap per column, python loops over the
        # structural event list only)
        grids = pair_grids(frame)
        # the per-(pair, sub) dd lenses from the events themselves —
        # the PAIR-frame events only (the registered standalone
        # component rows ride component codes outside the pair grids)
        events_by_cell: dict[tuple, list[dict]] = {}
        for row in history:
            if row["code"] not in grids:
                continue
            events_by_cell.setdefault((row["code"], row["signal_sub_type"]),
                                      []).append(row)
        lens = {key: dd_lens(grids[key[0]],
                             [r["date"] for r in rows])
                for key, rows in events_by_cell.items()}
        # each (pair, sub)'s winning cell's RSI window (the emitted
        # bar_def carries it — the witnesses + the stored cell-signal
        # condition records read THAT window; the standalone component
        # rows' rolling_quantile bar_def carries no window key)
        win_window: dict[tuple[str, int], int] = {
            (row["code"], row["signal_sub_type"]):
                int(row["rule_params"]["bar_def"]["window"])
            for row in rules
            if "window" in row["rule_params"].get("bar_def", {})
        }
        # the benefit + gate per pair (full-history drawdown gauges).
        # The sub's STORED RULE CONDITIONS ride the params too (2026-10-25,
        # user-directed "add Rule condition in Rule params ... all UI data
        # must be sourced from DB"): the sub's plain-language condition
        # line + one CELL-SIGNAL condition record per witness curve —
        # the UI parses these verbatim instead of composing prose from
        # the spec fragments (payload conditions, no rule rows behind
        # the records). The cell-signal condition states the pair's
        # WINNER window (the witness masks read it). Each row's
        # fragment carries ITS OWN sub's spec — _write_portfolio_tables
        # merges the subs dicts of a pair's orchestrating rows (the
        # top-level construction is pair-level and identical).
        cell_bar = f"trailing {cfg.HP_LEG_BAR_WINDOW_DAYS}d bar · prior day"
        spread_code = self.sub_type_code("hp_entry_spread")
        exit_code = self.sub_type_code("hp_exit_spread")
        # the per-(code, action) shipped registered sub-type LABELS — the
        # WITNESS records' MAPPED SIGNALS (user-directed 2026-10-25
        # "migrate rsi·witness ... from DB signals to rule params json
        # ... ensure mapping, ok to duplicate the mappings"): each stored
        # cell-signal witness carries the registered mov_rsi sub types
        # this portfolio maps at the witness's OWN curve, action-side
        # aligned (the trio's witnesses are all buy-side washes; the
        # spread's legs split buy/sell by role). Duplication is fine —
        # every witness carries its own list, the same cell may back
        # several witnesses across rules. The SAME rows' structured bar
        # facts (mapped_facts / mapped_bar) are the witness records'
        # CONDITION source — the HARD HOOK (2026-10-26, user-directed
        # "rule condition text is concat from cell signal names and
        # actions"): the text states only the vocabulary of the cells
        # actually registered at that curve+side, never the config
        # grids, so it cannot disagree with the cell signals shown
        # under the rule.
        mapped_txt: dict[tuple[str, int], list[str]] = {}
        mapped_facts: dict[tuple[str, int], list[tuple[int, int]]] = {}
        mapped_bar: dict[tuple[str, int], dict] = {}
        for row in rules:
            rp = row["rule_params"]
            if rp.get("portfolio_role") != "component":
                continue
            key = (row["code"], row["action"])
            mapped_txt.setdefault(key, []).append(
                SIGNAL_SUB_TYPE_LABEL[int(row["signal_sub_type"])])
            bd = rp.get("bar_def", {})
            win = rp.get("window")
            tail = bd.get("tail_pct")
            if isinstance(win, int) and isinstance(tail, (int, float)):
                mapped_facts.setdefault(key, []).append(
                    (int(win), int(tail)))
                mapped_bar.setdefault(key, bd)
        for v in mapped_txt.values():
            v.sort()
        buy_mkt = mapped_txt.get(
            (cfg.HP_MARKET_CODE, ACTION_CODE["buy"]), [])

        def _mapped(code: str, action: int) -> list[str]:
            return mapped_txt.get((code, action), [])

        def _witness_condition(curve: str, action: int,
                               row_win: int | None, row_tail: int | None,
                               buy: bool) -> str:
            """One witness record's condition: the MAPPED registered
            cells' own vocabulary when the curve maps any (the hard
            hook), else the row winner's own bar facts — the grid
            sentence only for a malformed row."""
            facts = mapped_facts.get((curve, action))
            if facts:
                return _tail_side_text(facts, buy=buy)
            if row_win is not None and row_tail is not None:
                return _tail_side_text([(int(row_win), int(row_tail))],
                                       buy=buy)
            return _tail_side_text(
                [(w, p) for w in cfg.HP_LEG_RSI_WINDOWS
                 for p in cfg.HP_LEG_TAIL_PCTS], buy=buy)

        def _witness_bar(curve: str, action: int) -> str:
            bd = mapped_bar.get((curve, action))
            return _bar_side_text(bd) if bd else cell_bar
        # each stored cell-signal record's OWN ACTION (user-directed
        # 2026-10-26 "since cell signal carry action, should also show
        # action on UI") — the LABEL WORD (the JSON is self-describing,
        # the portfolio_sub_type precedent): the trio's witnesses are
        # all buy-side washes, the spread subs' legs split buy/sell by
        # role (the exit flips both)
        buy_word = ACTION_LABEL[ACTION_CODE["buy"]]
        sell_word = ACTION_LABEL[ACTION_CODE["sell"]]
        for row in rules:
            pair = pair_info.get(row["code"])
            if pair is None:
                continue
            sub_code = row["signal_sub_type"]
            # the row's PORTFOLIO RULE (the 2026-10-25 sub split): which
            # portfolio_sub_type this orchestrating sub writes + its
            # rule_params name — the marker is run_family's grouping key
            ps_code, ps_name = HP_PORTFOLIO_SUBS.get(
                sub_code, (TRIO_WASH_CODE, cfg.HP_PORTFOLIO_NAME))
            row["_portfolio_sub_type"] = ps_code
            benefit_d = benefit(grids[row["code"]])
            cell_lens = [v for (c, _s), v in lens.items()
                         if c == row["code"]]
            is_entry = sub_code == spread_code
            is_exit = sub_code == exit_code
            win_w = win_window.get(
                (row["code"], sub_code), cfg.HP_LEG_RSI_WINDOW)
            # the row's OWN winner bar — the sub-condition numbers'
            # source (the hard hook): the emitted sentence states the
            # winner window x tail the cell actually fires on, never
            # the candidate grid (the min() guard below is for a
            # malformed row only — every emitted row carries its
            # winner bar_def)
            row_bar = row["rule_params"].get("bar_def", {})
            row_win = row_bar.get("window")
            row_tail = row_bar.get("tail_pct")
            # the per-pair sub spec: the shared construction + the
            # pair's OWN cell-signal condition records (the witness
            # codes differ per pair)
            if is_entry or is_exit:
                # the per-ROLE conditions mirror with the sub's
                # direction: the ENTRY buys the laggard leg A (its own
                # bottom tail) and sells the leader leg B (its own top
                # tail); the EXIT flips both — sell the leader leg A
                # (top tail), buy the laggard leg B (bottom tail)
                sub_cond = _spread_sub_condition(
                    int(row_win) if row_win is not None else win_w,
                    int(row_tail) if row_tail is not None
                    else min(cfg.HP_LEG_TAIL_PCTS),
                    top=is_exit)
                if is_entry:
                    sub_name = "hp_entry_spread"
                    sub_kind = "spread_rsi_tail"
                    leg_a_sig = {"sub_type": "hp_spread_buy_leg",
                                 "code": pair["leg_a"],
                                 "role": "buy leg (the laggard)",
                                 "action": buy_word,
                                 "condition": _witness_condition(
                                     pair["leg_a"], ACTION_CODE["buy"],
                                     row_win, row_tail, buy=True),
                                 "bar": _witness_bar(
                                     pair["leg_a"], ACTION_CODE["buy"]),
                                 "mapped_signals":
                                     _mapped(pair["leg_a"],
                                             ACTION_CODE["buy"])}
                    leg_b_sig = {"sub_type": "hp_spread_sell_leg",
                                 "code": pair["leg_b"],
                                 "role": "sell leg (the leader)",
                                 "action": sell_word,
                                 "condition": _witness_condition(
                                     pair["leg_b"], ACTION_CODE["sell"],
                                     row_win, row_tail, buy=False),
                                 "bar": _witness_bar(
                                     pair["leg_b"], ACTION_CODE["sell"]),
                                 "mapped_signals":
                                     _mapped(pair["leg_b"],
                                             ACTION_CODE["sell"])}
                else:
                    sub_name = "hp_exit_spread"
                    sub_kind = "spread_rsi_tail_mirror"
                    leg_a_sig = {"sub_type": "hp_spread_sell_leg",
                                 "code": pair["leg_a"],
                                 "role": "sell leg (the leader)",
                                 "action": sell_word,
                                 "condition": _witness_condition(
                                     pair["leg_a"], ACTION_CODE["sell"],
                                     row_win, row_tail, buy=False),
                                 "bar": _witness_bar(
                                     pair["leg_a"], ACTION_CODE["sell"]),
                                 "mapped_signals":
                                     _mapped(pair["leg_a"],
                                             ACTION_CODE["sell"])}
                    leg_b_sig = {"sub_type": "hp_spread_buy_leg",
                                 "code": pair["leg_b"],
                                 "role": "buy leg (the laggard)",
                                 "action": buy_word,
                                 "condition": _witness_condition(
                                     pair["leg_b"], ACTION_CODE["buy"],
                                     row_win, row_tail, buy=True),
                                 "bar": _witness_bar(
                                     pair["leg_b"], ACTION_CODE["buy"]),
                                 "mapped_signals":
                                     _mapped(pair["leg_b"],
                                             ACTION_CODE["buy"])}
                subs_spec = {
                    sub_name: {
                        "axis": f"neg_corr_hedge_pair:{sub_name}",
                        "response": "spread_long_short",
                        "condition": sub_cond,
                        "coordination": {
                            "kind": sub_kind,
                            "witnesses": ["leg_a", "leg_b"],
                            "tail_pcts": list(cfg.HP_LEG_TAIL_PCTS),
                            "rsi_windows": list(cfg.HP_LEG_RSI_WINDOWS),
                        },
                        "cell_signals": [leg_a_sig, leg_b_sig],
                    },
                }
            else:
                # the trio's CELL-SIGNAL records: THREE witness curves —
                # market + both legs, ONE FLAT set (2026-10-26,
                # user-directed "there are RSI(10) · 3 witnesses vs
                # RSI(14) · 3 witnesses, remove them": the per-window
                # keyed duplication is gone — each curve ONE record,
                # its condition the MAPPED registered cells' own
                # vocabulary (the hard hook — ALL windows classify by
                # window in the UI instead); no rule rows behind them
                trio_cells = [
                    {"sub_type": "hp_rsi_witness_mkt",
                     "code": cfg.HP_MARKET_CODE,
                     "role": "market witness",
                     "action": buy_word,
                     "condition": _witness_condition(
                         cfg.HP_MARKET_CODE, ACTION_CODE["buy"],
                         row_win, row_tail, buy=True),
                     "bar": _witness_bar(cfg.HP_MARKET_CODE,
                                         ACTION_CODE["buy"]),
                     "mapped_signals": list(buy_mkt)},
                    {"sub_type": "hp_rsi_witness_leg_a",
                     "code": pair["leg_a"], "role": "leg A",
                     "action": buy_word,
                     "condition": _witness_condition(
                         pair["leg_a"], ACTION_CODE["buy"],
                         row_win, row_tail, buy=True),
                     "bar": _witness_bar(pair["leg_a"],
                                         ACTION_CODE["buy"]),
                     "mapped_signals":
                         _mapped(pair["leg_a"], ACTION_CODE["buy"])},
                    {"sub_type": "hp_rsi_witness_leg_b",
                     "code": pair["leg_b"], "role": "leg B",
                     "action": buy_word,
                     "condition": _witness_condition(
                         pair["leg_b"], ACTION_CODE["buy"],
                         row_win, row_tail, buy=True),
                     "bar": _witness_bar(pair["leg_b"],
                                         ACTION_CODE["buy"]),
                     "mapped_signals":
                         _mapped(pair["leg_b"], ACTION_CODE["buy"])},
                ]
                trio_wins = row_bar.get("windows") or []
                # the malformed-row guard (see above): every emitted
                # trio row carries its bar_def.windows winner picks
                sub_cond = _trio_sub_condition(
                    trio_wins if trio_wins else
                    [{"window": win_w,
                      "tail_pct": min(cfg.HP_LEG_TAIL_PCTS)}])
                subs_spec = {
                    "hp_entry_trio": {
                        "axis": "neg_corr_hedge_pair:hp_entry_trio",
                        "response": "inverse_vol_pair_portfolio",
                        "condition": sub_cond,
                        "coordination": {
                            "kind": "trio_rsi_tail",
                            "witnesses": ["leg_a", "leg_b", "market"],
                            "tail_pcts": list(cfg.HP_LEG_TAIL_PCTS),
                            "rsi_windows": list(cfg.HP_LEG_RSI_WINDOWS),
                        },
                        "cell_signals": trio_cells,
                    },
                }
            row["_portfolio_rule_params"] = {
                "name": ps_name,
                # the rule's portfolio_sub_type LABEL WORD (user-directed
                # "in rule params json should have all" — the JSON is
                # self-describing: name + sub identity + the per-witness
                # mapped_signals; the column holds the code)
                "portfolio_sub_type": PORTFOLIO_SUB_TYPE_LABEL[ps_code],
                "universe": UNIVERSE,
                "pair_weighting": WEIGHTING,
                "episode_merge_days": PAIR_K,
                "gate": {"metric": cfg.PF_GATE_METRIC,
                         "min_pp": cfg.PF_GATE_MIN_PP},
                "confidence": {"factor": cfg.PF_CONF_FACTOR,
                               "unit_pp": cfg.PF_CONF_UNIT_PP,
                               "cap": cfg.PF_CONF_CAP},
                "portfolio": {
                    "legs": [pair["leg_a"], pair["leg_b"]],
                    "market": cfg.HP_MARKET_CODE,
                    "aim": "max_drawdown_reduction",
                    # None on the pinned showcase pair when the offsets
                    # carry no opposite-score reading for it
                    "opposite_score": round(pair["score"], 4)
                    if pair["score"] is not None else None,
                    "offset_rho": round(pair["rho"], 4)
                    if pair["rho"] is not None else None,
                },
                "subs": subs_spec,
            }
            row["_portfolio_performance"] = {
                "benefit": benefit_d,
                "gate": gate_digest(benefit_d),
                "dd": combine_lens(cell_lens),
            }
        # each event's member records — set by the portfolio signal
        # itself: the trio's per-curve witnesses (market + both legs;
        # the event's own window DETECTED — the union cell fires on
        # either window) or the spread subs' one-buy-one-sell leg
        # records at the spread cell's winning window (the exit sub's
        # records flip the roles: sell the leader leg A, buy the
        # laggard leg B). The PAIR-frame events only — the registered
        # standalone component events ride component codes with NO
        # member payload (ordinary history rows; a portfolio event
        # needs its >=2 coordinated members)
        for row in history:
            if row["code"] not in grids:
                continue
            sub_code = row["signal_sub_type"]
            is_spread = sub_code in (spread_code, exit_code)
            # the event's PORTFOLIO RULE (the sub split) — the column
            # stamp on the portfolio event tier
            ps_code, _name = HP_PORTFOLIO_SUBS.get(
                sub_code, (TRIO_WASH_CODE, cfg.HP_PORTFOLIO_NAME))
            row["_portfolio_event_members"] = {
                "portfolio_sub_type": ps_code,
                "member_signals": (
                    self._spread_event_members(
                        row, grids.get(row["code"]),
                        window=win_window.get((row["code"], sub_code)),
                        top_side=sub_code == exit_code)
                    if is_spread else
                    self._event_witnesses(row, grids.get(row["code"]))
                ),
                "cell_signals": [],
            }
