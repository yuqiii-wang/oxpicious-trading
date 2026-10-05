"""Internal roof/floor BANDS step for analyze.mov_ave_spread.

Precomputes the UI shade toggle's roof/floor band edges + per-band
BREACH and BOUNCE analytics into
analysis.mov_ave_spreads_roof_and_floor_bands (LONG: one row per
(sec_type, code, date, period ∈ OHLC_WINDOWS)).

Band edges (same anchors as the sibling roof_and_floor_lines step —
recomputed here via the SAME vectorised sparse-table helper, from the
SAME parent source DataFrame, no second DB round-trip):
  - upper_band_low/high = min/max of high_Wd (1st-half max CLOSE) and
    high_2nd_Wd (2nd anchor's INTRADAY HIGH). The anchor pair is stored
    in DATE order, so the band spans min↔max of the two values — exactly
    the derivation the UI's shade rectangles use.
  - lower_band_low/high = the mirror on low_Wd / low_2nd_Wd.
  NULL when either anchor of the pair is absent.

Event σ (shared by BREACH and BOUNCE): sigma[i] = population std
(ddof=0, the Bollinger convention) of the daily CLOSE CHANGES over
the trailing BANDS_EVENT_SIGMA_WINDOW (20) trading rows ending at
i-1 (shifted 1 row — day i never reads its own move), per (sec_type,
code) and shared by every period; NULL until the full window of
valid diffs exists (no sigma → no event of either family).

BREACH + BOUNCE semantics (ANCHOR-BAND events, redefined 2026-10-02):
row (T, p)'s event columns describe touches/crossings of the row's
OWN band AS THE UI DRAWS IT — the near edge AFTER the shade cut
(the trailing-BANDS_CUT_WINDOW (20) close extremes at T raise a
roof's bottom edge / lower a floor's top edge; when the strict cut
empties the band it retries at the anchor close; still-sub-visible
bands (< BANDS_MIN_HEIGHT_FACTOR of the anchor close) and windows
without both anchors carry NO edge and NO events) — over the row's
window [T-p+1, T]. The UI reads a row's bounce/breach_dates arrays
directly as its marker set when anchored at T (the API serves the
arrays). Day i ∈ [T-p+1, T] is an
  UPPER BOUNCE of row (T, p) iff
      |close[i] - roof_edge(T)| <= sigma[i]   (±1σ touch of the
        drawn edge — an approach that closes just under it counts)
      AND close[i] >= close[i-1]              (local peak of the touch)
      AND close[i+1] < close[i]               (turned back down)
  UPPER BREACH iff
      close[i] > roof_edge(T) + sigma[i] AND close[i-1] <=
      roof_edge(T)                           (a DIRECTIONAL entry
        into the band from OUTSIDE: the prior close sat at/below the
        drawn near edge and the day cleared the +1σ breach line
        upward — an up move by construction. A close already INSIDE
        the band that later clears +1σ never marks: the price got
        there from the wrong side, e.g. a rebound day after a
        breakdown fell into the zone (000001 @ 2021-11-11: the Nov
        breakdown left closes just above the roof bottom edge, so
        the +40 rebound cleared +1σ without ever entering the band —
        not a roof break). Staying beyond is not a new breach, and
        falling back never marks).
  Mirrors on the floor band's drawn top edge: touch zone ±1σ with a
  local trough and close[i+1] > close[i]; breach = close[i-1] >=
  edge with close[i] < edge − sigma[i] — a DOWNWARD entry from above
  the floor. Same-side BOUNCE de-clustering: consecutive bounces of
  one side within a row's window are the same episode unless the
  close DEPARTED the band >= BANDS_BOUNCE_MIN_SEPARATION_SIGMA (3)
  event-σ in the side's REJECTION direction and returned — SIGN-AWARE
  travel measured from the last kept bounce (roof: close[i] −
  min(close[i..j]) >= 3σ[j], a DOWN leg; floor: max(close[i..j]) −
  close[i] >= 3σ[j], an UP leg; greedy walk against the last KEPT
  touch — see _decluster_same_side_bounces). A price gliding along a
  band therefore labels ONE bounce, not one per daily uptick, and an
  excursion in the wrong direction never separates two same-side
  touches (000001 @ 2024-07-03 period 500 previously marked
  Dec-20/26 + Jan-08/10/12 while the index slid through the floor).
  Bounce and breach are mutually exclusive by
  construction (the touch zone caps at edge + sigma; breach is
  strictly beyond). The close[i+1] confirmation means a row's LAST
  day cannot (yet) bounce — incremental runs purge + recompute the
  last existing date so the pending touch lands once its
  confirmation day exists.

count == cardinality(dates) by construction for both families.

Incremental mode (``force=False``): target dates = dates present in the
source identity tables but missing in the bands table (per sec_type),
PLUS each sec_type's LAST existing date (purged first — COPY has no ON
CONFLICT): its bounce flags were written without a confirmation day and
become finalizable once the next day exists. Breach events at day t
depend on the band of t-1 and bounce on the close of t+1 (both
recomputed here from the FULL per-code history), so — as in the lines
step — the full history is computed and filtered to target dates before
the upsert.

Force mode: purge the sec_type's rows (or TRUNCATE when no sec_type),
then recompute everything. Single-code mode (--code): the caller
pre-deleted the code's rows; recompute all its dates.

The *_breach_dates / *_bounce_dates columns are rendered as PostgreSQL
array-literal strings ('{"2024-01-02","2024-01-05"}', '{}' when empty)
at the compute boundary: the CSV COPY writer's minimal quoting
round-trips them into the DATE[] columns (a field containing
commas/quotes is CSV-escaped by pandas, and PostgreSQL's CSV-mode
parser + date[] input reader restore the array). The breach/bounce
count columns render as integer STRINGS for the same
reason as the lines step's days_since_last_high/_low (cudf.pandas
renders Int64 columns as "3.0" via to_numpy, which INTEGER COPY
rejects).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.db_commons import chunked_purge_async
from _common.df_utils import (
    grouped_rolling_agg,
    grouped_shift,
    host_array,
    safe_columns,
)
from analyze.mov_ave_spread._common import (
    null_if_overflow_counted,
    run_table_step,
)
from analyze.mov_ave_spread.config import (
    BANDS_ANALYSIS_NAME,
    BANDS_BOUNCE_MIN_SEPARATION_SIGMA,
    BANDS_COLUMNS,
    BANDS_CUT_WINDOW,
    BANDS_DESCRIPTION,
    BANDS_EDGE_COLUMNS,
    BANDS_EVENT_SIGMA_WINDOW,
    BANDS_MIN_HEIGHT_FACTOR,
    BANDS_TABLE,
    OHLC_WINDOWS,
    bands_wide_columns,
)
from analyze.mov_ave_spread._common.ohlc_vector import (
    compute_group_anchors_all_windows,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Compute helpers
# ---------------------------------------------------------------------------


def _decluster_same_side_bounces(
    bounce: np.ndarray,
    close: np.ndarray,
    sigma: np.ndarray,
    side: int,
) -> np.ndarray:
    """Greedy left-to-right de-clustering of one side's bounce grid (in
    place). Within each anchor row, a candidate bounce day j is the SAME
    EPISODE as the last KEPT bounce day i unless the close DEPARTED the
    band by at least BANDS_BOUNCE_MIN_SEPARATION_SIGMA event-σ in the
    side's rejection direction and came back — SIGN-AWARE travel:

        roof  (side > 0): close[i] − min(close[i..j]) >= 3 × sigma[j]
        floor (side < 0): max(close[i..j]) − close[i] >= 3 × sigma[j]

    (span inclusive of both bounce days; σ = the candidate's own event
    σ — the same σ that governs its ±1σ touch zone). A roof episode is
    reset only by a 3σ DOWN leg from the last kept touch, a floor
    episode only by a 3σ UP leg — an excursion in the WRONG direction
    never separates two same-side touches (a floor re-touch after the
    price merely slid further down is the same episode, since the
    market never rose 3σ away from the floor and returned).

    Suppressed days are dropped (bounce[T, j] = False) and do NOT
    anchor later comparisons — the walk measures every candidate
    against the LAST KEPT bounce, so a cluster of touches separated by
    < 3σ of signed travel collapses to its first member. The excursion
    (not close[j] − close[i]) is the separation measure: a touch after
    a full away-and-back move qualifies even when both bounce days
    close at the same level. Runs only over rows with events;
    host-numpy part of the module's sanctioned event-grid boundary.
    """
    for t in np.flatnonzero(bounce.any(axis=1)):
        days: np.ndarray = np.flatnonzero(bounce[t])
        if days.size < 2:
            continue
        kept: int = days[0]
        for j in days[1:]:
            span: np.ndarray = close[kept : j + 1]
            if side > 0:
                travel = close[kept] - span.min()
            else:
                travel = span.max() - close[kept]
            if travel >= BANDS_BOUNCE_MIN_SEPARATION_SIGMA * sigma[j]:
                kept = j
            else:
                bounce[t, j] = False
    return bounce


def _anchor_band_events(
    close: np.ndarray,
    edge: np.ndarray,
    sigma: np.ndarray,
    w: int,
    side: int,
    dates64: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """ANCHOR-BAND bounce + breach counts + array literals per row.

    Row T's events are measured against row T's CUT-ADJUSTED drawn near
    edge ``edge[T]`` (roof bottom / floor top — the band the UI draws
    for anchor T; NaN where the band is not drawn → no events). Day
    i ∈ [T-w+1, T] is:
      BOUNCE   — a ±1σ TOUCH of the drawn edge (|close[i] - edge[T]| <=
                 sigma[i] — an approach closing just under the edge
                 counts) that is the LOCAL peak/trough of the touch
                 (close[i] >= close[i-1] on the roof, mirrored on the
                 floor) and turned back the next day. Touches of one
                 side are then DE-CLUSTERED, SIGN-AWARE: a candidate
                 is dropped unless the close departed >=
                 BANDS_BOUNCE_MIN_SEPARATION_SIGMA (3) event-σ in the
                 side's rejection direction (DOWN from a roof, UP from
                 a floor) since the last kept bounce of that side (see
                 _decluster_same_side_bounces).
      BREACH   — a DIRECTIONAL entry into the band from OUTSIDE
                 (roof: close[i] > edge[T] + sigma[i] with
                 close[i-1] <= edge[T] — the prior close outside at
                 or below the drawn near edge, so clearing +1σ is an
                 up move; floor mirror: close[i] < edge[T] −
                 sigma[i] with close[i-1] >= edge[T], a down entry
                 from above the floor). A close already inside the
                 band never breaks; staying beyond is not a new
                 breach; falling back never marks.
    Mutually exclusive by construction (the touch zone caps at edge +
    sigma). NaN edges / σ / closes never event (NaN comparisons are
    False in numpy).

    Returns (bounce_counts, bounce_literals, breach_counts,
    breach_literals) — the literals are PostgreSQL DATE[] strings
    ('{"d1","d2"}', '{}' when empty). One (rows T × days i) boolean
    grid per family, O(n²) booleans per call (≈2.5 MB at n=1600 —
    fine for index/ETF histories; a stock-sized universe would need a
    span-banded rewrite); the literal loop then runs only over rows
    with events.

    This is the module's SANCTIONED host boundary: the row×day event
    relation is a 2D broadcast pandas cannot express without the same
    numpy grid, and cuDF has no per-row-column gather. Everything else
    in this module (σ, cut lines, band edges) is whole-frame pandas.
    """
    n: int = len(close)
    bn_counts: np.ndarray = np.zeros(n, dtype=np.int64)
    bn_strs: np.ndarray = np.full(n, "{}", dtype=object)
    br_counts: np.ndarray = np.zeros(n, dtype=np.int64)
    br_strs: np.ndarray = np.full(n, "{}", dtype=object)
    if n < 2:
        return bn_counts, bn_strs, br_counts, br_strs

    prev: np.ndarray = np.full(n, np.nan)
    prev[1:] = close[:-1]
    nxt: np.ndarray = np.full(n, np.nan)
    nxt[:-1] = close[1:]
    t_arr: np.ndarray = np.arange(n, dtype=np.int64)
    i_arr: np.ndarray = np.arange(n, dtype=np.int64)
    win_start: np.ndarray = np.maximum(0, t_arr - w + 1)

    lev: np.ndarray = edge[:, None]          # (T, 1) each row's drawn edge
    c: np.ndarray = close[None, :]           # (1, i)
    p: np.ndarray = prev[None, :]
    sig: np.ndarray = sigma[None, :]
    sig_ok: np.ndarray = (~np.isnan(sigma))[None, :]
    in_span: np.ndarray = (
        (i_arr[None, :] >= win_start[:, None])
        & (i_arr[None, :] <= t_arr[:, None])
    )
    # ±1σ touch zone around the drawn edge (shared by both sides).
    zone: np.ndarray = (c >= lev - sig) & (c <= lev + sig)
    if side > 0:
        local_ext: np.ndarray = c >= p
        rev: np.ndarray = (nxt < close)[None, :]
        # Direction gate: the prior close must sit OUTSIDE the band
        # (at/below the drawn near edge) — p <= lev already implies
        # p <= lev + sig, so a breach day is an up move by construction.
        fresh: np.ndarray = (c > lev + sig) & (p <= lev)
    else:
        local_ext = c <= p
        rev = (nxt > close)[None, :]
        fresh = (c < lev - sig) & (p >= lev)
    bounce: np.ndarray = in_span & sig_ok & zone & local_ext & rev
    bounce = _decluster_same_side_bounces(bounce, close, sigma, side)
    breach: np.ndarray = in_span & sig_ok & fresh

    day_str: np.ndarray = dates64.astype("datetime64[D]").astype(str)
    for counts, strs, mask in (
        (bn_counts, bn_strs, bounce),
        (br_counts, br_strs, breach),
    ):
        counts[:] = mask.sum(axis=1)
        for r in np.flatnonzero(counts > 0):
            parts = day_str[np.flatnonzero(mask[r])]
            strs[r] = '{"' + '","'.join(parts) + '"}'
    return bn_counts, bn_strs, br_counts, br_strs


def compute_bands_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add the per-window band-edge + breach/bounce wide columns to the
    source frame (in place), pandas/cuDF end-to-end.

    Phases:
      A — event σ + the shade CUT lines for ALL groups in ONE grouped
          rolling pass each (grouped diff / ``grouped_rolling_agg`` /
          ``grouped_shift`` — GPU under cudf.pandas):
            sigma = grouped rolling(20, min_periods=20) population σ of
                    the daily close diffs, shifted 1 row (day t never
                    reads its own move);
            cut   = grouped rolling(20, min_periods=1) max/min close —
                    the trailing-20-row close extremes at each row.
      B — per-group anchors (``compute_group_anchors_all_windows``);
          the four anchor-value columns per window are batch-written
          into temp columns (one concat + one .loc write per column —
          cudf.pandas discipline B-A3).
      C — per window, the pair edges (min/max of the two anchors) and
          the CUT-ADJUSTED drawn near edges as index-aligned Series
          ops (where/mask only — GPU elementwise); the pair edges
          become the output wide edge columns directly.
      D — per (group, window, side), the ±1σ touch-bounce /
          fresh-crossing-breach events via ``_anchor_band_events``
          (the module's one sanctioned host boundary), batch-written
          per column.
    """
    if df.empty:
        for col in BANDS_COLUMNS:
            df[col] = pd.Series(dtype="object")
        return df

    grp_keys: list[str] = ["sec_type", "code"]
    cols = set(safe_columns(df))
    for col in ("price", "high", "low"):
        if col in cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # ---- Phase A — event σ + shade cut lines (whole-frame, grouped) ----
    diff = df.groupby(grp_keys, sort=False)["price"].diff()
    sigma_raw = grouped_rolling_agg(
        df.assign(_bn_diff=diff), grp_keys, "_bn_diff",
        BANDS_EVENT_SIGMA_WINDOW,
        min_periods=BANDS_EVENT_SIGMA_WINDOW, agg="std", ddof=0,
        sort=False,
    )
    df["_bn_sigma_raw"] = sigma_raw
    grouped_shift(df, grp_keys, "_bn_sigma_raw",
                  out_names="_bn_sigma", periods=1, sort=False)
    df["_bn_cut_max"] = grouped_rolling_agg(
        df, grp_keys, "price", BANDS_CUT_WINDOW,
        min_periods=1, agg="max", sort=False,
    )
    df["_bn_cut_min"] = grouped_rolling_agg(
        df, grp_keys, "price", BANDS_CUT_WINDOW,
        min_periods=1, agg="min", sort=False,
    )

    # ---- Phase B — anchors per group; temp anchor columns --------------
    anchor_frames: list[pd.DataFrame] = [
        compute_group_anchors_all_windows(
            g[["date", "price", "high", "low"]],
            OHLC_WINDOWS,
        )
        for (_st, _code), g in df.groupby(grp_keys, sort=False)
    ]
    anchor_temp: list[str] = []
    if anchor_frames:
        real_concat = getattr(pd.concat, "_fsproxy_slow", pd.concat)
        all_anchors = real_concat(anchor_frames)
        aidx = all_anchors.index
        for w in OHLC_WINDOWS:
            for src in (f"high_{w}d", f"high_2nd_{w}d",
                        f"low_{w}d", f"low_2nd_{w}d"):
                dst = f"_bn_{src}"
                anchor_temp.append(dst)
                df.loc[aidx, dst] = host_array(all_anchors[src].to_numpy())

    # ---- Phase C — pair + cut-adjusted edges (Series ops per window) ---
    close_s: pd.Series = df["price"]
    cut_max: pd.Series = df["_bn_cut_max"]
    cut_min: pd.Series = df["_bn_cut_min"]
    # Sub-visible threshold per row — max(anchor_close ?? 1, 1) × factor.
    min_h: pd.Series = (
        close_s.fillna(1.0).clip(lower=1.0) * BANDS_MIN_HEIGHT_FACTOR
    )
    edge_temp: list[str] = []
    for w in OHLC_WINDOWS:
        h1: pd.Series = df[f"_bn_high_{w}d"].astype("float64")
        h2: pd.Series = df[f"_bn_high_2nd_{w}d"].astype("float64")
        l1: pd.Series = df[f"_bn_low_{w}d"].astype("float64")
        l2: pd.Series = df[f"_bn_low_2nd_{w}d"].astype("float64")
        # Pair edges: min/max of the two anchors, NaN when EITHER is
        # absent (a one-sided band cannot be drawn).
        both_hi: pd.Series = h1.notna() & h2.notna()
        a1: pd.Series = h1.where(both_hi)
        a2: pd.Series = h2.where(both_hi)
        ub_lo: pd.Series = a1.where(a1 <= a2, a2)
        ub_hi: pd.Series = a1.where(a1 >= a2, a2)
        both_lo: pd.Series = l1.notna() & l2.notna()
        b1: pd.Series = l1.where(both_lo)
        b2: pd.Series = l2.where(both_lo)
        lb_lo: pd.Series = b1.where(b1 <= b2, b2)
        lb_hi: pd.Series = b1.where(b1 >= b2, b2)
        df[f"upper_band_low_{w}d"] = ub_lo
        df[f"upper_band_high_{w}d"] = ub_hi
        df[f"lower_band_low_{w}d"] = lb_lo
        df[f"lower_band_high_{w}d"] = lb_hi

        # CUT-ADJUSTED drawn roof bottom (the UI shade cut, replicated):
        # raised to the trailing-20-row max close, retried at the anchor
        # close when the strict cut empties the band, dropped when still
        # sub-visible or when the anchor pair is absent/equal. The
        # NaN-aware max/min idiom ``x.where(x > y, y)`` yields y when x
        # is NaN and NaN when y is — exactly the numpy maximum/minimum
        # with NaN propagation it replaces (comparisons on NaN compare
        # False, so the .where falls through to the other operand).
        strict_bot: pd.Series = cut_max.where(cut_max > ub_lo, ub_lo)
        bot_fb: pd.Series = close_s.where(close_s > ub_lo, ub_lo)
        bot: pd.Series = bot_fb.where((ub_hi - strict_bot) < min_h, strict_bot)
        roof_drawn: pd.Series = (
            both_hi & (ub_hi != ub_lo) & ((ub_hi - bot) >= min_h)
        )
        strict_top: pd.Series = cut_min.where(cut_min < lb_hi, lb_hi)
        top_fb: pd.Series = close_s.where(close_s < lb_hi, lb_hi)
        top: pd.Series = top_fb.where((strict_top - lb_lo) < min_h, strict_top)
        floor_drawn: pd.Series = (
            both_lo & (lb_hi != lb_lo) & ((top - lb_lo) >= min_h)
        )
        rcol = f"_bn_roof_edge_{w}d"
        fcol = f"_bn_floor_edge_{w}d"
        df[rcol] = bot.where(roof_drawn)
        df[fcol] = top.where(floor_drawn)
        edge_temp.extend((rcol, fcol))

    # ---- Phase D — anchor-band events (sanctioned host grid) -----------
    parts: list[pd.DataFrame] = []
    for (_st, _code), g in df.groupby(grp_keys, sort=False):
        close_np: np.ndarray = host_array(
            g["price"].to_numpy()
        ).astype("float64")
        sigma_np: np.ndarray = host_array(
            g["_bn_sigma"].to_numpy()
        ).astype("float64")
        dates64: np.ndarray = host_array(g["date"].to_numpy())
        out: dict[str, np.ndarray] = {}
        for w in OHLC_WINDOWS:
            roof_edge_np: np.ndarray = host_array(
                g[f"_bn_roof_edge_{w}d"].to_numpy()
            ).astype("float64")
            floor_edge_np: np.ndarray = host_array(
                g[f"_bn_floor_edge_{w}d"].to_numpy()
            ).astype("float64")
            bn_up_c, bn_up_d, br_up_c, br_up_d = _anchor_band_events(
                close_np, roof_edge_np, sigma_np, w, +1, dates64,
            )
            bn_dn_c, bn_dn_d, br_dn_c, br_dn_d = _anchor_band_events(
                close_np, floor_edge_np, sigma_np, w, -1, dates64,
            )
            for stem, arr in (
                (f"upper_band_breach_count_{w}d", br_up_c),
                (f"upper_band_breach_dates_{w}d", br_up_d),
                (f"lower_band_breach_count_{w}d", br_dn_c),
                (f"lower_band_breach_dates_{w}d", br_dn_d),
                (f"upper_band_bounce_count_{w}d", bn_up_c),
                (f"upper_band_bounce_dates_{w}d", bn_up_d),
                (f"lower_band_bounce_count_{w}d", bn_dn_c),
                (f"lower_band_bounce_dates_{w}d", bn_dn_d),
            ):
                out[stem] = arr
        parts.append(pd.DataFrame(out, index=g.index))

    if parts:
        real_concat = getattr(pd.concat, "_fsproxy_slow", pd.concat)
        all_events = real_concat(parts)
        event_cols = safe_columns(all_events)
        eidx = all_events.index
        for ec in event_cols:
            df.loc[eidx, ec] = host_array(all_events[ec].to_numpy())

    # Drop the working columns — only the BANDS wide schema remains.
    df.drop(
        columns=["_bn_sigma_raw", "_bn_sigma", "_bn_cut_max", "_bn_cut_min"]
        + anchor_temp + edge_temp,
        inplace=True,
    )
    return df


def build_bands_long_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Melt the wide per-window bands frame to the long DB schema.

    Mirrors build_ohlc_long_frame: one slice + rename per window, a
    single concat, whole-column overflow guard on the edge columns, and
    the INTEGER render of the breach/bounce counts (float64 → int
    strings — see the module docstring for the CSV COPY rationale). The
    *_breach_dates / *_bounce_dates columns are already array-literal
    STRINGS from the compute step.
    """
    if df.empty:
        return pd.DataFrame(
            columns=["sec_type", "code", "date", "period"] + list(BANDS_COLUMNS)
        )

    base: list[str] = ["sec_type", "code", "date"]
    parts: list[pd.DataFrame] = []
    for w in OHLC_WINDOWS:
        wide = list(bands_wide_columns(w))
        parts.append(
            df[base + wide]
            .rename(columns=dict(zip(wide, BANDS_COLUMNS)))
            .assign(period=w)
        )
    long_df = pd.concat(parts, ignore_index=True)
    long_df = long_df[base + ["period"] + list(BANDS_COLUMNS)]

    nulled: dict[str, int] = {}
    for c in BANDS_EDGE_COLUMNS:
        clean, n = null_if_overflow_counted(long_df[c], max_abs=1e12, scale=6)
        long_df[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total: int = sum(nulled.values())
        per: str = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}")

    # INTEGER render of the breach/bounce counts (CSV COPY discipline —
    # the compute arrays are float64-capable int64).
    for c in (
        "upper_band_breach_count", "lower_band_breach_count",
        "upper_band_bounce_count", "lower_band_bounce_count",
    ):
        vals: np.ndarray = host_array(long_df[c].to_numpy())
        ok: np.ndarray = ~np.isnan(vals)
        strs: np.ndarray = np.full(len(vals), None, dtype=object)
        strs[ok] = vals[ok].astype("int64").astype(str)
        long_df[c] = strs

    return long_df


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__; the
#  skeleton lives in _common.steps.run_table_step)
# ---------------------------------------------------------------------------

async def _detect_bands_last_date(conn, st: str) -> set:
    """detect_fn hook — BOUNCE confirmation reads t+1's close: the LAST
    existing date's rows were written without a confirmation day, so
    purge + recompute them (COPY has no ON CONFLICT — the delete must
    precede the re-insert) once the next day exists. Breach flags are
    unaffected (no lookahead)."""
    last_existing = await conn.fetchval(
        f"SELECT MAX(date) FROM {BANDS_TABLE} WHERE sec_type = $1",
        st,
    )
    if last_existing is None:
        return set()
    n_del_last = await chunked_purge_async(
        conn, BANDS_TABLE,
        where_sql="sec_type = $1 AND date = $2",
        params=(st, last_existing),
    )
    logger.info(f"    -> {st}: purged {n_del_last:,} rows of last date "
          f"{last_existing} for bounce confirmation recompute")
    return {last_existing}


async def run_roof_floor_bands(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the bands pipeline against the source data already loaded by
    the parent mov_ave_spread.

    Mirrors the other steps' mode handling (incremental missing dates +
    the last-date bounce-confirmation recompute via the detect_fn hook /
    force purge / single-code full recompute), computes over the FULL
    per-code history, filters to the target dates, and chunk-CSV-COPYs
    the melted long frame into
    analysis.mov_ave_spreads_roof_and_floor_bands.
    """
    await run_table_step(
        conn, df,
        title="ROOF_AND_FLOOR_BANDS (internal step of mov_ave_spread)",
        tag="b",
        table=BANDS_TABLE,
        analysis_name=BANDS_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_roof_and_floor_bands",
        description=BANDS_DESCRIPTION,
        needed_cols=["sec_type", "code", "date", "price", "high", "low"],
        compute_fn=compute_bands_columns,
        build_df_fn=build_bands_long_frame,
        detect_fn=_detect_bands_last_date,
        force=force,
        pool=pool,
        max_concurrent=max_concurrent,
        sec_type=sec_type,
        code_filter=code_filter,
        label="mov_ave_spreads_roof_and_floor_bands",
    )
