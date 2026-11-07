"""SSE options flow — tstyle endpoints → sse_options_intraday CSV → options_intraday_5min.

Streams the per-(underlying, expiry-month) live quotes behind
https://www.sse.com.cn/assortment/options/price/ (see
``downloads/_common/exchanges/sse_options.py`` for the endpoint study) as a
fourth asset of ``downloads.stream.sse.price``:

  * universe discovery — 5 underlyings + live expiry months
    (``stockexpire``) + {contractid → (numeric 合约编码, 合约简称)} map
    (当日合约 listing); refreshed on startup and on trade-date rollover
  * poll — every cycle sweeps all ≈20 code-month ``tstyle`` endpoints into
    ONE snapshot keyed by contractid (raw sample archived to
    ``temps/sse_options_intraday/sse_options_intraday_YYYYMMDD.csv``)
  * aggregation — 5 samples → one 5-min bar per contract; OHLC from the
    ``last`` samples (stock-stream parity); per-bar ``volume``/``amount``
    are SUBTRACTED from the source's day-cumulative values; bar time is
    clamped to CLOSE_TIME so a post-close sample lands on the 15:00 bar
  * identity — the stream writes ``stats.options_identity`` rows itself
    (numeric contract_code + name from the day-contract map, exchange
    'SSE'), satisfying the FK of ``stats.options_intraday_5min``
  * OI estimate — each bar also lands in ``live.options_intraday_oi``:
    ``open_interest_est = oi_base + cum_volume`` (GENERATED column) where
    ``oi_base`` is the contract's prev-trading-day daily OI
    (stats.options_volume_oi; 0 for SSE — no daily per-contract source)
    and ``cum_volume`` is the day-cumulative traded volume. An upper-bound
    estimator: closes / rolls are not netted out.

A single post-close sample carries the day's final OHLC + day totals, so
``run_options_eod_capture`` (``--options-eod``) fetches once and emits the
15:00 EOD bar per contract — the same capture the future daily build will
reuse. Special case: with exactly ONE sample in the buffer the bar's
open/high/low come from the sample's day-level fields (after close they are
the full-day values; deriving them from a single ``last`` would collapse
O=H=L=C).

Class shape: the mutators that took the stream as their subject
(``refresh_options_universe``, ``load_options_oi_base``,
``write_options_snapshot_csv``, ``aggregate_options_bars``,
``prepopulate_options_finished_codes``, ``backfill_options_asset``) are
methods on :class:`OptionsStream` — the ``conn`` / ``session`` / ``proxy``
arguments stay explicit parameters (never hidden state), and
``write_options_snapshot_csv``'s explicit ``contract_map`` argument was
folded into ``self.contract_map`` (every caller passed the asset's own map).
The factories and conn/session-first orchestrators that construct or use an
asset without mutating it (``build_options_asset``, ``fetch_oi_base``,
``load_options_bars``, ``run_options_eod_capture``) and the pure CSV parsing
helpers stay module-level.
"""
from __future__ import annotations

import csv
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from downloads._common import (
    AntiBotProxy,
    resolve_out_dir,
    setup_logger,
)
from downloads._common.exchanges.sse_options import (
    OPTIONS_CSV_COLUMNS,
    fetch_day_contract_map,
    fetch_expire_months,
    fetch_options_snapshot,
    fetch_underlyings,
)
from _common.db_commons import bulk_upsert




from ._csv_backfill import _extract_date_from_filename, _file_last_window
from ._io import write_snapshot_csv
from ._model import CLOSE_TIME, ceiling_5min

from downloads.stream.sse.price.data_definitions import (
    OptionsStream,
    SERVICE,
    _group_options_csv_by_windows,
    _options_day_complete,
    _parse_options_csv_row,
    fetch_oi_base,
    load_options_bars,
)
logger = setup_logger("stream_sse")

TSTYLE_SWEEP_SLEEP_SEC = 0.3  # between code-month requests in one sweep




# ---------------------------------------------------------------------------
# Universe discovery — factory
# ---------------------------------------------------------------------------




def build_options_asset(
    session: requests.Session,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[OptionsStream]:
    """Construct the options asset; None if the universe discovery fails."""
    asset = OptionsStream()
    if asset.refresh_options_universe(session, proxy):
        return asset
    return None




# ---------------------------------------------------------------------------
# EOD capture (--options-eod / future daily build)
# ---------------------------------------------------------------------------
def run_options_eod_capture(
    session: requests.Session,
    conn,
    proxy: Optional[AntiBotProxy] = None,
    service: str = "downloads.stream.sse.price",
) -> Tuple[int, int]:
    """Fetch the current tstyle state once and emit the 15:00 EOD bars.

    After close the tstyle snapshot is the day's final state (day OHLC +
    day-cumulative volume/amount), so ONE sample per contract becomes the
    15:00 bar (single-sample OHLC semantics, clamped bar time). The same
    snapshot is ALSO archived as the day's daily CSV
    (temps/sse_options_daily/, one row per contract with volume + amount —
    the source builds.options.sse quotes off). Idempotent: re-running
    upserts the same PK rows. Returns (n_contracts, n_bars).
    """
    asset = build_options_asset(session, proxy)
    if asset is None:
        return 0, 0

    update_dt, snapshot = fetch_options_snapshot(
        session, asset.months, service=service, proxy=proxy,
        inter_request_sec=TSTYLE_SWEEP_SLEEP_SEC,
    )
    if update_dt is None or not snapshot:
        logger.warning("options EOD capture: no snapshot returned")
        return 0, 0

    csv_path = asset.write_options_snapshot_csv(update_dt, snapshot)
    # The SAME post-close snapshot is the day's DAILY state (close + day
    # OHLC + day-cumulative 成交量(手)/成交额), so the EOD capture also
    # writes the one-row-per-contract daily archive the options build
    # quotes off — builds.options.sse reads temps/sse_options_daily
    # (volume AND amount), never by aggregating this stream's intraday CSV.
    daily_path = asset.write_options_snapshot_csv(
        update_dt, snapshot,
        csv_subdir="sse_options_daily", csv_prefix="sse_options_daily",
    )
    logger.info(
        "options EOD capture: %d contracts @ %s → %s (+daily %s)",
        len(snapshot), update_dt.strftime("%Y-%m-%d %H:%M:%S"),
        csv_path.name, daily_path.name,
    )

    asset.buffer = [(update_dt, snapshot)]
    asset.load_options_oi_base(conn, update_dt.date())
    identity_rows, bar_rows, oi_rows, bar_time = asset.aggregate_options_bars(update_dt.date())
    if bar_rows:
        load_options_bars(conn, asset, identity_rows, bar_rows, oi_rows)
        logger.info(
            "options EOD capture: upserted %d identity + %d bars + %d OI rows "
            "for %s %s",
            len(identity_rows), len(bar_rows), len(oi_rows),
            update_dt.date(), bar_time,
        )
    return len(identity_rows), len(bar_rows)
