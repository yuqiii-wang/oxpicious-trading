"""CLI entry: ``python -m strategy.forecast_next_day``.

The ±kσ next-day scenario forecast (see the package docstring). Per
code: the trailing-20d σ of daily returns projects six next-day closes;
each scenario evaluates the active rule cells of the scenario-evaluable
families against the synthetic close and registers the fired signals
(the summary row + one row per fired cell) into
strategy.forecast_next_day.

Incremental by default: a code whose registered anchor already matches
its OHLCV horizon is skipped (reads filter on the target table's PK);
``--force`` purges the run's scope and regenerates. The AUTO-LINK
(strategy_link.StrategyFreshness) spawns the nightly strategy run
first when its latest active run is behind the bar horizon —
``--skip-strategy`` opts out (the tier ordering usually made it fresh
already).

The :class:`ForecastNextDayPipeline` entry class (a
:class:`StrategyPipeline` subclass) owns the runtime + connection
lifecycle; the work lives in run(). Bootstrap-first layout: the
runtime setup runs before this module's analysis-importing imports
(values/evaluation pull the analysis configs).
"""
from __future__ import annotations

import logging
from pathlib import Path

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the analysis imports below (they pull
# pandas through the config/fetch modules; the hook patches first).
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.labels import SEC_TYPE_CODE  # noqa: E402

from strategy._common.strategy_base import StrategyPipeline  # noqa: E402
from strategy.forecast_next_day import (  # noqa: E402
    SCENARIO_FAMILIES,
    SKIPPED_FAMILIES,
    STUDY_CODES,
)
from strategy.forecast_next_day.evaluation import (  # noqa: E402
    ForecastCellEvaluator,
    PositionState,
    ScenarioBars,
)
from strategy.forecast_next_day.fetch import (  # noqa: E402
    ForecastFetch,
    build_scenario_bars,
)
from strategy.forecast_next_day.scenarios import SigmaLadder  # noqa: E402
from strategy.forecast_next_day.strategy_link import StrategyFreshness  # noqa: E402
from strategy.forecast_next_day.values import ScenarioValues  # noqa: E402
from strategy.forecast_next_day.writer import (  # noqa: E402
    ForecastRow,
    ForecastWriter,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


class ForecastNextDayPipeline(StrategyPipeline):
    """``python -m strategy.forecast_next_day`` — the σ-ladder forecast
    register writer."""

    component = "forecast_next_day"
    title = "forecast_next_day"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--sec-type", choices=("index", "etf", "stock"),
            default="index",
            help="The forecast's security class (the mandatory study "
                 "scope runs index).",
        )
        parser.add_argument(
            "--codes", nargs="+", default=None,
            help=f"Codes to forecast (default: the {len(STUDY_CODES)} "
                 f"study indices).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Purge the run's codes' rows and regenerate (default: "
                 "incremental — codes whose anchor is current are "
                 "skipped).",
        )
        parser.add_argument(
            "--skip-strategy", action="store_true",
            help="Skip the strategy-freshness self-spawn (the auto-link "
                 "that runs strategy.singleton_trading when the nightly "
                 "portfolio is behind the bar horizon).",
        )

    def apply_args(self) -> None:
        self.codes = sorted(self.args.codes or STUDY_CODES)
        self.sec_type = self.args.sec_type

    def header_fields(self) -> dict:
        return {
            "sec_type": self.sec_type,
            "codes": len(self.codes),
            "families": "+".join(SCENARIO_FAMILIES),
            "skipped": "+".join(SKIPPED_FAMILIES),
        }

    async def run(self) -> None:
        conn = self.conn
        fetch = ForecastFetch(conn)
        writer = ForecastWriter(conn)
        sec_label = self.sec_type
        sec_code = SEC_TYPE_CODE[sec_label]

        # 1. the price ladders → the per-code anchors (the OHLCV horizon)
        closes_by_code = await fetch.fetch_closes(sec_label, self.codes)
        anchors = {
            code: dates[-1]
            for code, (dates, closes) in closes_by_code.items()
            if closes
        }
        if not anchors:
            logger.warning("no close history for the run's codes — done")
            return

        # 2. the incremental skip (or the --force purge + regenerate)
        if self.args.force:
            await fetch.purge_scope(sec_label, sorted(anchors))
            existing: dict = {}
        else:
            existing = await fetch.fetch_existing_anchors(
                sec_label, sorted(anchors),
            )
        todo = [
            code for code in sorted(anchors)
            if existing.get(code) != anchors[code]
        ]
        logger.info(
            "anchors: %d code(s) with data, %d to forecast "
            "(%d current, skipped)",
            len(anchors), len(todo), len(anchors) - len(todo),
        )
        if not todo:
            return

        # 3. THE AUTO-LINK — the strategy-freshness self-spawn (the
        # trigger reading applies to a current position state)
        freshness = StrategyFreshness(_REPO_ROOT)
        if not self.args.skip_strategy:
            await freshness.ensure_fresh(
                fetch, conn, sec_label,
                {code: anchors[code] for code in todo},
            )
        strategy_name = freshness.strategy_name()

        # 4. the per-run reads (the TODO codes only); the anchor is the
        # confidence-base as-of bound, so the cells fetch runs per
        # anchor group (codes share the horizon in the usual aligned run)
        by_anchor: dict = {}
        for code in todo:
            by_anchor.setdefault(anchors[code], []).append(code)
        cells_by_code: dict = {}
        for anchor, group in sorted(by_anchor.items()):
            cells_by_code.update(
                await fetch.fetch_active_cells(sec_label, group, anchor),
            )
        rsi_hist = await fetch.fetch_rsi_history(sec_label, todo)
        mom_hist = await fetch.fetch_momentum_history(sec_label, todo)
        vol_hist = await fetch.fetch_vol_history(sec_label, todo)
        ema_hist = await fetch.fetch_ema_history(sec_label, todo)
        bars_by_code = build_scenario_bars(
            cells_by_code, rsi_hist, mom_hist, vol_hist,
        )

        # 5. the day context + the pickup preconditions
        trends: dict = {}
        regimes: dict = {}
        for anchor, group in sorted(by_anchor.items()):
            t, r = await fetch.fetch_day_context(sec_label, group, anchor)
            trends.update(t)
            regimes.update(r)
        positions = await fetch.fetch_position_state(
            strategy_name, sec_label, closes_by_code,
        )

        # 6. the σ ladder + the scenario evaluation per code
        ladder = SigmaLadder()
        rows: list[ForecastRow] = []
        fired_by_family: dict[str, int] = {}
        for code in todo:
            dates, closes = closes_by_code[code]
            specs = ladder.scenarios(code, anchors[code], closes)
            if not specs:
                logger.warning(
                    "[%s] underivable σ ladder (%d closes) — skipped",
                    code, len(closes),
                )
                continue
            ewma_series = [
                v for v in vol_hist.get(code, {}).get("ewma_vol", [])
                if v is not None
            ]
            ema12, ema26 = ema_hist.get(code, ([], []))
            values = ScenarioValues(
                closes, ema12, ema26,
                ewma_series[-1] if ewma_series else None,
            )
            evaluator = ForecastCellEvaluator(
                cells_by_code.get(code, []),
                bars_by_code.get(code, ScenarioBars(
                    rsi={}, momentum={}, momentum_sigma={},
                    volatility={}, volatility_sigma={},
                )),
                trends.get(code),
                regimes.get(code, 0),
            )
            position = positions.get(code) or PositionState(
                ladder_len=len(closes),
            )
            sigma_pct = specs[0].sigma_20d * 100.0
            logger.info(
                "[%s] anchor %s close %.2f σ20 %.4f%% — %d active "
                "cells, trend=%s regime=%s, position_open=%s",
                code, anchors[code], closes[-1], sigma_pct,
                len(cells_by_code.get(code, [])),
                trends.get(code), regimes.get(code, 0),
                position.position_open,
            )
            for spec in specs:
                fired = evaluator.evaluate(
                    values, spec.forecast_close, position,
                )
                rows.append(ForecastRow.summary(spec, sec_code,
                                                strategy_name))
                for trigger_no, sig in enumerate(fired, start=1):
                    rows.append(ForecastRow.fired(
                        spec, sec_code, strategy_name, trigger_no, sig,
                    ))
                    fired_by_family[sig.signal_type] = \
                        fired_by_family.get(sig.signal_type, 0) + 1

        # 7. the register write + the run summary
        n_rows = await writer.upsert(rows)
        n_summary = sum(1 for r in rows if r.trigger_no == 0)
        logger.info(
            "forecast registered: %d row(s) across %d code(s) — %d "
            "scenarios (%d summary rows), %d fired signal(s) %s; "
            "not-evaluable families skipped: %s",
            n_rows, len(todo), n_summary, n_summary,
            n_rows - n_summary,
            dict(sorted(fired_by_family.items())) or "—",
            ", ".join(SKIPPED_FAMILIES),
        )


if __name__ == "__main__":
    ForecastNextDayPipeline().execute()
