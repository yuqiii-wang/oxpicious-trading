"""OptionsStream — data definition.

Migrated verbatim from :mod:`downloads.stream.sse.price._options` (one file per ``@dataclass``)."""

from __future__ import annotations

import csv
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests
from downloads._common import AntiBotProxy, resolve_out_dir, setup_logger
from downloads._common.exchanges.sse_options import OPTIONS_CSV_COLUMNS, fetch_day_contract_map, fetch_expire_months, fetch_underlyings
from _common.db_commons import bulk_upsert
from .._csv_backfill import _extract_date_from_filename, _file_last_window
from .._io import write_snapshot_csv
from .asset_stream import CLOSE_TIME, ceiling_5min


SERVICE = "downloads.stream.sse.price"


def _group_options_csv_by_windows(
    csv_path: Path,
) -> Dict[time, List[Tuple[datetime, Dict[str, dict]]]]:
    """Group an options CSV's rows into 5-min windows of snapshots."""
    windows: Dict[time, List[Tuple[datetime, Dict[str, dict]]]] = {}
    snapshots: Dict[datetime, Dict[str, dict]] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            parsed = _parse_options_csv_row(row)
            if parsed is None:
                continue
            dt, code, rec = parsed
            snapshots.setdefault(dt, {})[code] = rec
    for dt in sorted(snapshots.keys()):
        wend = min(ceiling_5min(dt.time()), CLOSE_TIME)
        windows.setdefault(wend, []).append((dt, snapshots[dt]))
    return windows


def _options_day_complete(
    conn,
    asset: OptionsStream,
    trade_date: date,
    last_window: Optional[time],
) -> bool:
    """Options completeness: latest SSE bar reached CLOSE_TIME (or the CSV's
    own last window on short days). The generic ratio-based check does not
    apply — options_identity also holds SZSE/CFFEX codes that never stream.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT MAX(time) FROM {asset.intraday_table} "
                "WHERE date = %s AND exchange = 'SSE'",
                (trade_date,),
            )
            max_time = cur.fetchone()[0]
        if max_time is None:
            return False
        if max_time >= CLOSE_TIME:
            return True
        return last_window is not None and max_time >= last_window
    except Exception as e:  # noqa: BLE001
        logger.warning("options completeness check failed for %s: %s", trade_date, e)
        return False


def _parse_options_csv_row(row: dict) -> Optional[Tuple[datetime, str, dict]]:
    """Parse one options CSV row into (update_dt, contractid, record)."""
    raw_time = row.get("update_time", "")
    code = (row.get("code") or "").strip()
    if not raw_time or not code:
        return None
    try:
        dt = datetime.strptime(raw_time.strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    rec: Dict[str, object] = {"name": row.get("name") or ""}
    for k in ("open", "high", "low", "last", "volume", "amount"):
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            rec[k] = float(v)
        except (ValueError, TypeError):
            pass
    return dt, code, rec


def fetch_oi_base(conn, trade_date) -> Dict[str, float]:
    """Per-contract daily OI of the latest trading day STRICTLY BEFORE
    ``trade_date`` (stats.options_volume_oi) — the base the intraday
    estimator adds the day's traded volume onto. SSE rows carry 0 (no
    per-contract daily OI source); contracts absent here (new listings)
    base at 0 in the aggregation."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT contract_code, COALESCE(open_interest, 0) "
                "FROM stats.options_volume_oi "
                "WHERE date = (SELECT MAX(date) FROM stats.options_volume_oi "
                "             WHERE date < %s)",
                (trade_date,),
            )
            return {row[0]: float(row[1] or 0.0) for row in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load options OI base for %s: %s", trade_date, e)
        return {}


def load_options_bars(
    conn,
    asset: OptionsStream,
    identity_rows: List[dict],
    bar_rows: List[dict],
    oi_rows: Optional[List[dict]] = None,
) -> None:
    """Upsert identity rows (FK parents) then option bars and OI estimates."""
    if identity_rows:
        seen = set()
        uniq = []
        for r in identity_rows:
            k = (r["date"], r["contract_code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, asset.identity_table, uniq, ["date", "contract_code"])
    if bar_rows:
        bulk_upsert(conn, asset.intraday_table, bar_rows, ["date", "contract_code", "time"])
    if oi_rows:
        # GENERATED column open_interest_est is computed server-side — the
        # rows carry only its inputs (oi_base, cum_volume).
        bulk_upsert(conn, asset.oi_table, oi_rows, ["date", "contract_code", "time"])


logger = setup_logger("stream_sse")


@dataclass
class OptionsStream:
    """Streaming context for the SSE ETF-options asset.

    Attribute names deliberately mirror ``AssetStream`` (name,
    identity_table, intraday_table, exchange, csv_subdir/prefix, buffer,
    finished_codes) so the generic progress/completeness helpers in
    ``_io.py`` keep working; options-specific state (cumulative-amount
    baseline, universe, contract map) lives alongside.

    exchange is None — NOT the suffixed-code convention: options rows key
    on the numeric contract_code, and bars carry a literal 'SSE' exchange
    column instead (options_intraday_5min schema).

    Methods below are the former module-level mutator functions of this
    module (self is the asset; conn/session/proxy stay explicit parameters).
    """

    name: str = "options"
    list_url: str = ""  # unused: options sweep ≈20 code-month URLs, not one
    identity_table: str = "stats.options_identity"
    intraday_table: str = "stats.options_intraday_5min"
    oi_table: str = "live.options_intraday_oi"
    # The generic progress/completeness helpers key on this column — the
    # options tables use contract_code where stock/etf/index use code.
    code_column: str = "contract_code"
    exchange: Optional[str] = None
    has_volume: bool = True
    allowed_codes: Optional[set] = None
    csv_subdir: str = "sse_options_intraday"
    csv_prefix: str = "sse_options_intraday"

    underlyings: List[str] = field(default_factory=list)
    months: Dict[str, List[str]] = field(default_factory=dict)
    contract_map: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    # numeric contract_code -> prev-trading-day daily OI (the estimator's
    # base); contracts absent from the map (new listings) base at 0.
    oi_base: Dict[str, float] = field(default_factory=dict)

    buffer: List[Tuple[datetime, Dict[str, dict]]] = field(default_factory=list)
    prev_bar_cumvol: Dict[str, float] = field(default_factory=dict)
    prev_bar_cumamt: Dict[str, float] = field(default_factory=dict)
    finished_codes: set = field(default_factory=set)

    # -----------------------------------------------------------------------
    # Universe discovery (promoted from refresh_options_universe(asset, ...))
    # -----------------------------------------------------------------------
    def refresh_options_universe(
        self,
        session: requests.Session,
        proxy: Optional[AntiBotProxy] = None,
    ) -> bool:
        """(Re)discover underlyings, expiry months and the contract-code map.

        Called at startup and whenever the trade date rolls over (new contract
        listings / expiries). Returns True when the months map is usable.
        """
        underlyings = fetch_underlyings(session, service=SERVICE, proxy=proxy)
        months = fetch_expire_months(session, service=SERVICE, proxy=proxy)
        contract_map = fetch_day_contract_map(session, proxy=proxy, service=SERVICE)
        if not months:
            logger.warning(
                "options universe refresh: no expiry months returned "
                "(underlyings=%d, contract_map=%d) — options asset disabled this cycle",
                len(underlyings), len(contract_map),
            )
            return False
        self.underlyings = underlyings
        self.months = months
        self.contract_map = contract_map
        n_months = sum(len(v) for v in months.values())
        logger.info(
            "options universe: %d underlyings × months = %d code-month endpoints, "
            "%d mapped contracts",
            len(underlyings), n_months, len(contract_map),
        )
        if not contract_map:
            logger.warning(
                "options universe: contract map empty — bars cannot resolve "
                "numeric contract codes; snapshot will still be archived to CSV"
            )
        return True

    # -----------------------------------------------------------------------
    # OI estimator base (promoted from load_options_oi_base(conn, asset, ...))
    # -----------------------------------------------------------------------
    def load_options_oi_base(self, conn, trade_date) -> None:
        """(Re)load the asset's OI base map — at startup and on trade-date
        rollover (the base advances one daily snapshot per trading day)."""
        self.oi_base = fetch_oi_base(conn, trade_date)
        logger.info(
            "options OI base loaded: %d contracts (prev daily snapshot before %s)",
            len(self.oi_base), trade_date,
        )

    # -----------------------------------------------------------------------
    # CSV archive (promoted from write_options_snapshot_csv(..., contract_map))
    # — the explicit contract_map argument was folded into self.contract_map
    # (every caller passed the asset's own map).
    # -----------------------------------------------------------------------
    def write_options_snapshot_csv(
        self,
        update_dt: datetime,
        snapshot: Dict[str, dict],
        csv_subdir: str = "sse_options_intraday",
        csv_prefix: str = "sse_options_intraday",
    ) -> Path:
        """Append one options snapshot to the daily CSV (raw cumulative values).

        Names come from the day-contract map (tstyle carries no name); ``month``
        is the YYMM expiry encoded in the contractid.
        """
        enriched: Dict[str, dict] = {}
        for code, rec in snapshot.items():
            sec = self.contract_map.get(code)
            row = dict(rec)
            row["name"] = sec[1] if sec else ""
            row["month"] = rec.get("yymm", "")
            enriched[code] = row
        return write_snapshot_csv(
            update_dt, enriched,
            csv_subdir=csv_subdir, csv_prefix=csv_prefix,
            fieldnames=OPTIONS_CSV_COLUMNS,
        )

    # -----------------------------------------------------------------------
    # Aggregation (promoted from aggregate_options_bars(asset, ...))
    # -----------------------------------------------------------------------
    def aggregate_options_bars(
        self,
        trade_date: date,
    ) -> Tuple[List[dict], List[dict], List[dict], Optional[time]]:
        """Aggregate buffered samples into per-contract 5-min bars.

        Bar time = ceiling_5min of the last sample, CLAMPED to CLOSE_TIME: a
        post-close sample (EOD capture) lands on the 15:00 bar. With one sample
        the OHLC comes from the sample's day-level fields (EOD semantics); with
        several, from the ``last`` samples (stock-stream parity). Per-bar
        volume/amount subtract the day-cumulative source values; codes not in
        the day-contract map are skipped (no numeric contract_code → FK target
        unknown).

        Also emits one OI-estimate row per bar (``live.options_intraday_oi``):
        oi_base (prev-trading-day daily OI) + cum_volume (day-cumulative
        traded volume — the tstyle source value, NOT the subtracted per-bar
        volume) with the GENERATED open_interest_est = base + volume.

        Returns (identity_rows, bar_rows, oi_rows, bar_time).
        """
        buffer = self.buffer
        if not buffer:
            return [], [], [], None

        last_dt = buffer[-1][0]
        bar_time = min(ceiling_5min(last_dt.time()), CLOSE_TIME)

        all_codes: set = set()
        for _, snap in buffer:
            all_codes.update(snap.keys())

        identity_rows: List[dict] = []
        bar_rows: List[dict] = []
        oi_rows: List[dict] = []
        n_unmapped = 0
        for code in sorted(all_codes):
            if code in self.finished_codes:
                continue

            lasts: List[float] = []
            day_open = day_high = day_low = None
            end_cumvol = end_cumamt = None
            underlying = ""
            for _, snap in buffer:
                entry = snap.get(code)
                if entry is None:
                    continue
                last = entry.get("last")
                if last is not None:
                    lasts.append(last)
                day_open = entry.get("open") if day_open is None else day_open
                # running day extremes: the LATEST sample carries the widest ones
                if entry.get("high") is not None:
                    day_high = entry.get("high")
                if entry.get("low") is not None:
                    day_low = entry.get("low")
                if entry.get("volume") is not None:
                    end_cumvol = entry["volume"]
                if entry.get("amount") is not None:
                    end_cumamt = entry["amount"]
                u = entry.get("underlying")
                if u:
                    underlying = u
            if not lasts:
                continue

            sec = self.contract_map.get(code)
            if sec is None:
                n_unmapped += 1
                continue
            security_id, name = sec

            if len(buffer) == 1:
                # single sample (EOD capture): the day fields ARE the bar
                o, h, low, c = day_open, day_high, day_low, lasts[-1]
            else:
                o, h, low, c = lasts[0], max(lasts), min(lasts), lasts[-1]
            if o is None or h is None or low is None or c is None:
                continue

            end_cumvol = end_cumvol or 0.0
            end_cumamt = end_cumamt or 0.0
            vol = end_cumvol - self.prev_bar_cumvol.get(code, 0.0)
            if vol < 0:
                vol = end_cumvol
            amt = end_cumamt - self.prev_bar_cumamt.get(code, 0.0)
            if amt < 0:
                amt = end_cumamt
            self.prev_bar_cumvol[code] = end_cumvol
            self.prev_bar_cumamt[code] = end_cumamt

            change = round(c - o, 4)
            change_pct = round((c - o) / o * 100, 4) if o else None

            identity_rows.append({
                "date": trade_date,
                "contract_code": security_id,
                "contract_name": name,
                "exchange": "SSE",
            })
            bar_rows.append({
                "date": trade_date,
                "contract_code": security_id,
                "exchange": "SSE",
                "time": bar_time,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": vol,
                "amount": amt,
                "change": change,
                "change_pct": change_pct,
            })
            oi_rows.append({
                "date": trade_date,
                "contract_code": security_id,
                # Same shared bar grid as bar_rows — `time` is part of the OI
                # table's PK, so a row without it fails the bulk-write key read.
                "time": bar_time,
                "underlying_code": underlying,
                "exchange": "SSE",
                "oi_base": self.oi_base.get(security_id, 0.0),
                "cum_volume": end_cumvol,
            })

            if bar_time >= CLOSE_TIME:
                self.finished_codes.add(code)

        if n_unmapped:
            logger.warning(
                "aggregate_options_bars: %d/%d contracts missing from the day "
                "contract map — skipped (no numeric contract_code)",
                n_unmapped, len(all_codes),
            )
        return identity_rows, bar_rows, oi_rows, bar_time

    # -----------------------------------------------------------------------
    # Finished-codes prepopulation (promoted from
    # prepopulate_options_finished_codes(conn, asset, ...))
    # -----------------------------------------------------------------------
    def prepopulate_options_finished_codes(self, conn, trade_date) -> None:
        """Mark contracts that already have a 15:00 bar today as finished.

        The DB stores numeric contract codes; ``finished_codes`` keys on the
        tstyle contractid, so the day-contract map is inverted for the lookup
        (numerics absent from the current map — e.g. expired listings — are
        irrelevant: they can no longer appear in a snapshot either).
        """
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT DISTINCT contract_code FROM {self.intraday_table} "
                    "WHERE date = %s AND time = %s AND exchange = 'SSE'",
                    (trade_date, CLOSE_TIME),
                )
                done_numeric = {row[0] for row in cur.fetchall()}
            if not done_numeric:
                return
            id_by_numeric = {sec[0]: cid for cid, sec in self.contract_map.items()}
            matched = {id_by_numeric[n] for n in done_numeric if n in id_by_numeric}
            self.finished_codes.update(matched)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to pre-populate options finished_codes: %s", e)

    # -----------------------------------------------------------------------
    # CSV backfill (options-specific: cumulative volume AND amount baselines)
    # — promoted from backfill_options_asset(conn, asset, contract_map)
    # -----------------------------------------------------------------------
    def backfill_options_asset(self, conn, contract_map: Dict[str, Tuple[str, str]]) -> int:
        """Re-load raw options CSV snapshots into bars (recovery path).

        Mirrors ``StreamCsvBackfill.backfill_all_csvs``: filename dates checked
        first, only incomplete dates read, per-window aggregation via a scratch
        OptionsStream carrying the cumulative baselines across windows.
        """
        if not contract_map:
            logger.info("backfill options: no contract map — skipping (nothing resolvable)")
            return 0
        out_dir = resolve_out_dir(str(Path(__file__).resolve()), self.csv_subdir, None)
        csv_files = sorted(out_dir.glob(f"{self.csv_prefix}_*.csv"))
        if not csv_files:
            return 0

        total_bars = 0
        for csv_path in csv_files:
            trade_date = _extract_date_from_filename(csv_path)
            if trade_date is None:
                continue
            last_window = _file_last_window(csv_path)
            try:
                if _options_day_complete(conn, self, trade_date, last_window):
                    continue
            except Exception as e:  # noqa: BLE001
                logger.warning("backfill options: completeness check failed for %s: %s", csv_path.name, e)

            t0 = _time.time()
            windows = _group_options_csv_by_windows(csv_path)
            if not windows:
                continue

            scratch = OptionsStream(contract_map=contract_map)
            # OI base is date-specific (prev daily snapshot before the CSV's
            # date) — reload per file, shared by all windows of that day.
            scratch.oi_base = fetch_oi_base(conn, trade_date)
            all_identity: List[dict] = []
            all_bars: List[dict] = []
            all_oi: List[dict] = []
            for wend in sorted(windows.keys()):
                samples = windows[wend]
                scratch.buffer = samples
                scratch.finished_codes = set()
                day = samples[0][0].date()
                identity_rows, bar_rows, oi_rows, _ = scratch.aggregate_options_bars(day)
                all_identity.extend(identity_rows)
                all_bars.extend(bar_rows)
                all_oi.extend(oi_rows)
            # scratch baselines persist across windows by construction (same object)

            if all_bars:
                load_options_bars(conn, self, all_identity, all_bars, all_oi)
                total_bars += len(all_bars)
            logger.info(
                "backfill options: %s → %d windows, %d bars in %.1fs",
                csv_path.name, len(windows), len(all_bars), _time.time() - t0,
            )
        return total_bars
