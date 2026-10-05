"""Roof/floor SHADE BANDS constants for analyze.mov_ave_spread.

analysis.mov_ave_spreads_roof_and_floor_bands — the UI shade toggle's band
edges + per-band BREACH analytics, precomputed per (sec_type, code, date,
period) from the SAME anchors as the sibling lines table. Internal step of
the parent pipeline (see steps/roof_floor_bands.py).
"""
from analyze.mov_ave_spread.config.ohlc import OHLC_WINDOWS

BANDS_TABLE = "analysis.mov_ave_spreads_roof_and_floor_bands"
BANDS_ANALYSIS_NAME = "mov_ave_spread_bands"

# Event σ window (trading rows): BREACH and BOUNCE are scaled by
# sigma[t] = population std (ddof=0, the Bollinger convention) of the
# daily CLOSE CHANGES (close[r] - close[r-1], price units) over the
# trailing BANDS_EVENT_SIGMA_WINDOW rows ENDING AT t-1 (shifted 1 row —
# day t's classification never reads its own move; NULL until the full
# window of valid diffs exists). 20 = the repo's Bollinger σ window
# (std_20days): the tolerance around a band edge is ONE typical daily
# move of that code. One sigma per (sec_type, code, date) — shared by
# every period so the same price distance classifies identically
# regardless of window. Retune only with a --force rebuild of the
# bands step.
BANDS_EVENT_SIGMA_WINDOW = 20

# Same-side BOUNCE de-clustering, SIGN-AWARE: two consecutive bounces
# of one side (within one anchor row's window) are the SAME episode
# unless the close DEPARTED the band by at least this many event-σ in
# the side's rejection direction and returned. Kept candidate j
# (i = the last KEPT bounce, span inclusive of both days) requires
#   roof:  close[i] − min(close[i..j]) >= value × sigma[j]  (DOWN leg)
#   floor: max(close[i..j]) − close[i] >= value × sigma[j]  (UP leg)
# An excursion in the WRONG direction never separates two same-side
# touches — without it, a price gliding along a band labels every
# down-day + next-day-uptick pair as a "bounce" (000001 @ 2024-07-03,
# period 500: Dec-20/26 + Jan-08/10/12 all marked while the index slid
# through the floor). Retune only with a --force rebuild of the bands
# step.
BANDS_BOUNCE_MIN_SEPARATION_SIGMA = 3.0

# Shade CUT-line window (trading rows) — replicates the UI shade
# toggle's 20d cut (chartOption/roofFloor/shades.ts): the trailing
# BANDS_CUT_WINDOW CLOSE extremes at the anchor raise a roof's bottom
# edge / lower a floor's top edge, with the anchor-close fallback and
# the sub-visible skip (BANDS_MIN_HEIGHT_FACTOR). The bands table's
# stored events are measured against the RESULTING drawn edge, so
# markers exist only where the curve meets the band the chart actually
# draws. Closes are the basis (equal to the Price pair's curve; MA
# pairs' UI cut uses their own curve and may differ slightly).
BANDS_CUT_WINDOW = 20

# Sub-visible band height as a fraction of the anchor close (0.02%) —
# the UI's sliver threshold: a band whose cut height falls below this
# is not drawn (and carries no events).
BANDS_MIN_HEIGHT_FACTOR = 0.0002

# DB schema (LONG format): one row per (sec_type, code, date, period) —
# the per-period columns in table order (after the PK columns). period
# ∈ OHLC_WINDOWS. The compute pipeline keeps the WIDE per-window layout
# (BANDS_WIDE_COLUMNS below) and melts to this long schema only at the
# DB-write boundary (steps/roof_floor_bands.build_bands_long_frame).
BANDS_COLUMNS = (
    "upper_band_low",
    "upper_band_high",
    "lower_band_low",
    "lower_band_high",
    "upper_band_breach_count",
    "upper_band_breach_dates",
    "lower_band_breach_count",
    "lower_band_breach_dates",
    "upper_band_bounce_count",
    "upper_band_bounce_dates",
    "lower_band_bounce_count",
    "lower_band_bounce_dates",
)

# The numeric (NUMERIC(18,6)) band-edge subset of the long schema.
BANDS_EDGE_COLUMNS = (
    "upper_band_low",
    "upper_band_high",
    "lower_band_low",
    "lower_band_high",
)


def bands_wide_columns(w: int) -> tuple:
    """Internal WIDE per-window compute column names for window W (the
    long-schema names with a _{w}d suffix; the *_breach_dates columns
    hold PostgreSQL array-literal STRINGS rendered at the compute
    boundary so the CSV COPY writer can pass them through)."""
    return tuple(f"{c}_{w}d" for c in BANDS_COLUMNS)


BANDS_WIDE_COLUMNS = tuple(
    c for w in OHLC_WINDOWS for c in bands_wide_columns(w)
)

BANDS_DESCRIPTION = (
    "Roof/floor shade-band analysis (ETF + Index + Stock). LONG format: "
    "one row per (security, business date, period) with period ∈ {20, "
    "60, 120, 255, 500, 750, 1275} trading days. Band edges: "
    "upper_band_low/high = min/max of the window's two high anchors "
    "(high_over_period, high_2nd_over_period of the sibling "
    "roof_and_floor_lines table — the 1st is an anchor-date CLOSE, the "
    "2nd an anchor-date INTRADAY HIGH, stored in DATE order hence the "
    "min/max); lower_band_low/high = the mirror on the two low anchors. "
    "Events are scaled by the event σ: sigma[i] = population std "
    "(ddof=0) of the daily close changes over the trailing 20 trading "
    "rows ending at i-1. BREACH/BOUNCE are ANCHOR-BAND events — "
    "measured against the ROW'S OWN band AS DRAWN (the near edge after "
    "the UI shade cut: the trailing-20-row close extremes raise a "
    "roof's bottom / lower a floor's top, with the anchor-close "
    "fallback and the sub-visible skip), over the row's window "
    "[date-period+1, date]: day i is an upper BOUNCE iff |close[i] − "
    "cut_edge(T)| <= sigma[i] AND close[i] >= close[i-1] (local peak) "
    "AND close[i+1] < close[i] — a ±1σ touch of the drawn edge that "
      "turned back — DE-CLUSTERED, SIGN-AWARE: consecutive same-side "
      "bounces need >= 3 event-σ of close travel in the side's "
      "rejection direction between them (DOWN from a roof, UP from a "
      "floor — one bounce per episode, not one per daily uptick); an "
      "upper BREACH iff close[i] > cut_edge(T) + "
      "sigma[i] with close[i-1] <= cut_edge(T) — a DIRECTIONAL entry "
      "into the band from OUTSIDE: the prior close sat at/below the "
      "drawn near edge and the day cleared the +1σ breach line "
      "upward (an up move by construction; a close already inside "
      "the band that later clears +1σ is not a breach). Mirror on "
      "the floor band's drawn top edge: close[i-1] >= cut_edge(T) "
      "with close[i] < cut_edge(T) − sigma[i] — a downward entry "
      "from above the floor. "
      "Mutually exclusive by "
      "construction; count == cardinality(dates). The UI's shade toggle "
    "reads the edges from this table and reads the anchor row's "
    "*_dates arrays directly as its marker set (solid triangle = "
    "bounce, light transparent triangle = breach; no client-side "
    "recomputation). Source: same DataFrame as the mov_ave_spread "
    "parent pipeline (no second DB round-trip). The sec_type column "
    "discriminates the source universe ('etf' | 'index' | 'stock')."
)
