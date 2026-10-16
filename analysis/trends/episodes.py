"""The shared EPISODE ANATOMY (analysis.trends.episodes — the single
source every trend/episode consumer imports from).

An EPISODE is an event, not a day: a cluster of trading days during
which a per-(code, date) condition (a band breach, a tail membership)
holds, tolerating brief exits. Its anatomy:

  pass            a maximal run of CONSECUTIVE in-band days (gap-1
                  continuation stays in the pass).
  first-touch     the FIRST pass of an episode — it begins after more
  pass            than REENTRY_EXIT_DAYS out-of-band traded days (or at
                  the code's start).
  re-entry pass   every later pass of the same episode — the condition
                  re-qualifying after a 1..REENTRY_EXIT_DAYS-day exit
                  (holiday closures / short flickers).
  episode size    the episode's TOTAL trading-day count across all its
                  passes — the population weight (w = 1/size: each
                  episode contributes total weight exactly 1, so a
                  stretch that burrows deep into the tail counts as one
                  event, not forty days).

Also here: EpisodeDetector — the breach-run FIRST-day machinery the
signal rules tier's cell loop runs on (moved verbatim from
analysis.analysis_signals.engines.evaluation, its 2026-10-15 home; the
consolidation into analysis.trends keeps analysis.* free of any
analysis_signals import).

GPU LAYOUT: rsi_tail_columns is whole-frame vectorized on the cudf
proxy — per-code traded-day ordinals (sorted cumcount), ONE grouped
ffill+shift for the prior in-band ordinal, then cumsum-based pass /
episode numbering on the in-band subframe. No per-code loops.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_shift, host_array

# ---------------------------------------------------------------------------
#  Anatomy constants (the single source — the retired
#  analysis.analysis_signals.episodes module's values)
# ---------------------------------------------------------------------------

# A gap of MORE THAN this many out-of-band TRADED days ends the episode
# and the next in-band day starts a fresh FIRST-TOUCH pass.
TYPICAL_ISOLATED_DAYS = 5

# A gap of 1..REENTRY_EXIT_DAYS out-of-band traded days is the SAME
# episode re-qualifying — the returning day opens a RE-ENTRY pass.
REENTRY_EXIT_DAYS = 5

# The tail band edge for rank-ratio banding: the top RSI_TYPICAL_BAND
# and bottom RSI_TYPICAL_BAND fractions of the code's own full-history
# rank distribution (the mov_ave_rsi_ranks percentile axis).
RSI_TYPICAL_BAND = 0.25


# ---------------------------------------------------------------------------
#  EpisodeDetector (breach-run first days — the signal rules tier)
# ---------------------------------------------------------------------------

class EpisodeDetector:
    """Episode FIRST days of the breach runs, with the K-absorb: a run
    only ends after MORE THAN k consecutive out-of-band TRADED days on
    the code's own calendar (holiday closures inside k are the SAME
    episode — the measured re-entry-artifact fix).

    TREND-AWARE mode (``triggers(trend=...)``): the K window merges
    only SAME-trend re-entries — a re-entry (gap >= 2 rows) whose trend
    differs from the trend at the previous breach day starts a FRESH
    episode in the trend it re-entered in; gap-1 continuation days
    never split (one price event). The (pos, trend) pair rides ONE
    groupby cummax packed as pos*8 + trend + 1 (stride 8 > max trend
    code 5; 0 stays the no-prior-breach sentinel).

    CELL-SPLIT mode (``triggers(context=...)``): episode detection runs
    PER (code, context) group — the K-absorb tracks the last breach of
    the row's OWN cell population (gaps still measured on the code's
    traded-day ordinals), so a breach day whose (trend[, regime])
    differs from the episode's earlier days is a FIRST day of its own
    cell's episode even when the breach itself is continuous (the
    2023-07-31 class: a band-walk episode whose first day lands in a
    cell that measured out silently drops the continuation days that
    carried a LIVE cell). A strict superset of both other modes: every
    plain/trend-aware first day stays a first day of its own group."""

    def __init__(self, k: int) -> None:
        self.k = k

    def triggers(self, df: pd.DataFrame, mask: pd.Series,
                 trend: pd.Series | None = None,
                 context: pd.Series | None = None) -> pd.Series:
        """Episode-first-day mask on df's index; ``mask`` marks the
        QUALIFYING breach-condition days. Whole-frame grouped ops: pos
        = per-code traded-day ordinal; the last at-or-before breach
        ordinal rides a groupby cummax; a breach day triggers when it
        sits > k rows past the previous breach (or re-enters in a
        different trend, trend-aware mode / a different cell group,
        cell-split mode)."""
        work = pd.DataFrame(
            {"code": df["code"], "_b": mask.fillna(False).astype("int64")},
            index=df.index,
        )
        work["_pos"] = work.groupby("code").cumcount() + 1
        if context is not None:
            # cell-split: the last-breach tracking runs per (code,
            # context) group — days outside the row's cell population
            # (other cells' breach days, out-of-band days) never feed
            # its cummax, so a gap is measured against the row's OWN
            # population's previous breach
            work["_c"] = context.astype("int64")
            g = ["code", "_c"]
            work["_pb"] = work["_pos"] * work["_b"]
            work["_last"] = work.groupby(g, sort=False)["_pb"].cummax()
            prev = grouped_shift(
                work, g, ["_last"], out_names=["_prev"], periods=1,
                sort=False,
            )["_prev"].fillna(0).astype("int64")
            trig = (work["_b"] == 1) & ((work["_pos"] - prev) > self.k)
            return trig.fillna(False)
        if trend is None:
            work["_pb"] = work["_pos"] * work["_b"]
        else:
            work["_t"] = trend.astype("int64")
            work["_pb"] = (work["_pos"] * 8 + work["_t"] + 1) * work["_b"]
        work["_last"] = work.groupby("code")["_pb"].cummax()
        prev = grouped_shift(
            work, ["code"], ["_last"], out_names=["_prev"], periods=1,
        )["_prev"].fillna(0).astype("int64")
        if trend is None:
            trig = (work["_b"] == 1) & ((work["_pos"] - prev) > self.k)
            return trig.fillna(False)
        prev_pos = (prev - 1) // 8
        prev_trend = prev - 1 - prev_pos * 8
        gap = work["_pos"] - prev_pos
        fresh = (prev == 0) | (gap > self.k) \
            | ((gap >= 2) & (work["_t"] != prev_trend))
        trig = (work["_b"] == 1) & fresh
        return trig.fillna(False)


# ---------------------------------------------------------------------------
#  The tail-episode segmentation (rank-ratio banding + pass anatomy)
# ---------------------------------------------------------------------------

def rsi_tail_columns(base: pd.DataFrame, rank_col: str,
                     band: float = RSI_TYPICAL_BAND) -> tuple[pd.Series,
                                                              pd.Series]:
    """(pass_flag, episode_size) for one window's full-history rank
    column — the RSI reading of the shared episode anatomy.

    Args:
      base: frame sorted by (sec_type, code, date) with a default index,
          carrying the per-(sec_type, code) traded-day ordinal column
          ``_p`` (sorted cumcount) and ``rank_col`` (float64 rank within
          the code's own non-null history, 1 = highest reading, NaN =
          unranked).
      band: the two-sided tail fraction (default RSI_TYPICAL_BAND).

    Returns:
      (pass_flag, episode_size) — full-length float64 HOST numpy arrays
      aligned POSITIONALLY to base's rows (the caller assigns them with
      plain frame column setitem):

        pass_flag    the SIDE-MIRRORED pass population — BOTTOM band:
                     1.0 the isolated first-touch episodes (the
                     capitulation), 0.0 the re-entry passes (flickers /
                     falling knives); TOP band: 1.0 the re-entry passes
                     (the re-test — the double top), 0.0 the isolated
                     first-touch passes (isolated breakouts); NaN
                     out-of-band. Gap-1 continuation days inherit their
                     pass's flag.
        episode_size  the episode's total trading-day count on every
                     in-band day (all passes); NaN out-of-band.

    Whole-frame vectorized: band masks from the rank ratio (rank / the
    code's own max rank), ONE grouped ffill+shift for the prior
    same-side in-band ordinal, cumsum-based pass/episode numbering on
    the in-band subframe; the results land in host numpy at the
    in-band positions (the sanctioned positional-gather boundary — no
    label-based .loc assignment, which probes the proxy Index).
    """
    grp = ["sec_type", "code"]
    keys = [base[c] for c in grp]
    rank = base[rank_col]
    # The code's own ranked-day count = max rank (dense 1..N) — the
    # percentile denominator; unranked codes (all-NaN) stay NaN.
    ratio = rank / rank.groupby(keys, sort=False).transform("max")
    top = ratio.notna() & (ratio <= band)
    bottom = ratio.notna() & (ratio >= 1.0 - band)

    # Full-length HOST float64 arrays (NaN = out-of-band), filled at the
    # in-band positions of each side — plain frame column setitem at the
    # caller (label-based .loc assignment probes the proxy Index and
    # falls back).
    n = len(base)
    flags = np.full(n, np.nan, dtype="float64")
    sizes = np.full(n, np.nan, dtype="float64")
    for side, in_band in (("bottom", bottom), ("top", top)):
        if not in_band.any():
            continue
        # The prior same-side in-band ordinal: mask _p off-band, ffill
        # within the code (carries the last in-band ordinal forward
        # across the exit), then shift 1 — on an in-band day this is
        # the ordinal of the PREVIOUS in-band day of the same side.
        p_side = base["_p"].where(in_band)
        carried = p_side.groupby(keys, sort=False).ffill()
        prev_p = grouped_shift(
            base.assign(_carried=carried), grp, ["_carried"],
            out_names=["_prev_p"], periods=1, sort=False,
        )["_prev_p"]
        # OUT-OF-BAND days between same-side in-band days (pos gap
        # minus 1 — calendar gaps are not rows; validated against the
        # legacy store's episode sizes).
        out_days = base["_p"] - prev_p - 1
        # Pass boundary: a consecutive in-band day (out == 0) continues
        # the pass; ANY exit (out >= 1) opens a new pass.
        pass_start = in_band & (prev_p.isna() | (out_days >= 1))
        # Episode boundary: more than REENTRY_EXIT_DAYS out-of-band
        # traded days (or the code's first same-side in-band day) — a
        # 1..REENTRY_EXIT_DAYS-day exit re-qualifies the SAME episode
        # as a re-entry pass.
        episode_start = in_band & (
            prev_p.isna() | (out_days > TYPICAL_ISOLATED_DAYS)
        )

        # Number the passes/episodes cumulatively per code over the
        # in-band subframe, then count pass starts WITHIN the episode —
        # 1 = the first-touch pass, >= 2 = a re-entry pass. The episode
        # size is the groupby row count per (code, episode), merged
        # back by the group keys (cuDF-native hash join; left order is
        # preserved, so the subframe stays positionally aligned to the
        # in-band row order).
        sub = pd.DataFrame(
            {"sec_type": base.loc[in_band, "sec_type"],
             "code": base.loc[in_band, "code"],
             "_ps": pass_start[in_band].astype("int64"),
             "_es": episode_start[in_band].astype("int64"),
             "_one": in_band[in_band].astype("float64")},
        )
        wkeys = [sub["sec_type"], sub["code"]]
        sub["_eid"] = sub.groupby(wkeys, sort=False)["_es"].cumsum()
        ekeys = [sub["sec_type"], sub["code"], sub["_eid"]]
        # passes so far in THIS episode (inclusive of a pass start on
        # the current day) — the pass ordinal within the episode.
        sub["_pnum"] = sub.groupby(ekeys, sort=False)["_ps"].cumsum()
        ep_size = sub.groupby(
            ekeys, sort=False, as_index=False,
        )["_one"].sum().rename(columns={"_one": "_esize"})
        sub = sub.merge(ep_size, on=["sec_type", "code", "_eid"],
                        how="left")
        first_pass = (sub["_pnum"] == 1).astype("float64")
        flag = first_pass if side == "bottom" else 1.0 - first_pass
        side_pos = np.flatnonzero(host_array(in_band.to_numpy()))
        flags[side_pos] = host_array(flag.to_numpy())
        sizes[side_pos] = host_array(sub["_esize"].to_numpy())
    return flags, sizes
