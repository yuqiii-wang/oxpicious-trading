"""Config for analysis.oracle_signals — the hindsight oracle's pivot
constants, run scope and universes (single source; the DDL header under
database/sql/analysis/oracle_signals.sql mirrors the definitions).

The pipeline is STRUCTURED for the security sec_types (etf / index /
stock — each mapped to its own stats.*_basic_stats source below), but
only RUNS for the enabled subset — index for now, per the study-scope
rule (the 10 study indices)."""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
#  Per-sec_type source mapping — each security sec_type's daily-bar table
#  (the oracle reads CLOSES only; the bars table is passed as a fragment
#  arg to the pipeline's SQL views).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SecTypeSources:
    """One security sec_type's source table (fragment name passed to
    the pipeline's SQL views)."""

    bars_table: str


SEC_TYPE_SOURCES: dict[str, SecTypeSources] = {
    "etf": SecTypeSources(bars_table="stats.etf_basic_stats"),
    "index": SecTypeSources(bars_table="stats.index_basic_stats"),
    "stock": SecTypeSources(bars_table="stats.stock_basic_stats"),
}

# The pipeline's run scope — index only for now (oxpicious-dev-conventions
# study scope). Widening = adding sec_type strings here (the universe
# resolver and per-sec_type sources are already wired).
ENABLED_SEC_TYPES: tuple[str, ...] = ("index",)


# ---------------------------------------------------------------------------
#  Universes — which codes each sec_type processes.
# ---------------------------------------------------------------------------
# The 10 study indices (the index run scope per the study-scope rule;
# widening = a config change — the table and code are sec_type-ready).
STUDY_INDEX_CODES: tuple[str, ...] = (
    "000001", "931743", "931160", "931071", "000015", "930708",
    "931994", "931594", "000683", "000991",
)


def universe_for(sec_type: str) -> tuple[str, ...] | None:
    """The sec_type's fixed run universe (None = data-driven — every
    code with bars in the sec_type's own basic-stats table)."""
    return STUDY_INDEX_CODES if sec_type == "index" else None


# ---------------------------------------------------------------------------
#  The decision grammar v6 (2026-11-20; mirrored in the DDL header —
#  keep both current). The v1 gate apparatus (5d/20d hindsight windows,
#  the fluctuation gates, the long-horizon extremum gates, the
#  post-action quiet, the 252d forward-context trail) and v2's +-5
#  (11-bar) pivot windows are RETIRED. v3 (user-directed "for 5d
#  window, update that stride should be 1d but keep the 5d one action
#  window") set the 1-day stride; v3.1 (user-directed "within 5d
#  action window can do both buy and sell") split the one-action
#  window PER SIDE; v4 (user-directed "should look future 60d if there
#  are big rise/drop trend, should tune down action confidence even
#  take no action" — the 2020-01 small-gap example) added the
#  FORWARD-TREND GRADING; v5 (user-directed "should consider before
#  and after local min/max rises and falls" — the 2020-11-09 /
#  2024-02-23 / 2025-05-14 top examples) added the BACKWARD LEG; v6
#  (user-directed "2024-08 has small peaks that could be noisy, while
#  2024-09 has a big drop, so should not trigger buy action but sell
#  some in advance to prepare more cash to buy in deep in next month"
#  — the 2024-08 chop / 2024-09 deep example) added the
#  PREPARE-TO-BUY-DEEP RULE below; v7 (same day, user-directed "the
#  buy in deep rule should also account for recent rises unless the
#  rises are small, refactor the buy in deep sell in high logic to
#  take care of recent and future min/max") widened it into the
#  GENUINE-EXTREME RULE — both sides x both directions.
# ---------------------------------------------------------------------------
# The oracle speaks at LOCAL EXTREMES detected with a 1-DAY STRIDE:
# every bar i is tested as a local extreme over the 5-BAR WINDOW
# centered on it ([i-2, i+2] — WINDOW_SIDES = 2 bars each side;
# STRICT vs the left neighbors so a plateau's FIRST bar wins, ties
# allowed on the right). v2's +-5 window is gone: it suppressed real
# peaks whenever a higher close sat within 5 trading rows on either
# side — the motivating case (2026-11-19 study): 931743's 2025-09-24
# spike top (5126) failed its window ONLY because the 10-09 higher
# top (5350) fell exactly 5 rows later (Golden Week compresses the
# calendar), leaving September's huge local peak unmarked while the
# chart showed a lone 09-04 buy.
#
# THE 5D ONE-ACTION WINDOW, PER SIDE (v3.1; v3 held at most ONE action
# of EITHER side per window, which suppressed real opposite extremes —
# 000001's 2025-08-01 local min inside the 07-30 sell unit, its
# 2020-07-22 local max inside the 07-16 buy unit, its 2022-03-01
# local max inside the 02-24 buy unit): consecutive actions of the
# SAME side sit >= 5 traded rows apart — any 5-consecutive-row window
# holds at most ONE buy AND at most ONE sell (a buy and a sell may
# sit within the same 5 rows). Selection walks the candidate extremes
# chronologically (stride 1) with ONE CURSOR PER SIDE: the FIRST
# candidate of a side's unit claims it; a MORE EXTREME same-side
# candidate within the unit's 5 rows UPGRADES the placement to that
# bar (the unit window then re-extends from it); every other
# candidate inside the unit is suppressed. A bar is never both sides
# (strict local min and local max are mutually exclusive), so the
# (sec_type, code, date) PK cannot collide.
WINDOW_SIDES = 2
ONE_ACTION_SPAN = 5

# ---------------------------------------------------------------------------
#  The FORWARD-TREND GRADING (v4, 2026-11-19 — user-directed "should
#  look future 60d if there are big rise/drop trend, should tune down
#  action confidence even take no action"; calibrated at 60d by
#  temp_scripts/oracle_forward60_study.py, digest
#  docs/oracle_forward60_confidence_study_2026-11-19.md; the window
#  SHORTENED to 20 the same week, 2026-11-20 user-directed "let
#  future look only be 20d not 60d" — matching the v7 sell-future
#  gate's horizon): every PLACED pivot is validated by its FORWARD
#  window — a BUY by the biggest RISE within the next
#  LOOKFORWARD_DAYS traded rows, a SELL by the biggest DROP (the max
#  favorable excursion). The move maps to a 0..100 confidence by a
#  LINEAR ramp from the no-action floor to the full bar (the study's
#  capture A/B: linear >= power-2 on every (floor, full) pair); a
#  move below the floor OMITS the row (the oracle takes no action at
#  that pivot). An UNREALIZED window (fewer than LOOKFORWARD_DAYS
#  future rows at the data frontier) is NOT graded: the row emits
#  with confidence NULL — unknown is not small — and is re-graded (or
#  dropped) by later runs' revisable tail. Bars are SYMMETRIC and
#  FIXED percent moves (user choice): a ±10% index move within the
#  forward window IS the big trend; the floor 2% cuts exactly the
#  2020-01-style small-gap dust (the +0.7%/+1.6% chop buys) while
#  keeping the 2020-02-03 deep min (+11.8% -> conf 100).
# ---------------------------------------------------------------------------
LOOKFORWARD_DAYS = 20
CONF_NO_ACTION_MOVE = 0.02   # fraction: below this the pivot emits nothing
CONF_FULL_MOVE = 0.10        # fraction: at/above this confidence = 100

# ---------------------------------------------------------------------------
#  THE BACKWARD LEG (v5, 2026-11-19 — user-directed "should consider
#  before and after local min/max rises and falls"; calibrated by
#  temp_scripts/oracle_backward_leg_study.py, digest
#  docs/oracle_backward_leg_study_2026-11-19.md): v4 graded each pivot
#  by its FORWARD window only (a sell validated by the drop after it),
#  which omitted real tops whose preceding run-up was the big move —
#  the motivating misses (000001): 2020-11-09 (+4.9% run-up in, only
#  +1.9% drop out), 2024-02-27 (the Feb-2024 rebound top, +11.6% in,
#  +1.9% out), 2025-05-14 (+9.9% in, +1.9% out). v5 mirrors the
#  forward window BACKWARD: the pivot's favorable excursion is
#  measured on EITHER side — a sell by the biggest RISE within the
#  LOOKBACK_DAYS (60) traded rows BEFORE it, a buy by the biggest
#  DROP — and the pivot acts when EITHER leg clears the no-action
#  floor (a realized-forward pivot whose both legs miss the floor
#  omits; the backward leg rescues a real top whose drop fell short).
#
#  THE 30/70 CONFIDENCE WEIGHTS (same day, user-directed "let
#  rises/drops before action date with confidence weighted 30%, while
#  future rises/drops weighted 70%"): each leg grades 0..100 on its
#  own linear ramp (floor -> full, clipped) and the pivot's
#  confidence is the WEIGHTED SUM
#
#      conf = CONF_BACK_WEIGHT * grade(back) + CONF_FWD_WEIGHT * grade(fwd)
#
#  — the future dominates the grade (70%) while the run-up into the
#  pivot contributes 30%; a pivot validated only by its run-up tops
#  out at 30. The frontier: the forward leg is unrealized there, so
#  its 70% share contributes 0 and the known backward leg grades at
#  its own 30% share (conf = CONF_BACK_WEIGHT * grade(back) when
#  grade(back) > 0; the revisable tail re-grades upward as the
#  forward realizes — conf only grows); a backward-dust frontier
#  pivot keeps confidence NULL (unknown is not small). The v4
#  motivating case survives: the 2020-01 small-gap chop's legs are
#  dust on both sides, so its buys still omit.
# ---------------------------------------------------------------------------
LOOKBACK_DAYS = 60
CONF_BACK_WEIGHT = 0.3        # the pre-action leg's confidence share
CONF_FWD_WEIGHT = 0.7         # the forward leg's confidence share

# ---------------------------------------------------------------------------
#  THE GENUINE-EXTREME RULE (v6 2026-11-20 user-directed "should not
#  trigger buy action but sell some in advance to prepare more cash to
#  buy in deep in next month" — the future-deep leg; v7 the same day
#  user-directed "the buy in deep rule should also account for recent
#  rises unless the rises are small, refactor the buy in deep sell in
#  high logic to take care of recent and future min/max" — the full
#  2x2; calibrated by temp_scripts/oracle_prepare_deep_study.py +
#  oracle_genuine_extreme_study.py, digests
#  docs/oracle_prepare_deep_study_2026-11-20.md +
#  docs/oracle_genuine_extreme_study_2026-11-20.md; the HORIZONS
#  UNIFIED at 20 the same day, user-directed "why 30? already said
#  only 20d future look forward" — overriding the v6 study's 30d
#  buy calibration; then v7.1 (the same day, user-directed "it is
#  still ok to buy if it is a local deep min despite even in 20d
#  there is a deeper local min ... the buy deep logic is more about
#  filter small fluctuation noises; 2024-10-16 should be a buy day")
#  REFINED the BUY side): each side speaks only at a genuine extreme
#  of its own horizon — and EVERY future-reading window in the
#  grammar is the same 20 traded rows (the grading's
#  LOOKFORWARD_DAYS, the buy gate's and the sell gate's horizons).
#
#  BUY = the GENUINE-DEEP-MIN TEST — the drawdown INTO the pivot
#  from the prior PREPARE_HORIZON_DAYS rows' high decides:
#  • GENUINE DEEP MIN (dd-into >= PREPARE_DEEP_MARGIN) — ALWAYS
#    buys, even when a deeper local min follows within the 20d
#    horizon (that deeper min gets its own buy when it forms — the
#    oracle accumulates into the deep; the v7-era kills this retires:
#    000001's 2024-10-17 pullback low -9.2% off the 10-08 top was
#    killed for sitting +17.2% above the 09-13 floor — the wrong
#    lens; the user's 2024-10-16 example is this pivot on the codes
#    where it lands 10-16).
#  • SMALL FLUCTUATION (dd-into < margin) — the NOISE FILTER: omits
#    only when a materially deeper level (PREPARE_DEEP_MARGIN below)
#    is imminent within the next PREPARE_HORIZON_DAYS rows (000001's
#    2024-08-22 chop wiggle @2848 saw the 09-13 deep -5.1% below 16
#    rows out), and keeps otherwise (08-05/08-14, whose within-20d
#    deepest is dust, stay).
#
#  SELL = a genuine HIGH (both legs kill):
#  • RECENT — the pivot must sit NEAR the prior window's ceiling: a
#    fall of SELL_HIGH_MARGIN+ from the prior SELL_HIGH_HORIZON_DAYS
#    rows' maximum means the "local max" is a WEAK BOUNCE in a
#    decline, not a high (measured kills at 20x5%: 423 of 1,684
#    v6-kept sells — 000683's 2024 dead-cat bounces 6..12% below
#    their ceilings).
#  • FUTURE — a close SELL_HIGH_MARGIN+ above within the next
#    SELL_HIGH_HORIZON_DAYS rows means a materially HIGHER EXIT is
#    imminent — do not sell this waypoint (measured kills at 20x5%:
#    401 sells — the pre-melt-up bounce tops the 924 rally runs
#    through, e.g. 931160's 2024-09-13 @5113 with +24% coming inside
#    20 rows; the 2024-08 advance sells stay live — their 20-row
#    margins: fall-from-ceiling 2.4..3.8%, coming-higher <= +1.4%).
#
#  The future legs read the REALIZED portion only (monotone at the
#  frontier: a partial window already showing the disqualifying level
#  omits now; the revisable tail — 25 rows, the 20d horizon + the
#  action span — re-decides the rest); the dd-into / recent legs are
#  realized at placement (past rows only). Suppressed pivots still
#  occupy their 5-row units and still anchor later rows' ext_* legs.
# ---------------------------------------------------------------------------
PREPARE_HORIZON_DAYS = 20     # traded rows — the buy gate's horizon (ALL future looks are 20)
PREPARE_DEEP_MARGIN = 0.05    # the deep bar (the dd-into test AND the noise filter)
SELL_HIGH_HORIZON_DAYS = 20   # traded rows — the sell's horizon (both legs)
SELL_HIGH_MARGIN = 0.05       # the high bar (both sell legs)

# A pivot row's hindsight swing target: the NEXT ACTION of the
# OPPOSITE side (a BUY's target is the next placed sell — where the
# hindsight holder exits; a SELL's the next placed buy), carried on
# the ext_* legs. NULL when that opposite action is not placed yet
# (the frontier's last action). The legs stay PLACED-level (v4): they
# point at the next opposite PLACED pivot even when that pivot's own
# row was omitted by the grading floor — the swing is a fact about
# the price path, not about the tradable action.
# The weekly orchestration gate: the scheduled incremental run no-ops
# unless the last build (max created_at on the target table) is at
# least this many calendar days old (the nightly chain fires every
# trading day; the weekly tier's own guard makes off-cadence fires
# no-ops — the builds_monthly month-guard precedent). --force and
# --code bypass the gate (explicit rebuilds).
WEEKLY_GATE_DAYS = 6
