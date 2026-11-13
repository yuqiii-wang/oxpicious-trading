"""Analysis-signal-sourced algo base — signals PICKED UP, never recomputed.

The Singleton Strategy's signal contract (2026-11-07 refactor,
user-directed): a strategy algo must NOT detect or recompute its own
signals — the ONLY legal signal source is the analysis tier's recorded
events in ``analysis_signals.history_signals`` (the rule cells' triggers
replayed by ``python -m analysis.analysis_signals``). This module is that
contract as code: :class:`AnalysisSignalAlgoBase` implements the generic
fetch (OHLC LEFT JOINed with the family's recorded events) and the
generic apply (event → signed ``signal_confidence``), so a family algo
subclass declares only its identity (``SIGNAL_TYPE_CODE``) and its
human-readable reason phrasing.

Per-family semantics — which sub types exist, what the bars mean, the
episode/K-absorb shaping — live in the analysis tier
(``analysis/analysis_signals``); see the sibling skills
``signals-live-vs-history`` / ``signals-biz-logic``. The strategy tier
consumes the FINISHED events:

  - one row per qualifying episode's FIRST day (K-absorb already applied
    by the detector), delay 0 — so no rising-edge filtering is needed
    here; the engine's ``min_holding_period`` still gates SELLs;
  - ``action`` — the static.label_dim 'action' code: 0 buy / 1 sell /
    2 wait. WAIT rows are observational only and are NEVER picked up
    (the SQL filters ``action IN (0, 1)``);
  - ``confidence`` — the cell's SCALED breach confidence in INTEGER
    basis points [0, 10000] (measured dir_ave base × depth scale ×
    starvation scale; bp/100 ≈ the expected move in percent points).
    This is an EVIDENCE record, NOT position sizing — the recorded
    population is small (macd: buys p50 77bp, sells p50 44bp), so a
    linear bp→sizing map would land everything under the engine's 5.0
    dust threshold and trade nothing. The engine sizing is therefore a
    FLAT full-size replay (:attr:`EVENT_CONFIDENCE`): every recorded
    event trades the same size — BUY adds a full position, SELL exits
    the whole position — so the backtest measures exactly the recorded
    signal timing. The bp evidence rides the reason text +
    ``signal_value`` instead;
  - ``signal`` / ``signal_reference`` — the day's indicator value and
    the crossed cell bar (family-local meaning; carried through as
    ``signal_value`` + reason text);
  - ``is_in_portfolio = FALSE`` — the STANDALONE tier only. The TRUE
    tier (cells sourced by a portfolio family's coordination) fires
    inside its portfolio, never standalone.

Because the signal math is no longer the strategy's, a subclass carries
NO model params: ``DEFAULT_PARAMS`` documents the pickup source only and
``TUNABLE_SPACE`` is empty (the optimization engine tunes just the
COMMON trading space — thresholds, delays, holding period).
"""
from __future__ import annotations

import pandas as pd

from _common.labels import (
    ACTION_CODE,
    SEC_TYPE_CODE,
    SIGNAL_SUB_TYPE_LABEL,
    SIGNAL_TYPE_CODE,
)
from strategy.factors_and_algos._algo.base import AlgoBase
from strategy.factors_and_algos._algo.fetch_base import (
    basic_stats_table,
    rows_to_df,
)

__all__ = ["AnalysisSignalAlgoBase", "FamilySignalAlgo", "family_algo"]


class AnalysisSignalAlgoBase(AlgoBase):
    """Base for algos whose signals are PICKED UP from
    ``analysis_signals.history_signals`` (never detected here).

    Subclass contract:
      - ``SIGNAL_TYPE_CODE`` — the family's static.label_dim 'signal_type'
        code (REQUIRED; the events subquery filters on it);
      - ``build_signal_reason`` — OPTIONAL override for the family's own
        phrasing (the base ships a generic default; the shared event
        fields arrive on the row as the ``hist_*`` columns, see
        :meth:`event_reason_parts`);
      - ``ALGO_NAME`` / ``POSITION_AWARE`` as on :class:`AlgoBase`.

    Inherited concrete behavior (shared, family-agnostic):
      - :meth:`fetch_signal_data` — OHLC from ``stats.<sec_type>_basic_stats``
        LEFT JOINed with the family's recorded standalone-tier events
        (DISTINCT ON guarantees at most one event per (code, date) so the
        OHLC series is never duplicated);
      - :meth:`apply_signals` — event → ``signal_confidence`` mapping
        (±:attr:`EVENT_CONFIDENCE`, signed by action) + ``signal_value``
        = the event's indicator value.
    """

    # The family's static.label_dim 'signal_type' code — REQUIRED override
    # (e.g. macd = 10). -1 is the unset sentinel; fetch would return no
    # events, so __init_subclass__ fails fast instead.
    SIGNAL_TYPE_CODE: int = -1

    # Event columns the fetch carries (NULL on non-event bars) — also the
    # columns build_signal_reason reads from the row.
    REQUIRED_COLUMNS: tuple = (
        "hist_action", "hist_sub_type", "hist_signal",
        "hist_reference", "hist_confidence",
    )

    # No model params: the signal math lives in the analysis tier.
    ALGO_PARAM_KEYS: tuple = ()
    DEFAULT_PARAMS: dict = {
        # Documentation-only constant: the DB algo_configs default row
        # shows WHERE the signals come from. Never read by code.
        "signal_source": "analysis_signals.history_signals",
    }
    TUNABLE_SPACE: dict = {}

    # Engine sizing for a picked-up event: FLAT full-size replay. The
    # recorded bp confidence is EVIDENCE (dir_ave-based; bp/100 ≈
    # percent points — the family populations sit at tens..hundreds of
    # bp), not the engine's 0-100 position sizing — a linear bp map
    # would dust-filter everything below SIGNAL_CONFIDENCE_THRESHOLD.
    # BUY adds EVENT_CONFIDENCE units; SELL liquidates EVENT_CONFIDENCE
    # percent of the position (100 = full exit — the recorded SELL
    # events are exit-timing by family design).
    EVENT_CONFIDENCE: float = 100.0

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.SIGNAL_TYPE_CODE < 0:
            raise TypeError(
                f"{cls.__name__} must set SIGNAL_TYPE_CODE (the family's "
                "static.label_dim 'signal_type' code) — signals are picked "
                "up from analysis_signals.history_signals by type."
            )

    # ------------------------------------------------------------------
    # Fetch — OHLC + the family's recorded events
    # ------------------------------------------------------------------
    async def fetch_signal_data(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Fetch OHLC LEFT JOINed with the family's recorded signal events.

        Joins:
          - ``stats.<sec_type>_basic_stats`` (b) — the full OHLC series
            (fill prices + the daily-row computation need every bar, not
            just event days);
          - ``analysis_signals.history_signals`` (h) — the family's
            STANDALONE-tier trading events (``signal_type`` =
            ``SIGNAL_TYPE_CODE``, ``is_in_portfolio = FALSE``,
            ``action IN (buy, sell)`` — wait rows are observational and
            never picked up). The events subquery is DISTINCT ON
            (code, date) so a family with multiple same-day sub types
            cannot duplicate OHLC rows; the lowest sub_type code wins
            (deterministic; for mutually-exclusive sub types like macd's
            cross up/dn it never matters).

        Non-event bars carry NULL ``hist_*`` columns — apply_signals
        maps them to a zero signal.
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
                h.action         AS hist_action,
                h.signal_sub_type AS hist_sub_type,
                h.signal         AS hist_signal,
                h.signal_reference AS hist_reference,
                h.confidence     AS hist_confidence
            FROM {basic_stats_table(sec_type)} b
            LEFT JOIN (
                SELECT DISTINCT ON (code, date)
                    code, date, action, signal_sub_type,
                    signal, signal_reference, confidence
                FROM analysis_signals.history_signals
                WHERE sec_type = $3::smallint
                  AND signal_type = $4::smallint
                  AND is_in_portfolio = FALSE
                  AND action = ANY($5::smallint[])
                AND code = ANY($2::text[])
                ORDER BY code, date, signal_sub_type
            ) h ON h.code = b.code AND h.date = b.date
            WHERE b.code = ANY($2::text[])
            ORDER BY b.code, b.date ASC
        """
        rows = await conn.fetch(
            sql,
            sec_type, sorted(codes),
            SEC_TYPE_CODE[sec_type], self.SIGNAL_TYPE_CODE,
            [ACTION_CODE["buy"], ACTION_CODE["sell"]],
        )
        numeric_cols = [
            "open_price", "high_price", "low_price", "close_price",
            *self.REQUIRED_COLUMNS,
        ]
        return rows_to_df(rows, numeric_cols)

    # ------------------------------------------------------------------
    # Apply — recorded event -> signed signal_confidence
    # ------------------------------------------------------------------
    def apply_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Map the picked-up events onto the engine's signal columns.

        Pure translation — no signal math, no recompute:

          - ``signal_confidence`` = ±:attr:`EVENT_CONFIDENCE` (flat
            full-size replay), signed by the event's action: BUY
            positive, SELL negative. Non-event bars → 0 (no signal).
            The recorded bp evidence is NOT used for sizing (see
            :attr:`EVENT_CONFIDENCE`).
          - ``signal_value`` = the event's ``signal`` (the day's indicator
            value; family-local meaning). Non-event bars → 0.

        The events are already episode-FIRST days (the analysis tier's
        detector applied the K-absorb), so one event fires at most once
        per episode — no rising-edge filtering here.
        """
        if df.empty:
            df = df.copy()
            df["signal_confidence"] = pd.Series(dtype=float)
            df["signal_value"] = pd.Series(dtype=float)
            return df

        action = df["hist_action"]
        is_buy = action == ACTION_CODE["buy"]
        is_sell = action == ACTION_CODE["sell"]
        conf = self.EVENT_CONFIDENCE
        signed = (
            pd.Series(conf, index=df.index).where(is_buy, -conf)
            .where(is_buy | is_sell, 0.0)
            .fillna(0.0)
        )

        df = df.copy()
        df["signal_confidence"] = signed
        df["signal_value"] = df["hist_signal"].fillna(0.0)
        return df

    # ------------------------------------------------------------------
    # Reason — shared event-field formatting
    # ------------------------------------------------------------------
    def event_reason_parts(self, row) -> tuple[str, str, str, str]:
        """Format the row's picked-up event for reason text.

        Returns ``(sub_label, value_str, reference_str, conf_bp_str)`` —
        the decoded sub-type label and NaN-safe renderings of the event's
        indicator value, crossed bar and bp confidence. Subclass reason
        builders phrase these in the family's vocabulary.
        """
        def _fmt(v) -> str:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return "NA"
            return f"{f:.4f}" if f == f else "NA"

        sub_code = row.get("hist_sub_type")
        try:
            sub_label = SIGNAL_SUB_TYPE_LABEL[int(sub_code)] if sub_code is not None else "NA"
        except (KeyError, TypeError, ValueError):
            sub_label = "NA"
        return (
            sub_label,
            _fmt(row.get("hist_signal")),
            _fmt(row.get("hist_reference")),
            _fmt(row.get("hist_confidence")),
        )

    # ------------------------------------------------------------------
    # Reason — generic default; subclasses may override for family voice
    # ------------------------------------------------------------------
    def build_signal_reason(self, row, side: str, params: dict, confidence: float) -> str:
        """Phrase the picked-up event (generic family-agnostic default).

        Reads the ``hist_*`` columns via :meth:`event_reason_parts` —
        the decoded sub type, its value vs the crossed bar, and the
        cell's bp evidence. A dedicated algo package (e.g. macd) may
        override for family-specific phrasing; the generic family algos
        (:class:`FamilySignalAlgo`) use this as-is.
        """
        sub, value, reference, conf_bp = self.event_reason_parts(row)
        return (f"{self.ALGO_NAME} {side}: analysis signal {sub} picked "
                f"up from analysis_signals.history_signals — value={value} "
                f"vs bar={reference}, cell confidence={conf_bp}bp "
                f"-> {confidence:.1f}/100")


# ---------------------------------------------------------------------------
# Generic one-family algos — identity straight from the label vocabulary
# ---------------------------------------------------------------------------
class FamilySignalAlgo(AnalysisSignalAlgoBase):
    """A GENERIC pickup algo for one analysis signal family.

    Identity (``ALGO_NAME`` / ``SIGNAL_TYPE_CODE``) comes from the
    static.label_dim 'signal_type' vocabulary at construction — no
    dedicated algo package needed. Everything (fetch, apply, reason) is
    the family-agnostic base behavior; only a family with its own reason
    voice (macd) earns a dedicated subclass.
    """

    # Placeholder so the __init_subclass__ guard passes at class
    # definition; the real per-family code lands on the INSTANCE in
    # __init__ (before any fetch/apply can run).
    SIGNAL_TYPE_CODE: int = 0

    def __init__(self, family_name: str) -> None:
        if family_name not in SIGNAL_TYPE_CODE:
            raise KeyError(
                f"unknown analysis signal family {family_name!r}; "
                f"vocabulary: {sorted(SIGNAL_TYPE_CODE)}"
            )
        self.ALGO_NAME = family_name
        self.SIGNAL_TYPE_CODE = SIGNAL_TYPE_CODE[family_name]


# One singleton per family (the registry's contract: get_algo returns a
# cached instance, never a fresh one per call).
_FAMILY_ALGOS: dict = {}


def family_algo(family_name: str) -> FamilySignalAlgo:
    """Return the cached :class:`FamilySignalAlgo` for ``family_name``.

    ``family_name`` must be a static.label_dim 'signal_type' label (e.g.
    ``macd`` / ``mov_rsi`` / ``bollinger``) — the analysis families the
    strategy tier can pick up events for.
    """
    algo = _FAMILY_ALGOS.get(family_name)
    if algo is None:
        algo = FamilySignalAlgo(family_name)
        _FAMILY_ALGOS[family_name] = algo
    return algo
