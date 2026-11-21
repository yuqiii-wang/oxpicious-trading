"""ForecastFetch — the forecast module's reads
(strategy.forecast_next_day.fetch).

One conn-scoped class, one method per sql_views read (the views live
under ``database/sql_views/strategy/forecast_next_day/`` — the module
owns ALL its reads; no other tier's view is imported). Everything
returns plain python structures (per-code scalar lists — the sanctioned
per-group host boundary), with the label decoding + jsonb parsing the
live tier does at its fetch boundary mirrored for the active cells.
"""
from __future__ import annotations

import json
from datetime import date

from asyncpg import Connection

from _common.labels import (
    ACTION_CODE,
    ACTION_LABEL,
    SEC_TYPE_CODE,
    SIGNAL_SUB_TYPE_LABEL,
    SIGNAL_TYPE_LABEL,
    SIGNAL_TYPE_CODE,
)
from _common.sql_views import frag, render_sql_view
from _common.sql_writes import render_sql_write

from analysis.analysis_signals.config import (
    MOMENTUM_BAR_MIN_PERIODS,
    MOMENTUM_BAR_WINDOW_DAYS,
    RSI_BAR_MIN_PERIODS,
    RSI_BAR_WINDOW_DAYS,
    RSI_WINDOWS,
    VOL_BAR_MIN_PERIODS,
    VOL_BAR_WINDOW_DAYS,
)
from analysis.mov_ave_spread.config.price_sources import (
    OHL_EST_FILTER,
    PRICE_SOURCE,
)

from strategy.forecast_next_day import CLOSE_ROW_LIMIT, SCENARIO_FAMILIES
from strategy.forecast_next_day.evaluation import (
    MOMENTUM_ANCHOR_ROWS,
    PositionState,
    ScenarioBars,
    _tail_bars,
    _trailing_sigma,
)

# the analysis tier's value-source tables (the fetch vocabulary
# constants — analysis_signals' own _shared names, restated so the
# module's reads stay self-contained)
SIGNALS_TABLE = "analysis_signals.signal_rules"
SIGNAL_PERFORMANCE_TABLE = "analysis_signals.history_signal_performance"
TREND_DAYS_TABLE = "analysis.trend_days"
RSI_TABLE = "analysis.mov_ave_rsi"
MOMENTUM_TABLE = "analysis.mov_ave_momentum"
VOL_TABLE = "analysis.volatility"

# the momentum FIXED anchors the scenario space evaluates (rows keyed
# by the axis label; the vol-adaptive axes are not close-derivable)
_MOMENTUM_ANCHOR_AXES = ("5days", "20days")

# the EMA fetch cap — the macd signal line's EMA9 recursion fully
# converges over this window (decay 0.8^n ≈ 0 long before the cap; the
# engine's own recursion runs the full history, identical from here on)
_EMA_ROW_LIMIT = 500

# the bar-window fetch caps (the trailing window + the shifted anchor
# row the bar computation drops)
_RSI_ROW_LIMIT = RSI_BAR_WINDOW_DAYS + 1
_MOMENTUM_ROW_LIMIT = MOMENTUM_BAR_WINDOW_DAYS + 1
_VOL_ROW_LIMIT = VOL_BAR_WINDOW_DAYS + 1


def _tech_stats_table(sec_type: str) -> str:
    """The macd family's EMA source table per sec_type (one table per
    sec_type — no sec_type column on it)."""
    return f"stats.{sec_type}_tech_stats"


class ForecastFetch:
    """The forecast's conn-scoped read store — one method per view."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # -- the price ladder -------------------------------------------------

    async def fetch_closes(
        self, sec_type: str, codes: list[str],
    ) -> dict[str, tuple[list[date], list[float]]]:
        """The run's codes' trailing closes (oldest-first), with the
        engines' own OHLC-estimated-row exclusion — the σ ladder + the
        synthetic-day value recompute's ONE price history."""
        base, price_expr = PRICE_SOURCE[sec_type]
        # strategy/forecast_next_day/closes
        view = render_sql_view(
            "strategy/forecast_next_day/closes",
            price_base=frag(base),
            price_expr=frag(price_expr),
            ohl_est_filter=frag(OHL_EST_FILTER[sec_type]),
            codes=codes,
            row_limit=CLOSE_ROW_LIMIT,
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        out: dict[str, tuple[list[date], list[float]]] = {}
        for r in rows:
            dates, closes = out.setdefault(r["code"], ([], []))
            dates.append(r["date"])
            closes.append(r["close"])
        return out

    # -- the bar histories -------------------------------------------------

    async def fetch_rsi_history(
        self, sec_type: str, codes: list[str],
    ) -> dict[str, dict[int, list[float | None]]]:
        """The trailing analysis.mov_ave_rsi rows per window
        (oldest-first, None-free not guaranteed — thin warmup rows
        carry NULL rsi) — the mov_rsi bars' input."""
        rsi_raw = ",\n               ".join(
            f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
        )
        rsi_names = ", ".join(f"rsi_{w}days" for w in RSI_WINDOWS)
        # strategy/forecast_next_day/rsi_history
        view = render_sql_view(
            "strategy/forecast_next_day/rsi_history",
            rsi_table=frag(RSI_TABLE),
            sec_type=SEC_TYPE_CODE[sec_type],
            codes=codes,
            row_limit=_RSI_ROW_LIMIT,
            rsi_raw_cols=frag(rsi_raw),
            rsi_cols=frag(rsi_names),
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        out: dict[str, dict[int, list[float | None]]] = {}
        for r in rows:
            per = out.setdefault(
                r["code"], {w: [] for w in RSI_WINDOWS},
            )
            for w in RSI_WINDOWS:
                per[w].append(r[f"rsi_{w}days"])
        return out

    async def fetch_momentum_history(
        self, sec_type: str, codes: list[str],
    ) -> dict[str, dict[str, list[float | None]]]:
        """The trailing FIXED-ANCHOR momentum axes as log-return series
        (oldest-first) — the momentum bars' + depth normalizer's
        input. ln(1.0 + momentum) — the family fetch's own boundary
        transform — rides the column fragment."""
        logret_raw = ",\n               ".join(
            f"ln(1.0 + m.momentum_{axis}::float8) AS logret_{axis}"
            for axis in _MOMENTUM_ANCHOR_AXES
        )
        logret_names = ", ".join(
            f"logret_{axis}" for axis in _MOMENTUM_ANCHOR_AXES
        )
        # strategy/forecast_next_day/momentum_history
        view = render_sql_view(
            "strategy/forecast_next_day/momentum_history",
            momentum_table=frag(MOMENTUM_TABLE),
            sec_type=SEC_TYPE_CODE[sec_type],
            codes=codes,
            row_limit=_MOMENTUM_ROW_LIMIT,
            logret_raw_cols=frag(logret_raw),
            logret_cols=frag(logret_names),
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        out: dict[str, dict[str, list[float | None]]] = {}
        for r in rows:
            per = out.setdefault(
                r["code"], {a: [] for a in _MOMENTUM_ANCHOR_AXES},
            )
            for axis in _MOMENTUM_ANCHOR_AXES:
                per[axis].append(r[f"logret_{axis}"])
        return out

    async def fetch_vol_history(
        self, sec_type: str, codes: list[str],
    ) -> dict[str, dict[str, list[float | None]]]:
        """The trailing realized-vol panel rows (oldest-first) — the
        volatility bars' + depth normalizer's input."""
        # strategy/forecast_next_day/vol_history
        view = render_sql_view(
            "strategy/forecast_next_day/vol_history",
            vol_table=frag(VOL_TABLE),
            sec_type=SEC_TYPE_CODE[sec_type],
            codes=codes,
            row_limit=_VOL_ROW_LIMIT,
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        out: dict[str, dict[str, list[float | None]]] = {}
        for r in rows:
            per = out.setdefault(
                r["code"], {"hv_20": [], "ewma_vol": []},
            )
            per["hv_20"].append(r["hv_20"])
            per["ewma_vol"].append(r["ewma_vol"])
        return out

    async def fetch_ema_history(
        self, sec_type: str, codes: list[str],
    ) -> dict[str, tuple[list[float], list[float]]]:
        """The trailing typical-EMA rows (oldest-first) — the macd
        recompute's input."""
        # strategy/forecast_next_day/ema_history
        view = render_sql_view(
            "strategy/forecast_next_day/ema_history",
            tech_stats_table=frag(_tech_stats_table(sec_type)),
            codes=codes,
            row_limit=_EMA_ROW_LIMIT,
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        out: dict[str, tuple[list[float], list[float]]] = {}
        for r in rows:
            e12, e26 = out.setdefault(r["code"], ([], []))
            e12.append(r["ema12"])
            e26.append(r["ema26"])
        return out

    # -- the cells + day context -------------------------------------------

    async def fetch_active_cells(
        self, sec_type: str, codes: list[str], as_of: date,
    ) -> dict[str, list[dict]]:
        """The run's codes' ACTIVE RULE CELLS of the scenario-evaluable
        families (is_valid trading cells, wait + portfolio tiers
        excluded), post-processed like the live tier's fetch: labels
        decoded, bar_def/confidence parsed, the confidence base
        (base_pct) + starvation scale (sample_scale) merged into the
        confidence dict, window coerced int. Grouped per code."""
        type_codes = [SIGNAL_TYPE_CODE[t] for t in SCENARIO_FAMILIES]
        # strategy/forecast_next_day/active_cells
        view = render_sql_view(
            "strategy/forecast_next_day/active_cells",
            signals_table=frag(SIGNALS_TABLE),
            perf_table=frag(SIGNAL_PERFORMANCE_TABLE),
            as_of=as_of,
            sec_type=SEC_TYPE_CODE[sec_type],
            codes=codes,
            signal_type_codes=type_codes,
            wait_code=frag(str(ACTION_CODE["wait"])),
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        out: dict[str, list[dict]] = {}
        for r in rows:
            rec = dict(r)
            rec["signal_type"] = SIGNAL_TYPE_LABEL.get(
                rec["signal_type"], rec["signal_type"],
            )
            rec["signal_sub_type"] = SIGNAL_SUB_TYPE_LABEL.get(
                rec["signal_sub_type"], rec["signal_sub_type"],
            )
            rec["action_label"] = ACTION_LABEL[rec["action"]]
            for key in ("bar_def", "confidence"):
                if isinstance(rec.get(key), str):
                    rec[key] = json.loads(rec[key])
            conf = rec["confidence"] \
                if isinstance(rec["confidence"], dict) else {}
            base_pct = rec.pop("base_pct", None)
            if base_pct is not None:
                conf = {**conf, "base_pct": base_pct}
            sample_scale = rec.pop("sample_scale", None)
            if sample_scale is not None:
                conf = {**conf, "sample_scale": float(sample_scale)}
            rec["confidence"] = conf
            if rec["window"] is not None:
                rec["window"] = int(rec["window"])
            out.setdefault(rec["code"], []).append(rec)
        return out

    async def fetch_day_context(
        self, sec_type: str, codes: list[str], anchor: date,
    ) -> tuple[dict[str, int], dict[str, int]]:
        """The run's codes' day-context inputs at the anchor: the
        latest trend_days trend at-or-before the anchor (missing → the
        code is out — no cell can match) and the anchor's own
        market_regimes label (missing → 0 calm, the engine's own
        regime-join default)."""
        # strategy/forecast_next_day/trend_state
        trend_view = render_sql_view(
            "strategy/forecast_next_day/trend_state",
            trend_days_table=frag(TREND_DAYS_TABLE),
            sec_type=SEC_TYPE_CODE[sec_type],
            codes=codes,
            on_date=anchor,
        )
        trend_rows = await self._conn.fetch(
            trend_view.sql, *trend_view.params,
        )
        trends = {r["code"]: r["trend"] for r in trend_rows}
        # strategy/forecast_next_day/regime_state
        regime_view = render_sql_view(
            "strategy/forecast_next_day/regime_state",
            sec_type=SEC_TYPE_CODE[sec_type],
            codes=codes,
            on_date=anchor,
        )
        regime_rows = await self._conn.fetch(
            regime_view.sql, *regime_view.params,
        )
        regimes = {r["code"]: r["regime"] for r in regime_rows}
        return trends, regimes

    # -- the strategy pickup preconditions -----------------------------------

    async def fetch_position_state(
        self, strategy_name: str, sec_type_label: str,
        closes_by_code: dict[str, tuple[list[date], list[float]]],
    ) -> dict[str, PositionState]:
        """The nightly run's position state per code, mapped onto the
        closes ladder (the last BUY's ladder index + the position flag
        — would_execute's sell gate inputs)."""
        codes = sorted(closes_by_code)
        # strategy/forecast_next_day/position_state
        view = render_sql_view(
            "strategy/forecast_next_day/position_state",
            strategy_name=strategy_name,
            sec_type_label=sec_type_label,
            codes=codes,
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        out: dict[str, PositionState] = {}
        for r in rows:
            dates, _ = closes_by_code[r["code"]]
            idx_of = {d: i for i, d in enumerate(dates)}
            last_buy = r["last_buy_date"]
            out[r["code"]] = PositionState(
                position_open=bool(
                    r["position_after"] is not None
                    and float(r["position_after"]) > 0.0,
                ),
                last_buy_idx=idx_of.get(last_buy) if last_buy else None,
                ladder_len=len(dates),
            )
        return out

    async def fetch_strategy_horizon(
        self, strategy_name: str, sec_type_label: str, codes: list[str],
    ) -> dict[str, date]:
        """The run's codes' latest active strategy runs' end_dates —
        the strategy-freshness input."""
        # strategy/forecast_next_day/strategy_horizon
        view = render_sql_view(
            "strategy/forecast_next_day/strategy_horizon",
            strategy_name=strategy_name,
            sec_type_label=sec_type_label,
            codes=codes,
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        return {r["code"]: r["end_date"] for r in rows}

    async def fetch_analysis_horizon(self, sec_type: str) -> date | None:
        """The sec_type's ANALYSIS HORIZON (max date on the
        mov_ave_spreads_detail build) — the strategy runner's own cap:
        a strategy run can never end past it, so the freshness check
        targets min(bar anchor, this horizon)."""
        # strategy/forecast_next_day/analysis_horizon
        view = render_sql_view(
            "strategy/forecast_next_day/analysis_horizon",
            detail_table=frag("analysis.mov_ave_spreads_detail"),
            sec_type=SEC_TYPE_CODE[sec_type],
        )
        return await self._conn.fetchval(view.sql, *view.params)

    # -- the incremental input ----------------------------------------------

    async def fetch_existing_anchors(
        self, sec_type: str, codes: list[str],
    ) -> dict[str, date]:
        """The run's codes' latest registered forecast anchors — the
        incremental skip's input."""
        # strategy/forecast_next_day/existing_anchors
        view = render_sql_view(
            "strategy/forecast_next_day/existing_anchors",
            sec_type=SEC_TYPE_CODE[sec_type],
            codes=codes,
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        return {r["code"]: r["anchor_date"] for r in rows}

    # -- the force-mode purge -------------------------------------------------

    async def purge_scope(
        self, sec_type: str, codes: list[str],
    ) -> None:
        """--force: drop the run's codes' rows before regenerating."""
        write = render_sql_write(
            "strategy/forecast_next_day/writer/forecast_next_day/"
            "purge_scope",
            SEC_TYPE_CODE[sec_type], codes,
        )
        await self._conn.execute(write.sql, *write.params)


def build_scenario_bars(
    cells_by_code: dict[str, list[dict]],
    rsi_hist: dict[str, dict[int, list[float | None]]],
    momentum_hist: dict[str, dict[str, list[float | None]]],
    vol_hist: dict[str, dict[str, list[float | None]]],
) -> dict[str, ScenarioBars]:
    """Assemble each code's ScenarioBars from the REAL histories — the
    bar sets are demand-driven (each family's tail-pct grid read off
    the cells the code actually carries, so no bar is computed for a
    population that cannot fire)."""
    out: dict[str, ScenarioBars] = {}
    for code, cells in cells_by_code.items():
        rsi_pcts: set[int] = set()
        rsi_windows: set[int] = set()
        mom_pcts: set[int] = set()
        vol_pcts: set[int] = set()
        for cell in cells:
            bar_def = cell.get("bar_def") or {}
            pct = bar_def.get("tail_pct")
            if pct is None:
                continue
            if cell["signal_type"] == "mov_rsi":
                window = cell.get("window")
                if window is None:
                    continue
                rsi_pcts.add(int(pct))
                rsi_windows.add(int(window))
            elif cell["signal_type"] == "momentum" \
                    and cell["signal_sub_type"].rsplit("_", 1)[0] \
                    in MOMENTUM_ANCHOR_ROWS:
                mom_pcts.add(int(pct))
            elif cell["signal_type"] == "volatility":
                vol_pcts.add(int(pct))
        rsi_series = rsi_hist.get(code, {})
        mom_series = momentum_hist.get(code, {})
        vol_series = vol_hist.get(code, {})
        rsi_bars: dict[tuple[int, int, str], float] = {}
        for w in rsi_windows:
            hist = rsi_series.get(w) or []
            for pct, bar in _tail_bars(
                hist, tuple(sorted(rsi_pcts)), RSI_BAR_WINDOW_DAYS,
                RSI_BAR_MIN_PERIODS, buy_is_bottom=True,
            ).items():
                rsi_bars[(w, *pct)] = bar
        mom_bars: dict[tuple[int, int, str], float] = {}
        mom_sigma: dict[int, float] = {}
        for label, rows in MOMENTUM_ANCHOR_ROWS.items():
            axis = f"{rows}days"
            hist = mom_series.get(axis) or []
            for pct, bar in _tail_bars(
                hist, tuple(sorted(mom_pcts)), MOMENTUM_BAR_WINDOW_DAYS,
                MOMENTUM_BAR_MIN_PERIODS, buy_is_bottom=True,
            ).items():
                mom_bars[(rows, *pct)] = bar
            sigma = _trailing_sigma(
                hist, MOMENTUM_BAR_WINDOW_DAYS, MOMENTUM_BAR_MIN_PERIODS,
            )
            if sigma is not None:
                mom_sigma[rows] = sigma
        vol_bars: dict[tuple[str, int, str], float] = {}
        vol_sigma: dict[str, float] = {}
        for prefix in ("hv20", "ewma"):
            col = "hv_20" if prefix == "hv20" else "ewma_vol"
            hist = vol_series.get(col) or []
            # the spike BUY breaches the TOP tail — the side convention
            # is INVERTED vs mov_rsi/momentum
            for pct, bar in _tail_bars(
                hist, tuple(sorted(vol_pcts)), VOL_BAR_WINDOW_DAYS,
                VOL_BAR_MIN_PERIODS, buy_is_bottom=False,
            ).items():
                vol_bars[(prefix, *pct)] = bar
            sigma = _trailing_sigma(
                hist, VOL_BAR_WINDOW_DAYS, VOL_BAR_MIN_PERIODS,
            )
            if sigma is not None:
                vol_sigma[prefix] = sigma
        out[code] = ScenarioBars(
            rsi=rsi_bars,
            momentum=mom_bars,
            momentum_sigma=mom_sigma,
            volatility=vol_bars,
            volatility_sigma=vol_sigma,
        )
    return out
