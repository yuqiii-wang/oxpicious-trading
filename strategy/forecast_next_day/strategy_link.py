"""StrategyFreshness — the forecast's auto-link to the strategy run
(strategy.forecast_next_day.strategy_link).

The forecast's trigger reading applies to a CURRENT strategy position
state, so before forecasting the module checks the nightly mixed
portfolio's latest ACTIVE run per code: when its end_date is behind
the OHLCV bar horizon (new bars arrived since — the "missing latest
dates"), the module spawns ``python -m strategy.singleton_trading``
with the NIGHTLY TIER's own step-1 arguments verbatim (read from
scripts/orchestration/strategy_daily.json — the single source of the
family list; no duplicated config), the ``_run_trained_strategy``
precedent (strategy/factors_and_algos/_optm_engine/__main__.py): a
blocking subprocess in a thread whose exit code fails the forecast
honestly. In the nightly chain the tier ordering already ran the
strategy (step 1 before step 2) — the check then passes without
spawning; the spawn is the self-heal for manual / mid-day runs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

from asyncpg import Connection

from strategy._trading.constants import DEFAULT_FAULT_TOLERANCE
from strategy.forecast_next_day import NIGHTLY_TIER_JSON
from strategy.forecast_next_day.fetch import ForecastFetch
from strategy.factors_and_algos.portfolio import portfolio_name

logger = logging.getLogger(__name__)


class StrategyFreshness:
    """The nightly portfolio's identity: its CLI args (from the tier
    JSON), its strategy_name, and the behind-horizon check + self-
    spawn."""

    def __init__(self, repo_root: Path) -> None:
        self._tier_path = repo_root / NIGHTLY_TIER_JSON

    # -- the nightly tier's step 1 (the single source) -------------------

    def nightly_args(self) -> list[str]:
        """The strategy_daily tier's step-1 CLI args verbatim — the
        spawn reuses the canonical family list / sec_type; a malformed
        or missing tier file raises (the auto-link is a hard contract,
        not a best effort)."""
        raw = json.loads(self._tier_path.read_text(encoding="utf-8"))
        step = raw["1"]
        module = step["script"]
        if module != "strategy.singleton_trading":
            raise ValueError(
                f"{NIGHTLY_TIER_JSON} step 1 is no longer the strategy "
                f"run (found {module!r}) — update the forecast link",
            )
        return list(step.get("args", []))

    def strategy_name(self) -> str:
        """The nightly mixed portfolio's strategy_name (portfolio_name
        over the tier's --algo list at the pinned default fault
        tolerance — the same construction the nightly run writes)."""
        args = self.nightly_args()
        algo = args[args.index("--algo") + 1]
        selection = {
            name.strip(): 1.0
            for name in algo.split(",") if name.strip()
        }
        return portfolio_name(selection, fault_tolerance=DEFAULT_FAULT_TOLERANCE)

    # -- the freshness check + self-spawn ---------------------------------

    async def stale_codes(
        self,
        fetch: ForecastFetch,
        conn: Connection,
        sec_type_label: str,
        anchors: dict[str, date],
    ) -> list[str]:
        """The codes whose latest ACTIVE run ends before the dates the
        strategy CAN run — min(bar anchor, the analysis horizon): the
        runner caps its backtest at the analysis build's last date, so
        an analysis tier lagging behind the closes must not trigger a
        re-spawn the runner would cap anyway."""
        if not anchors:
            return []
        analysis_horizon = await fetch.fetch_analysis_horizon(
            sec_type_label,
        )
        horizon = await fetch.fetch_strategy_horizon(
            self.strategy_name(), sec_type_label, sorted(anchors),
        )
        stale: list[str] = []
        for code, anchor in anchors.items():
            target = anchor \
                if analysis_horizon is None else min(anchor, analysis_horizon)
            if code not in horizon or horizon[code] < target:
                stale.append(code)
        return sorted(stale)

    async def ensure_fresh(
        self,
        fetch: ForecastFetch,
        conn: Connection,
        sec_type_label: str,
        anchors: dict[str, date],
    ) -> bool:
        """Spawn the strategy CLI when any code is behind (unless the
        run is already current). Returns True when a spawn ran. A
        failed child logs a CAUTION row and re-raises — the forecast
        must not read a stale position state silently."""
        stale = await self.stale_codes(
            fetch, conn, sec_type_label, anchors,
        )
        if not stale:
            return False
        args = self.nightly_args()
        cmd = [
            sys.executable, "-m", "strategy.singleton_trading", *args,
        ]
        logger.info(
            "strategy behind bar horizon for %d code(s) (%s…) — "
            "spawning the nightly strategy run: %s",
            len(stale), ", ".join(stale[:5]), " ".join(cmd[1:]),
        )
        import subprocess
        completed = await asyncio.to_thread(subprocess.run, cmd)
        logger.info(
            "strategy freshness spawn exit code %d", completed.returncode,
        )
        if completed.returncode != 0:
            from _common.caution_log import record
            record(
                "service", "error",
                "forecast_next_day strategy-freshness spawn failed — "
                "the forecast runs against a stale position state",
                dedupe_key="forecast_next_day:strategy-spawn-failed",
                detail={"algo_args": args, "exit_code":
                        completed.returncode},
            )
            raise RuntimeError(
                "strategy.singleton_trading spawn failed "
                f"(exit {completed.returncode}) — forecast aborted",
            )
        return True
