"""The ORACLE mode's signal algo (2026-11-19, user-directed "set up a
mode oracle for optimization ... base on only oracle signals").

MODULE ISOLATION (the defining property of this package):

  - The oracle mode lives HERE and only here — its own package under
    ``factors_and_algos``, its own signal source table
    (``analysis.oracle_signals`` — the hindsight local-min/max pivot
    tier built by ``analysis.oracle_signals``), its own apply mapping
    (the oracle's own forward-trend grade as base confidence). It
    does NOT subclass :class:`AnalysisSignalAlgoBase` (that
    contract's source is ``analysis_signals.history_signals``), does
    NOT import the comb stack, and carries NO reject / no-action /
    amplifier mirrors (the oracle tier has no such tiers). The ONLY
    edits outside this package are the registry entry
    (``_algo/registry.py`` — the designed extension seam: "adding an
    algo only requires changes in this file") and the UI mode toggle.

HINDSIGHT DISCLOSURE (kept honest in every reason string):

  The oracle's pivots are PERFECT-FORESIGHT labels — a pivot is only
  knowable ``window_days`` (5) bars after it forms, and its grade
  (the forward trend — 20 traded rows since the 2026-11-20 user
  directive "let future look only be 20d not 60d") + the swing
  target (the next opposite
  pivot) are knowable even later. The oracle MODE therefore replays
  an upper-bound reference strategy, not a tradable one; it exists to
  train/measure what the ideal local-extreme timing is worth.

THE ORACLE'S OWN FAVORABLE-MOVE GRADE (grammar v5, 2026-11-19 —
user-directed "should look future 60d if there are big rise/drop
trend, should tune down action confidence even take no action" +
"should consider before and after local min/max rises and falls";
the replay consumes the STORED grade since the same day's "new oracle
confidence column" decision):

  The analysis tier grades every placed pivot by its favorable
  excursion on EITHER side — a buy by the biggest rise within the
  next 60 traded rows OR the biggest drop within the 60 rows before
  it, a sell by the biggest drop after / biggest rise before (the
  max favorable excursion on both sides) — each leg graded 0..100 on
  its own LINEAR ramp from the 2% no-action floor to the 10% full
  bar and combined as the WEIGHTED SUM 0.3 * pre-action leg +
  0.7 * forward leg (user-directed "let rises/drops before action
  date with confidence weighted 30%, while future rises/drops
  weighted 70%"; calibrated by temp_scripts/oracle_forward60_study.py
  + temp_scripts/oracle_backward_leg_study.py,
  docs/oracle_forward60_confidence_study_2026-11-19.md +
  docs/oracle_backward_leg_study_2026-11-19.md; a zero weighted
  grade — both legs below the floor — means the pivot emits NOTHING —
  the 2020-01 small-gap chop buys are not in the table at all, while
  the v5 run-up-validated tops 000001's 2020-11-09 / 2024-02-27 /
  2025-05-14 grade on their backward share). The replay:

      signal_confidence = ± oracle_confidence (signed by the side,
      then through the replay's CONFIDENCE SCALING — scaling.py;
      identity = the raw grade as-is)
      -> NULL grade  = an UNREALIZED frontier window whose backward
         leg also misses the floor: unknown is not small — replay at
         FULL size 100 (the flat-replay convention; a frontier row
         whose backward leg GRADES emits its 30% share — the tail
         re-grades upward as the forward realizes)
      -> the ext-based swing stays ``signal_value`` (the evidence the
         grade was scaled from; percent fraction)
      -> engine semantics: BUY qty = confidence, SELL qty_sold =
         conf/100 × the book — small falls exit partially, large
         falls fully (``exit_mode`` "full" = never scale an exit down,
         the meta-labeling convention) — one axis of the confidence
         scaling, whose whole group (curve power / floor / null size /
         exit mode) is the ORACLE OPTIMIZER's surface (optimizer.py),
         never the NestedTrainer's

FULL-FIDELITY REPLAY (grammar v6, 2026-11-20, user-directed "should
load exact the same signals from analysis oracle"): the replay
consumes EVERY graded pivot AT THE PIVOT'S OWN DATE — the generic
dust filter (``conf_threshold`` / tune_signals), the exec-date
delays (``buy_exec_delay`` / ``sell_exec_delay``) and the SELL
holding gate (``min_holding_period``, pinned 1) are structurally
OFF (the AlgoBase fidelity flags, honored by the FT stress pass and
read by the trainer's space builders so no ignored param is ever
tuned), and the 2026-11-19 first-sell filter is REMOVED. The only
decisions still absent from the pivot stream are sells while the
book is flat (the leading sells before the code's first buy) —
untradable from flat, not a filter.

  The 2026-11-19 swing-proportional scale (the per-code smallest-
  positive-swing anchor × power law) is SUPERSEDED by the stored
  grade: both express "confidence proportional to the following
  move", but the grade measures the honest forward excursion (20
  traded rows since 2026-11-20; 60 in the v4/v5 era)
  (not just the ride to the next placed pivot) and is validated
  against the no-action floor.
"""
from __future__ import annotations

import pandas as pd

from _common.labels import ORACLE_ACTION_CODE, SEC_TYPE_CODE
from strategy.factors_and_algos._algo.base import AlgoBase
from strategy.factors_and_algos._algo.fetch_base import (
    basic_stats_table,
    rows_to_df,
)
from strategy.factors_and_algos._oracle.amplify import ConfidenceAmplifier
from strategy.factors_and_algos._oracle.scaling import ConfidenceScale

__all__ = ["OracleSignalAlgo"]


class OracleSignalAlgo(AlgoBase):
    """Pick up the hindsight oracle's pivot actions and replay them at
    the oracle's own forward-trend grade.

        Signals are PICKED UP from ``analysis.oracle_signals`` (never
        recomputed): a local min takes a BUY, a local max a SELL — each
        already validated by the oracle's favorable-move grading (v5: a
        pivot whose BOTH favorable legs miss the 2% no-action floor is
        not in the table at all). The pivot math itself has no params (it
        lives in analysis.oracle_signals); the replay's tunable surface
        is its CONFIDENCE SCALING (scaling.py — owned by the oracle's
        own optimizer, never the trainer) — the full-fidelity flags
        below keep every graded pivot reaching the engine at the
        pivot's own date.
        """

    ALGO_NAME = "oracle"

    # FULL-FIDELITY REPLAY (2026-11-20, user-directed "should load exact
    # the same signals from analysis oracle"): the replay consumes every
    # graded pivot AT THE PIVOT'S OWN DATE — the generic dust filter
    # (``conf_threshold`` / tune_signals), the exec-date delays
    # (``buy_exec_delay`` / ``sell_exec_delay``) and the SELL holding
    # gate (``min_holding_period`` — pinned 1 in ``run_backtest``, so a
    # stale tuned value like the 2026-11-20 row's 3 is overridden) are
    # structurally OFF. The trainer's space builders read the same flags
    # and drop the axes, so no ignored param is ever tuned or stored.
    DUST_FILTER = False
    TUNES_EXEC_DELAYS = False
    TUNES_MIN_HOLDING = False

    # Event columns the fetch carries (NULL on non-pivot bars). The
    # swing-target DATE stays out of the frame (the reason phrasing
    # needs only its price — a date column in rows_to_df's numeric
    # conversion renders nan).
    REQUIRED_COLUMNS: tuple = (
        "oracle_action",       # static.label_dim 'oracle_action' code
        "oracle_window_days",  # the pivot order (5)
        "oracle_ext_price",    # the swing-target close (NULL at frontier)
        "oracle_confidence",   # the v5 favorable-move grade 0..100
                               # (NULL = a backward-dust unrealized
                               # frontier window)
    )

    # No model params: the pivot math + the grade live in
    # analysis.oracle_signals.
    ALGO_PARAM_KEYS: tuple = ()
    # TUNABLE_SPACE is EMPTY (2026-11-20): the replay's one-time
    # trainer axis ``exit_mode`` is RETIRED into the confidence-
    # scaling group (``params["conf_scale"].exit_mode`` — sell-side
    # confidence semantics ARE scaling), which is owned by the
    # oracle's OWN optimizer (optimizer.py), never the Omega/Calmar
    # NestedTrainer (the user's isolation directive). Legacy
    # top-level ``exit_mode`` keys (the 2026-11-19/20 trained rows,
    # old study replays) are still honored by
    # ``ConfidenceScale.from_params`` — but only when no group is
    # present. (swing_conf_power / wrong_way_mode were retired
    # earlier with grammar v4.)
    TUNABLE_SPACE: dict = {}
    DEFAULT_PARAMS: dict = {
        # Documentation-only constants (never read by code): the pickup
        # source and the confidence rule (the grade itself is computed
        # by the analysis tier per run — 2% floor / 10% full linear).
        "signal_source": "analysis.oracle_signals",
        "confidence_ref": (
            "the oracle's own favorable-move grade (the weighted sum "
            "0.3 * pre-action leg + 0.7 * forward leg, each leg "
            "ramped 2% no-action floor / 10% full); NULL grade = a "
            "backward-dust unrealized frontier window, replays "
            "full size"
        ),
        # The replay's confidence scaling (scaling.py) — pinned by hand
        # from the oracle's own optimizer's matrices (temp_scripts/
        # oracle_conf_scale_results.csv + oracle_pnl_*.csv +
        # oracle_amp_*.csv, 2026-11-20). AMP PIN (2026-11-20, user
        # directives "higher pnl + stable increasing trend, creative
        # signal amplification" then "the 2025-05-09 rise should see
        # significant pnl rise" — streak_k raised 0.4 -> 0.6, the
        # STRICT DOMINANCE point where every stability metric still
        # >= the sk0.4 cell): IDENTITY grades (power 1.0, swing — the
        # former p1.5 convex pin is SUPERSEDED: it was compensating
        # for book-blind sizing, which the amplifier now owns) + the
        # shadow-book gap-restore/streak stage ON. Measured (10 study
        # codes, oracle_amp_round2-5): pooled pnl 41.3k vs identity
        # 17.0k / former p1.5 pin 16.7k (+148%) at pooled dd 4.5%,
        # worst-code dd 19.7% (vs 29.8%), posMo 76% / worst month
        # streak 4, OOS pnl 9.9k. 000001: pnl 506 (feeless) vs 245
        # pin, window 2025-05-09 rise realized +70.0 feeless (old pin
        # +37.3 stored, sk0.4 amp +62.5 stored), window A +21.8, ret
        # +65.7%, dd 4.5%, posMo 67%. LEVERAGE DISCLOSURE (standing):
        # capital ~2.0x identity on 000001 (~2.9x pooled) — the Total
        # Return card reads LOWER while absolute pnl ~2.4x's; streak_k
        # is the measured leverage dial (+0.1 sk ~ +2.4 winB / +20
        # 000001-pnl / +0.1-0.2x lev, dd FLAT to sk1.0 — sk0.7+ starts
        # paying a small pooled-dd premium). top_k>0 is the defensive
        # variant. Re-measured and re-pinned by hand after every
        # analysis.oracle_signals grammar change — never trainer-
        # upserted.
        "conf_scale": {
            "grade_power": 1.0,
            "grade_floor": 0.0,
            "null_conf": 100.0,
            "exit_mode": "swing",
        },
        # The replay's signal AMPLIFICATION (amplify.py — the shadow-
        # book gap-restore / streak / top-boost stage, 2026-11-20 user
        # directive). Pinned by hand from the oracle optimizer's
        # amplification matrices (temp_scripts/oracle_amp_*.csv) under
        # the same convention as conf_scale.
        "conf_amp": {
            "enabled": True,
            "gap_k": 1.0,
            "gap_deadband": 0.9,
            "grade_gate": True,
            "buy_cap": 300.0,
            "streak_k": 0.6,
            "top_k": 0.0,
            "top_grade": 80.0,
        },
    }

    # ------------------------------------------------------------------
    # Fetch — OHLC + the oracle's pivot rows
    # ------------------------------------------------------------------
    async def fetch_signal_data(
        self, conn, sec_type: str, codes: list,
    ) -> pd.DataFrame:
        """Fetch OHLC LEFT JOINed with the oracle's pivot actions.

        Joins ``stats.<sec_type>_basic_stats`` (the full OHLC series —
        fill prices + daily rows need every bar) with
        ``analysis.oracle_signals`` (PK ``(sec_type, code, date)`` —
        one pivot row per date by construction, no DISTINCT ON
        needed). Non-pivot bars carry NULL ``oracle_*`` columns;
        :meth:`apply_signals` maps them to a zero signal.
        """
        if not codes:
            return pd.DataFrame()
        sql = f"""
            SELECT
                $1::text AS sec_type,
                b.code, b.date,
                b.open  AS open_price,
                b.high  AS high_price,
                b.low   AS low_price,
                b.close AS close_price,
                o.action       AS oracle_action,
                o.window_days  AS oracle_window_days,
                o.ext_price    AS oracle_ext_price,
                o.confidence   AS oracle_confidence
            FROM {basic_stats_table(sec_type)} b
            LEFT JOIN analysis.oracle_signals o
              ON o.code = b.code AND o.date = b.date
             AND o.sec_type = $3::smallint
            WHERE b.code = ANY($2::text[])
            ORDER BY b.code, b.date ASC
        """
        rows = await conn.fetch(
            sql, sec_type, sorted(codes), SEC_TYPE_CODE[sec_type],
        )
        numeric_cols = [
            "open_price", "high_price", "low_price", "close_price",
            *self.REQUIRED_COLUMNS,
        ]
        return rows_to_df(rows, numeric_cols)

    # ------------------------------------------------------------------
    # Apply — pivot row -> the oracle's graded signed signal_confidence
    # ------------------------------------------------------------------
    def apply_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Map the picked-up pivot rows onto the engine's signal columns.

          - ``signal_confidence`` = the oracle's OWN favorable-move
            grade (v5: the WEIGHTED SUM of the pivot's two legs'
            linear-ramp grades — 0.3 x the backward-60-row move into
            it + 0.7 x the forward-20-row move after it (the
            2026-11-20 user directive "let future look only be 20d
            not 60d"), each leg ramped
            2% no-action floor / 10% full; a zero weighted grade
            means the pivot is not in the table at all), signed by
            the pivot's action (buy positive, sell negative). A NULL
            grade is a backward-dust unrealized frontier window —
            unknown is not small, replay full size. Every graded
            pivot passes through UNFILTERED (grammar v6 full
            fidelity — no dust threshold, no exec delays, no
            first-sell filter; the engine's own flat-book sell skip
            is the only drop).
          - ``signal_value`` = the ext-based swing (the evidence the
            grade was scaled from; percent fraction).
        """
        if df.empty:
            df = df.copy()
            df["signal_confidence"] = pd.Series(dtype=float)
            df["signal_value"] = pd.Series(dtype=float)
            return df

        is_buy = df["oracle_action"] == ORACLE_ACTION_CODE["buy"]
        is_sell = df["oracle_action"] == ORACLE_ACTION_CODE["sell"]
        is_event = is_buy | is_sell

        close = df["close_price"]
        ext = df["oracle_ext_price"]
        # side-aligned rise/fall, NaN where the swing is unknown (the
        # frontier) — stays the recorded evidence (signal_value)
        swing = ext / close - 1.0        # buy: the rise to the next max
        swing = swing.where(is_buy, 1.0 - ext / close)  # sell: the fall
        # THE ORACLE'S OWN GRADE -> replay confidence, through the
        # replay's CONFIDENCE SCALING (scaling.py — the oracle mode's
        # own tunable surface, owned by its optimizer, never the
        # NestedTrainer). Identity by default: the stored grade as-is,
        # NULL (an unrealized frontier window) replays full size —
        # unknown is not small. (One whole-frame transform —
        # cudf-native, no per-group loops.)
        scale = ConfidenceScale.from_params(params)
        conf = scale.apply(df["oracle_confidence"], is_buy, is_sell)
        # Post-scaling signal AMPLIFICATION (amplify.py — the oracle's
        # own creative surface, 2026-11-20 user directive): the replay's
        # shadow book restores exposure after graded trims (gap_restore
        # / streak_boost) and strong tops exit harder (top_boost).
        # Magnitude-only, same dates, oracle-module isolated; absent /
        # malformed group -> disabled.
        amp = ConfidenceAmplifier.from_params(params)
        if amp.cfg.enabled:
            conf = amp.apply(df, conf, is_buy, is_sell, scale.cfg.exit_mode)
        signed = (
            conf.where(is_buy, -conf)
            .where(is_event, 0.0)
            .fillna(0.0)
        )

        df = df.copy()
        df["signal_confidence"] = signed
        df["signal_value"] = swing.fillna(0.0)
        return df

    # ------------------------------------------------------------------
    # Reason — the oracle's own phrasing (hindsight disclosed)
    # ------------------------------------------------------------------
    def build_signal_reason(
        self, row, side: str, params: dict, confidence: float,
    ) -> str:
        """Phrase the picked-up pivot (the hindsight disclosure rides
        every reason string)."""

        def _fmt(v) -> str:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return "NA"
            return f"{f:.4f}" if f == f else "NA"

        def _pct(v) -> str:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return "NA"
            return f"{f * 100:+.2f}%" if f == f else "NA"

        try:
            window = int(float(row.get("oracle_window_days")))
        except (TypeError, ValueError):
            window = 0
        ext_price = row.get("oracle_ext_price")
        target = (
            f"swing {_pct(row.get('signal_value'))} to target "
            f"{_fmt(ext_price)}"
            if ext_price is not None and _fmt(ext_price) != "NA"
            else "swing unknown (frontier pivot)"
        )
        scale = ConfidenceScale.from_params(params)
        scale_note = (
            f" [{scale.cfg.describe()}]"
            if not scale.cfg.is_identity else ""
        )
        amp = ConfidenceAmplifier.from_params(params)
        amp_note = (
            f" [{amp.cfg.describe()}]" if amp.cfg.enabled else ""
        )
        return (
            f"oracle {side}: HINDSIGHT order-{window} local "
            f"{'min' if side.lower() == 'buy' else 'max'} pivot picked up "
            f"from analysis.oracle_signals — {target} "
            f"-> conf {confidence:.1f}/100 "
            f"(the oracle's favorable-move grade — 30% pre-action / "
            f"70% forward 20-row legs, 2% floor / 10% full)"
            f"{scale_note}{amp_note}"
        )
