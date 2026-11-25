"""Compute layer for analysis.oracle_signals — the hindsight oracle's
decision engine v5 (class-first: OracleDecisionEngine owns the
per-code local-extreme detection, the per-side one-action selection
and the favorable-move grading).

LAYOUT per cudf-dtypes — ONE sanctioned host-numpy per-group boundary
core, NO mid-flow hybrids:

  The whole engine is the per-group boundary core. The pivot test
  needs each bar's CENTERED 5-bar window (a backward-only rolling
  window cannot see the right side — and the strict-vs-left /
  ties-on-right plateau rule needs the sides separately), and the
  one-action selection + the favorable-move grading are chronological
  walks over the candidate index list — the sliding_window_view +
  flatnonzero formulation is plain host numpy. The population is
  tiny (10 codes x ~1.7k bars), so the GPU adds nothing.

  The boundary: the fetch hands in ONE whole-frame (code, date, close)
  frame (cudf-clean — epoch float8 -> datetime64[us] at the fetch
  boundary, closes float64); the engine unwraps each code's date/close
  ONCE via ``host_array`` and stays plain host numpy for the whole
  per-code pass; the decisions materialize back through the REAL
  DataFrame ctor (``pd.DataFrame._fsproxy_slow``) at exit as one
  column-dict of raw host arrays. No proxy ndarray crosses the
  boundary, no label string rides a frame (the action stays its
  int16 'oracle_action' code end-to-end).

The decision grammar v6 (constants in config.py; documented in the
DDL header) — v3.1's detection + selection stay verbatim; v4
(2026-11-19, user-directed "should look future 60d if there are big
rise/drop trend, should tune down action confidence even take no
action" — the 2020-01 small-gap example) added the FORWARD-TREND
GRADING calibrated by temp_scripts/oracle_forward60_study.py
(docs/oracle_forward60_confidence_study_2026-11-19.md); v5 (same day,
user-directed "should consider before and after local min/max rises
and falls" — the 2020-11-09 / 2024-02-23 / 2025-05-14 top examples)
added the BACKWARD LEG calibrated by temp_scripts/
oracle_backward_leg_study.py
(docs/oracle_backward_leg_study_2026-11-19.md); v6 (2026-11-20,
user-directed "2024-08 has small peaks that could be noisy, while
2024-09 has a big drop, so should not trigger buy action but sell
some in advance to prepare more cash to buy in deep in next month")
added the PREPARE-TO-BUY-DEEP RULE calibrated by temp_scripts/
oracle_prepare_deep_study.py (docs/oracle_prepare_deep_study_
2026-11-20.md); v7 (same day, user-directed "the buy in deep rule
should also account for recent rises unless the rises are small,
refactor the buy in deep sell in high logic to take care of recent
and future min/max") widened it into the GENUINE-EXTREME RULE (both
sides x both directions) calibrated by temp_scripts/
oracle_genuine_extreme_study.py (docs/oracle_genuine_extreme_study_
2026-11-20.md); v7.1 (the same day, user-directed "it is still ok to
buy if it is a local deep min despite even in 20d there is a deeper
local min ... the buy deep logic is more about filter small
fluctuation noises; 2024-10-16 should be a buy day") refined the BUY
side to the GENUINE-DEEP-MIN TEST — the drawdown INTO the pivot
decides: a genuine deep min always buys (even with a deeper min
coming within 20d), only shallow wiggles before an imminent deeper
level omit:

  1. LOCAL EXTREME (1-day stride, 5-bar window): every bar i is
     tested over the window [i-2, i+2] (WINDOW_SIDES = 2 each side) —
     LOCAL MIN: close[i] STRICT below the 2 left neighbors (a
     plateau's FIRST bar wins) and no higher than the 2 right
     neighbors; LOCAL MAX mirrored. The motivating case for shrinking
     v2's +-5 window: 931743's 2025-09-24 spike top (5126) failed its
     +-5 window only because the 10-09 higher top (5350) fell exactly
     5 rows later (Golden Week compresses the calendar) — a huge
     local peak left unmarked.
  2. THE 5D ONE-ACTION WINDOW, PER SIDE (v3.1): consecutive actions
     of the SAME side sit >= 5 traded rows apart — any 5-consecutive-
     row window holds at most ONE buy AND at most ONE sell (a buy and
     a sell may sit within the same 5 rows; the v3 either-side window
     suppressed real opposite extremes — 000001's 2025-08-01 local
     min inside the 07-30 sell unit, its 2020-07-22 local max inside
     the 07-16 buy unit, its 2022-03-01 local max inside the 02-24
     buy unit). Selection walks the candidates chronologically with
     one cursor per side: the FIRST candidate of a side's unit claims
     it (its side advances the cursor); a MORE EXTREME same-side
     candidate within the unit's 5 rows UPGRADES the placement to
     that bar (the unit window re-extends from it); everything else
     inside the unit is suppressed. A bar is never both sides (a
     strict local min and local max are mutually exclusive), so the
     (sec_type, code, date) PK cannot collide.
  3. THE FAVORABLE-MOVE GRADING (v4 forward + v5 backward): each
     PLACED pivot is validated by its favorable excursion on EITHER
     side — a BUY by the biggest RISE within the next
     LOOKFORWARD_DAYS (20, user-directed 2026-11-20 "let future look
     only be 20d not 60d" — the 2026-11-19 v4 calibration ran 60)
     traded rows OR the biggest DROP within the
     LOOKBACK_DAYS (20, 2026-11-21 user-directed "also only look
     back 20d not 60d" — the v5 calibration ran 60) rows before it;
     a SELL by the biggest DROP after / biggest RISE before (the max
     favorable excursion on both sides). Each leg grades 0..100 on
     its own LINEAR ramp from CONF_NO_ACTION_MOVE (2%) to
     CONF_FULL_MOVE (10%, clipped); the pivot's CONFIDENCE is the
     WEIGHTED SUM — the pre-action leg at CONF_BACK_WEIGHT (30%),
     the forward leg at CONF_FWD_WEIGHT (70%, user-directed "let
     rises/drops before action date with confidence weighted 30%,
     while future rises/drops weighted 70%") — and the pivot emits
     when its weighted grade is positive:
     EITHER leg above the floor acts, BOTH legs below the floor OMIT
     the row (the oracle takes NO ACTION at that pivot — the 2020-01
     small-gap chop buys measure +0.7%/+1.6% forward and dust
     backward and die; the v5 rescues — 000001's 2020-11-09 top
     (+4.9% in / +1.9% out), its 2024-02-27 top (+11.6% in / +1.9%
     out), its 2025-05-14 top (+9.9% in / +1.9% out) — grade on
     their run-up share). The BACKWARD leg is fully realized the
     moment the pivot is confirmed (it reads only past rows), so a
     frontier pivot with a big run-up/drawdown grades at its 30%
     share now (conf only grows as the tail re-grades); a
     backward-dust UNREALIZED window (< 20 future rows at the
     frontier) still emits confidence NULL — unknown is not small —
     and is re-graded (or dropped) by later runs' daily diff. Omitted
     pivots still OCCUPY their 5-row unit
     (placement precedes grading), and the ext_* legs stay
     PLACED-level: the swing target is the next opposite PLACED pivot
     even when that pivot's own row was omitted — a fact about the
     price path, not about the tradable action.
  4. THE GENUINE-EXTREME RULE (v7, both sides x both directions; the
     horizons UNIFIED at 20, user-directed "why 30? already said
     only 20d future look forward" — every future-reading window in
     the grammar is the same 20 traded rows; the BUY side REFINED to
     the genuine-deep-min test, v7.1 user-directed "it is still ok to
     buy if it is a local deep min despite even in 20d there is a
     deeper local min ... the buy deep logic is more about filter
     small fluctuation noises; 2024-10-16 should be a buy day").
     BUY = the GENUINE-DEEP-MIN TEST: the drawdown INTO the pivot
     from the prior 20 rows' high decides — a PREPARE_DEEP_MARGIN
     (5%)+ drawdown is a GENUINE DEEP MIN and ALWAYS buys, even when
     a deeper local min follows within the 20d horizon (that deeper
     min gets its own buy when it forms; 000001's 2024-10-17
     pullback low, -9.2% off the 10-08 top, is a buy again — the
     v7-era recent-rise kill of it is retired); a shallower min is a
     SMALL FLUCTUATION and omits only when a materially deeper level
     (5%+ below) is imminent within the 20d horizon (000001's
     2024-08-22 chop wiggle before the 09-13 deep) and keeps
     otherwise (08-05/08-14, whose within-20d deepest is dust, stay).
     SELL = a genuine HIGH: the RECENT leg omits a sell sitting
     SELL_HIGH_MARGIN (5%) or more below the prior
     SELL_HIGH_HORIZON_DAYS (20) rows' ceiling (a weak bounce in a
     decline, not a high), and the FUTURE leg omits a sell whose next
     20 rows contain a close 5%+ above (a materially higher exit is
     imminent — the waypoint sells the 924-style melt-up runs
     through; the 2024-08 advance sells stay live — their 20-row
     margins: fall-from-ceiling 2.4..3.8%, coming-higher <= +1.4%).
     The future legs read the
     REALIZED portion only and are MONOTONE at the frontier (a
     partial window already showing the disqualifying level omits
     now; the daily incremental re-decides the rest as the window
     realizes); the recent/dd-into legs are realized at
     placement.
     Suppressed pivots still OCCUPY their 5-row units and still
     anchor later rows' ext_* legs (placement precedes grading).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from analysis.oracle_signals import config
from analysis.oracle_signals.data_definitions import OUT_COLUMNS


# the static.label_dim 'oracle_action' codes (mirrored by _common.labels)
ACTION_BUY = 0    # the local-min side
ACTION_SELL = 1   # the local-max side


def _leg_grade(move: float, floor: float, full: float) -> float:
    """One favorable leg's 0..100 grade — the calibrated LINEAR ramp
    from the no-action floor to the full bar, clipped; a leg below
    the floor grades 0 (v4's curve shape, unchanged)."""
    return min(100.0, max(0.0, 100.0 * (move - floor) / (full - floor)))


class OracleDecisionEngine:
    """Builds the oracle action rows for one sec_type's close frame.

    The frame is the fetch's (code, date, close) output sorted by
    (code, date); ``build()`` returns the OUT_COLUMNS frame of action
    rows (cudf-clean, ready for the write boundary).
    """

    def __init__(self, closes: pd.DataFrame, sec_code: int) -> None:
        self.closes = closes
        self.sec_code = sec_code

    # -- public ----------------------------------------------------------

    def build(self) -> pd.DataFrame:
        """Run the per-code detection + selection + grading pass and
        materialize the output frame (REAL ctor, one boundary pass at
        exit). The frame is the code set's FULL re-decision — the
        incremental write diffs the target against it."""
        col: dict[str, list] = {c: [] for c in OUT_COLUMNS}
        for code, g in self._code_groups():
            rows = self._decide_code(
                host_array(g["date"].to_numpy()),
                host_array(g["close"].to_numpy()).astype("float64"),
            )
            for r in rows:
                col["sec_type"].append(np.int16(self.sec_code))
                col["code"].append(code)
                for name in OUT_COLUMNS[2:]:
                    col[name].append(r[name])
        if not col["code"]:
            return pd.DataFrame({c: [] for c in OUT_COLUMNS})
        return self._materialize(col)

    # -- pieces ----------------------------------------------------------

    def _code_groups(self):
        """Yield (code, group_frame) in (code, date) order — the frame
        arrives sorted from the fetch, so a plain groupby(false-sort)
        keeps the per-code chronological order the pass relies on."""
        yield from self.closes.groupby("code", sort=False)

    def _decide_code(
        self, dates: np.ndarray, closes: np.ndarray,
    ) -> list[dict]:
        """One code's pass: detect the 5-bar-window local extremes
        (1-day stride), select the actions under the 5d per-side
        one-action rule, drop every pivot that is not a genuine
        extreme of its own horizon (v7: the buy's coming-deep /
        recent-rise legs, the sell's weak-bounce / coming-higher
        legs), then grade each surviving pivot by its favorable
        excursion on EITHER side (v4 forward window + v5 backward
        leg). `dates`/`closes` are RAW host arrays (the
        boundary unwrapped once); everything below is plain numpy —
        no proxy dispatch. Returns the kept rows."""
        k = config.WINDOW_SIDES
        span = config.ONE_ACTION_SPAN
        n = closes.shape[0]
        if n < 2 * k + 1:
            return [], None  # not even one full pivot window

        w = np.lib.stride_tricks.sliding_window_view(closes, 2 * k + 1)
        center = w[:, k]                       # close[i] for window i-k
        left_min = w[:, :k].min(axis=1)        # the k left neighbors
        right_min = w[:, k + 1:].min(axis=1)   # the k right neighbors
        left_max = w[:, :k].max(axis=1)
        right_max = w[:, k + 1:].max(axis=1)
        # STRICT vs the left (a plateau's FIRST bar wins), ties on the
        # right — the same convention as v2's study
        is_min = (center < left_min) & (center <= right_min)
        is_max = (center > left_max) & (center >= right_max)

        # candidate event list: (position, action), chronological.
        # sliding_window_view index j covers [j, j+2k] — the candidate
        # is the window CENTER (j + k), so shift the flatnonzero
        # indices (v2's off-by-k bug, fixed 2026-11-19).
        events: list[tuple[int, int]] = (
            [(int(i) + k, ACTION_BUY) for i in np.flatnonzero(is_min)]
            + [(int(i) + k, ACTION_SELL) for i in np.flatnonzero(is_max)]
        )
        events.sort()

        # ---- the 5d one-action selection (PER SIDE, v3.1) ---------------
        # one cursor per side: a side's consecutive actions sit >= span
        # rows apart; the opposite side's candidates inside that window
        # start their OWN unit (both sides may act within the same 5
        # rows)
        placed: list[tuple[int, int]] = []
        next_ok = {ACTION_BUY: -1, ACTION_SELL: -1}
        for seq, (pos, act) in enumerate(events):
            if pos < next_ok[act]:
                continue  # inside this side's previous action's 5d unit
            # the unit's placement starts at the FIRST candidate; a
            # more extreme SAME-side candidate within the unit's 5
            # rows upgrades the placement (the unit window re-extends
            # from it)
            chosen = pos
            while True:
                upgrade = None
                for j in range(seq + 1, len(events)):
                    p2, a2 = events[j]
                    if p2 > chosen + span - 1:
                        break  # outside the unit's 5 rows
                    if a2 != act:
                        continue
                    if ((a2 == ACTION_BUY
                         and closes[p2] < closes[chosen])
                        or (a2 == ACTION_SELL
                            and closes[p2] > closes[chosen])):
                        upgrade = p2
                if upgrade is None:
                    break
                chosen = upgrade
            placed.append((chosen, act))
            next_ok[act] = chosen + span

        # per-side units interleave: a unit's UPGRADED placement can sit
        # AFTER an opposite unit placed from a later event, so the
        # append order is not the position order — sort chronologically
        # before the ext pass (ext_offset must stay >= 1: the swing
        # target is the NEXT placed opposite action)
        placed.sort(key=lambda t: t[0])

        # ---- the favorable-move grading (v4 forward + v5 backward) -----
        # each PLACED pivot is validated by its favorable excursion on
        # EITHER side: a BUY by the biggest rise within the next
        # LOOKFORWARD_DAYS traded rows OR the biggest drop within the
        # LOOKBACK_DAYS (20 — the 2026-11-21 unification, both windows
        # 20) rows before it; a SELL by the biggest drop
        # after / biggest rise before. Each leg grades 0..100 on its
        # own linear ramp (floor -> full, clipped); the pivot's
        # CONFIDENCE is the WEIGHTED SUM — the pre-action leg at
        # CONF_BACK_WEIGHT (30%), the forward leg at CONF_FWD_WEIGHT
        # (70%, user-directed) — and a pivot emits when its weighted
        # grade is positive (either leg above the floor; both legs
        # below → no action, the 2020-01 small-gap chop). The backward
        # leg is fully realized at the pivot (it reads only past
        # rows), so a frontier pivot with a big run-up/drawdown grades
        # at its 30% share now (the tail re-grades upward as the
        # forward realizes — conf only grows); a backward-dust
        # unrealized window (< 20 future rows at the frontier) rides
        # confidence NULL — unknown is not small — re-graded by later
        # runs' tail.
        fwd = config.LOOKFORWARD_DAYS
        lbk = config.LOOKBACK_DAYS
        floor = config.CONF_NO_ACTION_MOVE
        full = config.CONF_FULL_MOVE
        w_back = config.CONF_BACK_WEIGHT
        w_fwd = config.CONF_FWD_WEIGHT
        rows: list[dict] = []
        for seq, (i, action) in enumerate(placed):
            # the swing target: the NEXT placed action of the opposite
            # side (placed-level — an omitted pivot still anchors it)
            ext_pos: int | None = None
            for j, other in placed[seq + 1:]:
                if other != action:
                    ext_pos = j
                    break
            # THE GENUINE-EXTREME RULE (v7, both sides x both
            # directions): each side speaks only at a genuine extreme
            # of its own horizon — the buy at a genuine DEEP, the
            # sell at a genuine HIGH. BUY: the FUTURE leg (v6) omits
            # a buy whose next PREPARE_HORIZON_DAYS traded rows
            # contain a close PREPARE_DEEP_MARGIN+ below (a deeper
            # entry is coming — keep the cash), and the RECENT leg
            # (v7, user-directed "account for recent rises unless
            # the rises are small") omits a buy sitting
            # PREPARE_DEEP_MARGIN+ ABOVE the prior window's floor —
            # a pullback after a rally, not a deep (000001's
            # 2024-10-17 buy +17.2% above the 09-13 floor). SELL:
            # the RECENT leg omits a sell sitting SELL_HIGH_MARGIN+
            # below the prior window's ceiling (a weak bounce, not a
            # high), and the FUTURE leg omits a sell whose next
            # SELL_HIGH_HORIZON_DAYS rows contain a close
            # SELL_HIGH_MARGIN+ above (a materially higher exit is
            # imminent — do not sell this waypoint; H is 20, NOT the
            # buy's 30: the 2024-08 advance sells — the point of the
            # prepare doctrine — are exceeded by the 924 rally at
            # 24-31 rows out and MUST live; measured, not assumed).
            # The future legs read the REALIZED portion only (a
            # partial window already showing the disqualifying
            # level omits now; the daily incremental re-decides
            # the rest as the window realizes); the recent legs
            # are realized at placement (past rows). A
            # suppressed pivot still occupies its 5-row unit and
            # still anchors later rows' ext_* legs (placement
            # precedes grading).
            if action == ACTION_BUY:
                # THE GENUINE-DEEP-MIN TEST (v7.1, user-directed "it
                # is still ok to buy if it is a local deep min despite
                # even in 20d there is a deeper local min ... the buy
                # deep logic is more about filter small fluctuation
                # noises; 2024-10-16 should be a buy day"): the
                # drawdown INTO the pivot from the prior window's high
                # decides. A 5%+ drawdown is a GENUINE DEEP MIN — it
                # always buys, even when a deeper local min follows
                # within the 20d horizon (that deeper min gets its own
                # buy when it forms; the holder averages into the
                # deep). A shallower min is a SMALL FLUCTUATION — the
                # noise filter: it omits only when a materially deeper
                # level (5%+ below) is imminent within the 20d horizon
                # (the 2024-08-22 chop wiggle before the 09-13 deep),
                # and keeps otherwise. The dd-into leg is realized at
                # placement (past rows only); the future leg reads the
                # REALIZED portion and is monotone at the frontier
                # (the daily incremental re-decides).
                hist_hi = closes[max(0, i - config.PREPARE_HORIZON_DAYS):i]
                dd_into = (float(1.0 - closes[i] / hist_hi.max())
                           if hist_hi.size else 0.0)
                if dd_into < config.PREPARE_DEEP_MARGIN:
                    prep_end = min(n, i + 1 + config.PREPARE_HORIZON_DAYS)
                    if (prep_end > i + 1
                            and closes[i + 1:prep_end].min()
                            <= closes[i] * (1.0 - config.PREPARE_DEEP_MARGIN)):
                        continue
            else:
                hi = config.SELL_HIGH_HORIZON_DAYS
                ceil_seg = closes[max(0, i - hi):i]
                if (ceil_seg.size
                        and closes[i] <= ceil_seg.max()
                        * (1.0 - config.SELL_HIGH_MARGIN)):
                    continue
                sell_end = min(n, i + 1 + hi)
                if (sell_end > i + 1
                        and closes[i + 1:sell_end].max()
                        >= closes[i] * (1.0 + config.SELL_HIGH_MARGIN)):
                    continue
            # the BACKWARD leg (v5): the favorable move INTO the
            # extreme over the lookback rows before it (a buy's
            # biggest drop / a sell's biggest rise). A placed pivot
            # always has >= WINDOW_SIDES prior rows (the 5-bar window
            # needs them), so the slice is never empty.
            hist = closes[max(0, i - lbk):i]
            back_mfe = (float(1.0 - closes[i] / hist.max())
                        if action == ACTION_BUY
                        else float(closes[i] / hist.min() - 1.0))
            fut_end = min(n, i + 1 + fwd)
            conf: float | None = None
            if fut_end - i - 1 >= fwd:
                fut = closes[i + 1:fut_end]
                fwd_mfe = (float(fut.max() / closes[i] - 1.0)
                           if action == ACTION_BUY
                           else float(1.0 - fut.min() / closes[i]))
                back_grade = _leg_grade(back_mfe, floor, full)
                fwd_grade = _leg_grade(fwd_mfe, floor, full)
                if back_grade <= 0 and fwd_grade <= 0:
                    continue  # no action: both legs below the floor
                conf = w_back * back_grade + w_fwd * fwd_grade
            elif back_mfe >= floor:
                # frontier: the forward window is unrealized — the
                # known backward leg grades at its own 30% share (the
                # daily incremental re-grades upward when the forward
                # realizes; conf only grows)
                conf = w_back * _leg_grade(back_mfe, floor, full)
            # else: backward-dust frontier pivot — confidence NULL
            # (unknown is not small), re-graded by later runs
            rows.append({
                "date": dates[i],
                "action": action,
                "window_days": span,
                "confidence": conf,
                "ext_date": dates[ext_pos] if ext_pos is not None
                            else None,
                "ext_offset": (ext_pos - i) if ext_pos is not None
                              else None,
                "ext_price": (float(closes[ext_pos])
                              if ext_pos is not None else None),
            })
        if not rows:
            return []
        return rows

    def _materialize(self, col: dict[str, list]) -> pd.DataFrame:
        """The boundary exit: ONE real-ctor materialization of the raw
        host columns (dates as datetime64[ns], the two NULLABLE
        smallint legs as float64->Int64 at the emit boundary — the
        rv_bar_count precedent — the price + confidence legs float64)
        into the OUT_COLUMNS frame."""
        real_pd_df = getattr(pd.DataFrame, "_fsproxy_slow", pd.DataFrame)
        data: dict[str, np.ndarray] = {}
        for name in OUT_COLUMNS:
            raw = col[name]
            if name in ("window_days", "ext_offset"):
                data[name] = np.asarray(
                    [np.nan if v is None else float(v) for v in raw],
                    dtype="float64",
                )
            elif name in ("sec_type", "action"):
                data[name] = np.asarray(raw).astype("int16")
            elif name in ("date", "ext_date"):
                data[name] = np.asarray(
                    [np.datetime64("NaT") if v is None else v
                     for v in raw]).astype("datetime64[ns]")
            elif name == "code":
                data[name] = np.asarray(raw).astype(object)
            else:
                data[name] = np.asarray(
                    [np.nan if v is None else v for v in raw],
                    dtype="float64",
                )
        out = real_pd_df(data, columns=OUT_COLUMNS)
        # the nullable smallint legs: Int64 at the EMIT boundary only
        # (cudf-dtypes rule) — python ints / None survive sanitize
        for name in ("window_days", "ext_offset"):
            out[name] = out[name].astype("Int64")
        return out
