"""ma_cross — the price-vs-MA event family (analysis.analysis_signals.
engines.signal_families.ma_cross.engine).

NOT a golden cross / death cross (published MA-over-MA backtests run
mixed-to-negative): "cross" = PRICE crossing back over a SINGLE MA,
and every kind is conditioned and gated so the published criticisms
are answered by construction. Three entries share one fetch (the
mov_ave_spreads_detail gap columns ARE the values):

  MaCrossReclaimEntry  (buy)  — the first close back ABOVE the MA
      (ma20_cross / ma60_cross), conditioned on the CROSS EXCESS: the
      signed discount at the last below-day. The engine picks the
      per-(code, trend) excess bar from the candidate grid
      {1, 2, 3}% — the SELF-ADAPTIVE part (a bottom-trend reclaim
      needs less discount than a falling-knife fall trend).
  MaRetestEntry        (sell) — an MA breakdown closing a re-test
      rally (ma20_retest): a fresh above-episode whose previous
      episode ended within 20 traded days — the failed rally / double
      top; the SELL is exit timing for longs, never a short entry.
  MaGdepthEntry        (buy ONLY) — the raw-gap band breach below the
      MA at {3, 5, 8}% (ma120_gap / ma255_gap), the boll_std
      band-breach architecture on the raw gap axis. NO top side: deep
      gaps ABOVE the MA measured uniformly negative (momentum — the
      equity asymmetry).

REGIME-FREE (per-regime cells measured thin and sign-flippy): every
row stamps the pooled regime 4; trend remains a split.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.trends import REENTRY_EXIT_DAYS
from analysis.analysis_signals.fetch.inputs import fetch_ma_inputs


class _MaCrossBase(RuleEntry):
    """The shared fetch + frame helpers for the ma_cross entries."""

    FAMILY = "ma_cross"
    PACKAGE = "ma_cross"
    REGIME_FREE = True
    USES_TREND_SPLIT = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_ma_inputs(self.conn, self.sec_type, self.codes)


class MaCrossReclaimEntry(_MaCrossBase):
    """cross — the conditioned MA reclaim (buy)."""

    SUB_TYPES = ("ma20_cross", "ma60_cross")

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        keys = ["code"]
        for w in (20, 60):
            gap = frame[f"gap{w}"].astype("float64")
            prev_gap = grouped_shift(
                frame.assign(_g=gap), keys, ["_g"], out_names=["_pg"],
                periods=1,
            )["_pg"]
            below_prev = (prev_gap < 0).fillna(False)
            candidates = [
                Candidate(
                    key=f"excess{abs(bar) * 100:g}pct",
                    # the first close back above the MA whose last
                    # below-day cleared the excess bar
                    mask=(gap >= 0).fillna(False) & below_prev
                    & (prev_gap <= bar),
                    reference=0.0,
                    # depth: how much deeper than the bar the last
                    # below-day closed, normalized by the widest bar
                    depth=(prev_gap - bar) / 0.03,
                    params={"kind": "cross", "window": w,
                            "bar_def": {"kind": "cross_excess",
                                        "min_below_gap": bar}},
                )
                for bar in cfg.MA_CROSS_EXCESS_CANDIDATES
            ]
            r, h = self.run_cells(
                frame, f"ma{w}_cross", "buy", candidates,
                k=REENTRY_EXIT_DAYS, value_col=f"gap{w}",
                depth_factor=0.5,
                extra_params={"is_typical_split": "uninterrupted run"},
            )
            rules.extend(r)
            history.extend(h)
        return rules, history


class MaRetestEntry(_MaCrossBase):
    """retest — the failed-rally breakdown (sell)."""

    SUB_TYPES = ("ma20_retest",)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        w = 20
        gap = frame[f"gap{w}"].astype("float64")
        keys = ["code"]

        above = (gap >= 0).fillna(False)
        prev_above = grouped_shift(
            frame.assign(_a=above.astype("int64")), keys, ["_a"],
            out_names=["_pa"], periods=1,
        )["_pa"].fillna(0).astype("int64")
        run_start = above & (prev_above == 0)
        run_end = (~above) & (prev_above == 1)  # the first below-day

        work = frame[["code"]].assign(
            _rs=run_start.astype("int64"),
            _re=run_end.astype("int64"),
        )
        work["_run_id"] = work.groupby("code")["_rs"].cumsum()
        # per-code traded-day ordinal (positions are within the code —
        # the retest window is measured on the code's OWN calendar)
        work["_pos"] = work.groupby("code").cumcount() + 1
        # the ordinal of the last at-or-before run END, strictly before
        # today (yesterday's own end counts — a 1-day below gap between
        # runs is the tight retest)
        work["_end_pos"] = work["_pos"] * work["_re"]
        work["_last_end"] = work.groupby("code")["_end_pos"].cummax()
        prev_end = grouped_shift(
            work, keys, ["_last_end"], out_names=["_pe"], periods=1,
        )["_pe"].fillna(0)

        # a re-test rally: its start sits within RETEST_WINDOW traded
        # days of the previous above-episode's end
        short_run = run_start & (prev_end > 0) & (
            (work["_pos"] - prev_end) <= cfg.MA_RETEST_WINDOW)
        run_flags = (
            work.assign(_short=short_run.astype("int64"))
            .groupby(["code", "_run_id"])["_short"].max().rename(
                "_retest_run").reset_index()
        )
        work = work.merge(run_flags, on=["code", "_run_id"], how="left")
        mask = run_end & (work["_retest_run"].fillna(0) > 0)

        r, h = self.run_cells(
            frame, "ma20_retest", "sell",
            [Candidate(
                key="retest20",
                mask=mask,
                reference=0.0,
                depth=(-gap).clip(lower=0.0) / 0.05,
                params={"kind": "retest", "window": w,
                        "bar_def": {"kind": "episode_gap",
                                    "max_gap_days": cfg.MA_RETEST_WINDOW}},
            )],
            k=REENTRY_EXIT_DAYS, value_col=f"gap{w}",
            depth_factor=0.5,
        )
        rules.extend(r)
        history.extend(h)
        return rules, history


class MaBreadthEntry(_MaCrossBase):
    """breadth — the market-level thrust / 9:1 rules (2026-10-16).

    The Zweig Breadth Thrust and the 9:1 up/down days computed over
    the sec_type's OWN universe (fetch_market_breadth — every clean
    close code, not the requested family codes) and joined per DATE
    onto every code's frame: the market day is identical context for
    all codes, each code's cell measures its own forward blend of it.

      ma_breadth_thrust BUY  — the 10-session EMA of adv/(adv+dec)
          rises from below 0.40 to above 0.615 within 10 sessions
          (the rare thrust that has historically marked major bottoms
          — the 2024-09-24/26 class the per-code percentile families
          missed for lack of (bottom, calm) cells).
      ma_9to1_up BUY         — adv >= 9x dec at the code floor.
      ma_9to1_dn SELL        — dec >= 9x adv — exit timing (the
          capitulation washout day; 2026-10-15's growth-complex day
          hit these ratios).
    """

    SUB_TYPES = ("ma_breadth_thrust", "ma_9to1_up", "ma_9to1_dn")

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        ema = frame["mkt_breadth_ema10"].astype("float64")
        up_ratio = frame["mkt_up_ratio"].astype("float64")
        dn_ratio = frame["mkt_dn_ratio"].astype("float64")
        r9 = cfg.BREADTH_9TO1_RATIO

        r, h = self.run_cells(
            frame, "ma_breadth_thrust", "buy",
            [Candidate(
                key="thrust",
                mask=frame["mkt_thrust"].fillna(False),
                reference=cfg.BREADTH_THRUST_TO,
                depth=((ema - cfg.BREADTH_THRUST_TO) / 0.1)
                .clip(lower=0.0),
                params={"kind": "breadth_thrust",
                        "bar_def": {
                            "kind": "ema_cross_band",
                            "ema_span": cfg.BREADTH_EMA_SPAN,
                            "from_below": cfg.BREADTH_THRUST_FROM,
                            "through": cfg.BREADTH_THRUST_TO,
                            "within_sessions":
                                cfg.BREADTH_THRUST_WITHIN,
                            "min_codes": cfg.BREADTH_MIN_CODES}},
            )],
            k=REENTRY_EXIT_DAYS, value_col="mkt_breadth_ema10",
            depth_factor=0.5,
            extra_params={
                "value_source": "market breadth over the sec_type's "
                                "own universe (fetch_market_breadth)",
            },
        )
        rules.extend(r)
        history.extend(h)

        r, h = self.run_cells(
            frame, "ma_9to1_up", "buy",
            [Candidate(
                key="nine_one_up",
                mask=frame["mkt_9to1_up"].fillna(False),
                reference=r9,
                depth=((up_ratio - r9) / r9).clip(lower=0.0),
                params={"kind": "nine_one", "side": "up",
                        "bar_def": {"kind": "adv_dec_ratio",
                                    "ratio": r9,
                                    "min_codes":
                                        cfg.BREADTH_MIN_CODES}},
            )],
            k=REENTRY_EXIT_DAYS, value_col="mkt_up_ratio",
            depth_factor=0.5,
            extra_params={
                "value_source": "market breadth over the sec_type's "
                                "own universe (fetch_market_breadth)",
            },
        )
        rules.extend(r)
        history.extend(h)

        r, h = self.run_cells(
            frame, "ma_9to1_dn", "sell",
            [Candidate(
                key="nine_one_dn",
                mask=frame["mkt_9to1_dn"].fillna(False),
                reference=r9,
                depth=((dn_ratio - r9) / r9).clip(lower=0.0),
                params={"kind": "nine_one", "side": "dn",
                        "bar_def": {"kind": "adv_dec_ratio",
                                    "ratio": r9,
                                    "min_codes":
                                        cfg.BREADTH_MIN_CODES}},
            )],
            k=REENTRY_EXIT_DAYS, value_col="mkt_dn_ratio",
            depth_factor=0.5,
            extra_params={
                "value_source": "market breadth over the sec_type's "
                                "own universe (fetch_market_breadth)",
                "side_policy": "exit_timing",
            },
        )
        rules.extend(r)
        history.extend(h)
        return rules, history


class MaGdepthEntry(_MaCrossBase):
    """gdepth — the raw-gap band breach below the MA (buy ONLY)."""

    SUB_TYPES = ("ma120_gap", "ma255_gap")

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        for w in (120, 255):
            gap = frame[f"gap{w}"].astype("float64")
            candidates = [
                Candidate(
                    key=f"gap{abs(bar) * 100:g}pct",
                    mask=(gap <= bar).fillna(False),
                    reference=bar,
                    depth=(bar - gap) / 0.08,
                    params={"kind": "gdepth", "window": w,
                            "bar_def": {"kind": "gap_pct", "below": bar}},
                )
                for bar in cfg.MA_GDEPTH_BAR_CANDIDATES
            ]
            r, h = self.run_cells(
                frame, f"ma{w}_gap", "buy", candidates,
                k=REENTRY_EXIT_DAYS, value_col=f"gap{w}",
                depth_factor=0.5,
                extra_params={"side_policy": "buy_only"},
            )
            rules.extend(r)
            history.extend(h)
        return rules, history
