"""ForecastWriter — the forecast register's write boundary
(strategy.forecast_next_day.writer).

The row contract is the pydantic ForecastRow (the strategy.forecast_
next_day table's columns verbatim — typed payloads cross the module
boundary, model_dump() at the write boundary); rows upsert through the
shared ``bulk_upsert_async`` helper (the table's full PK as the
conflict keys — a re-run of the same anchor overwrites in place), and
--force purges the run's scope first through the
strategy/forecast_next_day/writer/forecast_next_day/purge_scope write
action.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from asyncpg import Connection
from pydantic import BaseModel, Field

from _common.labels import (
    ACTION_CODE,
    FORECAST_DIRECTION_CODE,
    SEC_TYPE_CODE,
    SIGNAL_SUB_TYPE_CODE,
    SIGNAL_TYPE_CODE,
)

from strategy.forecast_next_day import FORECAST_TABLE
from strategy.forecast_next_day.evaluation import FiredSignal
from strategy.forecast_next_day.scenarios import ScenarioSpec

# the table's full PK (bulk_upsert's conflict keys)
FORECAST_PK = [
    "anchor_date", "sec_type", "code", "direction", "sigma_mult",
    "trigger_no",
]


class ForecastRow(BaseModel):
    """One strategy.forecast_next_day row.

    trigger_no 0 is the scenario SUMMARY row (no signal fired — the
    signal columns NULL); 1..n carry one fired cell each. The scenario
    constants are denormalized on every row."""

    anchor_date: date
    sec_type: int
    code: str
    direction: int
    sigma_mult: int
    trigger_no: int
    base_close: float
    sigma_20d_pct: float
    forecast_close: float
    forecast_change_pct: float
    signal_type: int | None = None
    signal_sub_type: int | None = None
    action: int | None = None
    signal_value: float | None = None
    signal_threshold: float | None = None
    signal_excess_pct: float | None = None
    confidence_bp: int | None = None
    trend_state: int | None = None
    regime_state: int | None = None
    position_open: bool | None = None
    would_execute: bool | None = None
    strategy_name: str
    # the run-time stamp — carried in the payload so the shared upsert's
    # DO UPDATE refreshes it on every re-registration of the anchor
    # (created_at stays the DB default: the FIRST registration only)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def summary(
        cls, spec: ScenarioSpec, sec_type: int, strategy_name: str,
    ) -> "ForecastRow":
        """The trigger_no 0 marker row — the scenario registers even
        when nothing fires."""
        return cls(
            anchor_date=spec.anchor_date,
            sec_type=sec_type,
            code=spec.code,
            direction=FORECAST_DIRECTION_CODE[spec.direction],
            sigma_mult=spec.sigma_mult,
            trigger_no=0,
            base_close=round(spec.base_close, 4),
            sigma_20d_pct=round(spec.sigma_20d * 100.0, 8),
            forecast_close=round(spec.forecast_close, 4),
            forecast_change_pct=round(spec.forecast_change_pct, 8),
            strategy_name=strategy_name,
        )

    @classmethod
    def fired(
        cls, spec: ScenarioSpec, sec_type: int, strategy_name: str,
        trigger_no: int, sig: FiredSignal,
    ) -> "ForecastRow":
        """One fired-cell row (trigger_no >= 1)."""
        return cls(
            anchor_date=spec.anchor_date,
            sec_type=sec_type,
            code=spec.code,
            direction=FORECAST_DIRECTION_CODE[spec.direction],
            sigma_mult=spec.sigma_mult,
            trigger_no=trigger_no,
            base_close=round(spec.base_close, 4),
            sigma_20d_pct=round(spec.sigma_20d * 100.0, 8),
            forecast_close=round(spec.forecast_close, 4),
            forecast_change_pct=round(spec.forecast_change_pct, 8),
            signal_type=SIGNAL_TYPE_CODE[sig.signal_type],
            signal_sub_type=SIGNAL_SUB_TYPE_CODE[sig.signal_sub_type],
            action=ACTION_CODE[sig.action],
            signal_value=sig.signal_value,
            signal_threshold=sig.signal_threshold,
            signal_excess_pct=sig.signal_excess_pct,
            confidence_bp=sig.confidence_bp,
            trend_state=sig.trend_state,
            regime_state=sig.regime_state,
            position_open=sig.position_open,
            would_execute=sig.would_execute,
            strategy_name=strategy_name,
        )


class ForecastWriter:
    """The register's upsert (and --force purge, delegated to
    ForecastFetch.purge_scope's write action)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    async def upsert(self, rows: list[ForecastRow]) -> int:
        """Upsert the rows on the table's full PK (a re-run of the same
        anchor overwrites in place). Returns the row count."""
        from _common.db_commons import bulk_upsert_async
        if not rows:
            return 0
        await bulk_upsert_async(
            self._conn, FORECAST_TABLE,
            [r.model_dump() for r in rows],
            key_columns=FORECAST_PK,
        )
        return len(rows)

    @staticmethod
    def sec_type_code(sec_type_label: str) -> int:
        """The sec_type label → its static.label_dim code (the table's
        SMALLINT column)."""
        return SEC_TYPE_CODE[sec_type_label]
