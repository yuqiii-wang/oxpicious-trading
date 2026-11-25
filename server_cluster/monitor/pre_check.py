"""server_cluster.monitor.pre_check — the arrangement's DATA pre-check.

The nightly chain's run / don't-run gate BY DATA, for every service
that declares one. The freq-calendar rule (arrangement.py) asks "did
it run since the window re-opened?"; the pre-check asks the question
that decides whether a step has work at all — "is its data there?":

 1. the DB table: which of the latest CLOSED sessions (trading days
    ending at the 15:00-closed one) are absent from the service's data
    table;
 2. the source csv: for a service whose input is a downloaded file, is
    each absent session's file staged on disk yet?

and the verdict follows the service's ROLE (the ``kind``):

 - ``fetch`` (a download): a session is absent AND its csv NOT staged
   (``csv_missing``) → RUN; absent but staged (``csv_present``) → skip
   — the fetch is done, the build loads it. A fetch with no csv stage
   reads ``db_missing`` — absent is all it can be;
 - ``load`` (a build reading that csv): absent AND staged
   (``csv_present``) → RUN; absent but NOT staged (``csv_missing``) →
   skip until its download runs;
 - ``derive`` (analysis / strategy / a DB-only build): absent
   (``db_missing``) → RUN off the tables it reads.

 Every expected session present (``data_present``) → skip. A probe that
 errors is ``check_failed`` and FAILS OPEN (the step runs, its own
 incremental guards decide) — a broken pre-check must never hold data
 back.

 THE PRESENCE TABLE IS THE DATA TABLE, NOT THE IDENTITY TABLE. The
 stream pollers upsert today's rows into stats.stock_identity /
 etf_identity / index_identity / options_identity BEFORE the close (the
 FK parents of their intraday bars), so "the identity table has the
 session" is true hours before the EOD build loads anything — keying on
 it would skip the build (the 2026-10-12 gap: 1,063 SSE ETFs stuck at
 the prior date behind streamer-pre-seeded identity rows; builds/etf/
 pipeline/scope.py). Each config therefore names the table the
 service's own pipeline gates on — stats.etf_basic_stats for the ETF
 build, stats.options_terms for options — the same completeness signal
 the pipeline itself trusts.

 A session is PRESENT only when EVERY declared table slice holds it
 (``tables``; one exchange's rows of a shared table is one slice — a
 session another exchange populated does not count), and a csv is
 STAGED only when it is a real, FINAL file: at least ``min_bytes`` (the
 downloaders' MIN_VALID_BYTES — a 0-byte SZSE "no data" marker or a
 header-only SSE margin file is not data) and, unless the stage says
 otherwise, last written at/after the session's close (the SSE trend
 downloader's own ``_fetched_after_close`` rule: a file named for today
 but written before 15:00 is a pre-close snapshot, never EOD data).

 A download with no isolable DB slice (the exchange-native index trend
 files — other sources load the same index codes) declares its csv
 stage alone: the staged, final file IS its presence.

 A per-code SWEEP needs more than any-row presence: a sweep that died
 after its first codes leaves rows for the session while most of its
 universe is still missing, and the calendar rule is no help either (a
 pre-dawn run that served the PREVIOUS session counts as a same-day
 success and holds the step for the night — the 2026-11-24 CSI price
 sweep). Such a slice sets ``min_codes``: the session is present only
 when at least that many of its codes hold it.

 The chain arranges each tier right before running it
 (POST /orchestration/run ``arrange: true``), so a tier's reading sees
 the tiers before it: the builds tier is arranged after the downloads
 tier exited, its ``csv_present`` is that tier's fresh output.

 DECLARED, not coded: one git-reviewed JSON per service under
 ``database/sql/monitoring/static/orchestration_pre_checks/<dotted.
 service>.json`` (the non_daily_checks shape — read-only, no UI write
 path). A service without a file keeps the freq-calendar rule; a
 malformed file is logged and skipped, which falls back to the same
 rule. The session-presence reads render the sql_views templates
 ``server_cluster/monitor/arrangement/sessions_present*`` over ONE
 asyncpg connection per pass (one query per distinct table slice —
 never a per-service loop over the same table).

 Presence is not completeness: a session with a partial universe reads
 present. Partial sessions are the morning audit's domain (the 07:00
 daily_data_checks and the 07:15 missing-session backfill).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path
from string import Formatter
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from _common._holidays_and_weekdays import (
    business_days,
    expected_publish_session,
    recent_trading_day_cutoff,
)
from _common.caution_log import record_service_error
from _common.db_commons import get_db_connection_async
from _common.sql_views import frag, render_sql_view

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")

# The pre-check config static tree (git-reviewed, read-only — no UI
# write path, like non_daily_checks). One file per service, named by
# its dotted module path.
CHECKS_DIR = (_REPO_ROOT / "database" / "sql" / "monitoring" / "static"
              / "orchestration_pre_checks")

# How many closed sessions a pre-check expects, ending at the latest
# closed one (1 = the latest session only). A window wider than one
# session makes the pre-check a gap detector — a session an earlier
# chain never loaded is still absent days later — at no cost when the
# table is complete; a table that legitimately lacks a session just
# reads absent (the service runs and its own guards no-op, as before
# the arrangement existed). Per-config ``lookback`` overrides.
DEFAULT_LOOKBACK_SESSIONS = 5
MAX_LOOKBACK_SESSIONS = 60

# The A-share close (15:00 China): at/after it on a trading day the
# session counts as CLOSED and its EOD data is expected; before it only
# the previous session is (expected_publish_session's own rule).
_CLOSE_HOUR = 15

# The downloaders' validity floor (downloads/_common/filescan.py
# MIN_VALID_BYTES): a smaller file is an empty marker, not data.
DEFAULT_MIN_BYTES = 1024

# The session-presence sql_views templates, by a slice's filter shape.
# Each has a single-scan twin (``_scan``): one = ANY() pass instead of
# one probe per expected session — a slice's ``probe`` picks (see
# TableSlice.probe). The coverage shape is one GROUP BY pass by nature
# and has no twin.
_PRESENT_VIEWS: dict[str, str] = {
    "plain": "server_cluster/monitor/arrangement/sessions_present",
    "code": ("server_cluster/monitor/arrangement/"
             "sessions_present_code_slice"),
    "exchange": ("server_cluster/monitor/arrangement/"
                 "sessions_present_exchange_slice"),
    "coverage": "server_cluster/monitor/arrangement/sessions_present_coverage",
}
_SCAN_SUFFIX = "_scan"

# A probe slower than this makes the pass's timing line (the log's
# "which slice is slow" pointer).
_SLOW_PROBE_S = 0.2

# Which readings put a kind in the queue (the rest are not_due). The
# label keys of the orchestration_plan_basis domain. EVERY reading a
# role's _read path can emit is listed here with its verdict — a reading
# left out arranges the service NOT DUE on missing data (2026-11-24: the
# CSV-less downloads were moved to db_missing while fetch still queued
# only csv_missing, and eight downloads — the CSI price sweep among them
# — read "within freq window" on a night their session was absent):
#   fetch  — db_missing (no csv stage, session absent) | csv_missing
#            (a session to fetch) → due; csv_present → not due
#   load   — csv_present (a session to load) → due; csv_missing → not due
#   derive — db_missing → due
#   all    — data_present → not due; check_failed → due (fail-open)
_DUE_BASIS: dict[str, frozenset[str]] = {
    "fetch": frozenset({"db_missing", "csv_missing", "check_failed"}),
    "load": frozenset({"csv_present", "check_failed"}),
    "derive": frozenset({"db_missing", "check_failed"}),
}

# Config values that ride INTO SQL as identifiers, or into a filesystem
# glob: config is git-reviewed, but validate the charset defensively.
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PreCheckKind = Literal["fetch", "load", "derive"]


class CsvStage(BaseModel):
    """Where a service's downloaded source files sit — the stage between
    a download and its build. ``pattern`` is a glob relative to ``dir``
    with ONE ``{date}`` slot, filled per session with
    ``session.strftime(date_format)``."""

    dir: str = Field(description="Repo-relative directory (POSIX "
                                 "separators) under which the files "
                                 "sit, e.g. temps/szse_trend.")
    pattern: str = Field(description="File-name glob with a {date} slot "
                                     "(may span subdirectories, * and "
                                     "** included), e.g. "
                                     "{date}.csv or */{date}_futures.csv.")
    date_format: str = Field(default="%Y%m%d", description="strftime "
                          "format of the {date} token.")
    min_bytes: int = Field(default=DEFAULT_MIN_BYTES, ge=1,
                           description="A smaller file is an empty "
                                       "marker, not staged data.")
    after_close: bool = Field(default=True, description="Count only a "
                              "file last written at/after the session's "
                              "15:00 close — one named for the session "
                              "but written earlier is a pre-close "
                              "snapshot. False for artifacts a run "
                              "produces during the day (an AI daily "
                              "answer).")

    @field_validator("dir", "pattern")
    @classmethod
    def _safe_relative(cls, value: str) -> str:
        """A repo-relative POSIX path: no anchor, no ``..`` segment, no
        backslash."""
        anchored = value.startswith(("/", "\\")) or re.match(
            r"^[A-Za-z]:", value) is not None
        if anchored or "\\" in value or ".." in value.split("/"):
            raise ValueError(f"not a repo-relative POSIX path: {value!r}")
        return value

    @field_validator("pattern")
    @classmethod
    def _one_date_slot(cls, value: str) -> str:
        """Exactly the ``{date}`` slot — any other field would raise at
        format time."""
        fields = [name for _, name, _, _ in Formatter().parse(value)
                  if name is not None]
        if not fields or set(fields) != {"date"}:
            raise ValueError(f"the pattern needs the {{date}} slot only: "
                             f"{value!r}")
        return value

    def holds(self, session: date) -> bool:
        """Whether the session's file is staged — a real, final file (any
        match of the glob clears the stage)."""
        directory = _REPO_ROOT.joinpath(*self.dir.split("/"))
        pattern = self.pattern.format(
            date=session.strftime(self.date_format))
        for path in directory.glob(pattern):
            try:
                if not path.is_file() or path.stat().st_size < self.min_bytes:
                    continue
                if self.after_close and path.stat().st_mtime < datetime(
                        session.year, session.month, session.day,
                        _CLOSE_HOUR, tzinfo=CHINA_TZ).timestamp():
                    continue
            except OSError:
                continue
            return True
        return False


class TableSlice(BaseModel):
    """One table slice whose session rows say the service's data is
    there — the whole table, or the service's own universe inside a
    table other services share (an exchange, a code family)."""

    table: str = Field(description="The schema-qualified data table — "
                                   "the one the service's own pipeline "
                                   "gates on (NOT an identity table the "
                                   "streams pre-write: see the module "
                                   "docstring).")
    date_column: str = Field(default="date", description="The table's "
                              "session-date column.")
    exchange: str | None = Field(default=None, description="Restrict to "
                                 "one exchange's rows (e.g. SSE); None = "
                                 "every exchange.")
    exchange_column: str = Field(default="exchange", description="The "
                              "table's exchange column.")
    code_scope: str | None = Field(default=None, description="POSIX "
                                   "regex the slice's codes match (e.g. "
                                   "\\.SZ$); None = every code.")
    code_column: str = Field(default="code", description="The table's "
                              "code column.")
    probe: Literal["single_scan", "per_date"] = Field(
        default="single_scan", description="How the sessions are read — "
        "time only, never the verdict. single_scan: one = ANY() pass, "
        "right for a partitioned or date-unindexed table (a probe per "
        "session there costs a scan per session per partition). "
        "per_date: one index probe per expected session, far faster on "
        "a big table whose partitions index the date but cannot be "
        "pruned by it (strategy.strategy_daily: 0.8s vs 12.5s).")
    min_codes: int | None = Field(default=None, ge=1,
                                  description="A per-code sweep's "
                                  "completeness floor: the session is "
                                  "present only when at least this many "
                                  "distinct codes of the slice hold it "
                                  "(needs code_scope). None = any row.")

    @field_validator("table")
    @classmethod
    def _table_name(cls, value: str) -> str:
        if not _TABLE_NAME_RE.fullmatch(value):
            raise ValueError(f"not a schema-qualified table name: "
                             f"{value!r}")
        return value

    @field_validator("date_column", "exchange_column", "code_column")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"not an identifier: {value!r}")
        return value

    @model_validator(mode="after")
    def _floor_over_a_code_slice(self) -> "TableSlice":
        if self.min_codes is not None and (
                self.code_scope is None or self.exchange is not None):
            raise ValueError("min_codes counts a code slice — set "
                             "code_scope, and no exchange (an exchange "
                             "filter is not counted)")
        return self

    @property
    def shape(self) -> str:
        """Which filter shape of the session-presence template the slice
        renders."""
        if self.min_codes is not None:
            return "coverage"
        if self.exchange is not None:
            return "exchange"
        return "code" if self.code_scope is not None else "plain"

    @property
    def template(self) -> str:
        """The session-presence sql_views template the slice renders."""
        name = _PRESENT_VIEWS[self.shape]
        if self.shape == "coverage":
            return name  # one GROUP BY pass — no per-date twin
        return name + _SCAN_SUFFIX if self.probe == "single_scan" else name

    def __str__(self) -> str:
        scope = self.exchange if self.exchange is not None else self.code_scope
        if self.min_codes is not None:
            return f"{self.table}[{scope} >={self.min_codes}]"
        return f"{self.table}[{scope}]" if scope else self.table


class PreCheckConfig(BaseModel):
    """One service's pre-check config file from the static tree."""

    service: str = Field(description="The tier step's dotted python -m "
                                     "module path — the config's key.")
    kind: PreCheckKind = Field(description="The service's role: fetch (a "
                           "download), load (a build reading the "
                           "downloaded csv), derive (analysis / strategy "
                           "/ a DB-only build).")
    tables: list[TableSlice] = Field(default_factory=list,
                                     description="A session is present "
                                     "only when EVERY slice holds it; "
                                     "empty only for a fetch that stages "
                                     "its csv alone.")
    csv: list[CsvStage] = Field(default_factory=list,
                                description="The downloaded source stage "
                                "— required for load, the csv half of "
                                "fetch, absent for derive. A session is "
                                "staged when ANY stage holds its file.")
    lookback: int = Field(default=DEFAULT_LOOKBACK_SESSIONS, ge=1,
                          le=MAX_LOOKBACK_SESSIONS,
                          description="Closed sessions expected, ending "
                          "at the latest closed one.")
    notes: str | None = None

    @model_validator(mode="after")
    def _role_parts(self) -> "PreCheckConfig":
        if self.kind == "load" and not (self.tables and self.csv):
            raise ValueError('kind "load" needs its table slice(s) AND a '
                             "csv stage — a build with no downloaded "
                             "input is kind derive")
        if self.kind == "derive" and (not self.tables or self.csv):
            raise ValueError('kind "derive" needs its table slice(s) and '
                             "no csv stage — one reading a downloaded "
                             'file is kind load')
        if self.kind == "fetch" and not (self.tables or self.csv):
            raise ValueError('kind "fetch" needs its table slice(s) or '
                             "its csv stage")
        return self

    @classmethod
    def load_all(cls) -> dict[str, "PreCheckConfig"]:
        """Every config in the static tree, keyed by service. A
        malformed file is logged and skipped — it must never take the
        arrangement down (the service falls back to the freq-calendar
        rule)."""
        configs: dict[str, PreCheckConfig] = {}
        for path in sorted(CHECKS_DIR.glob("*.json")):
            try:
                cfg = cls.model_validate(json.loads(
                    path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                logger.warning("pre-check config unreadable (%s): %s",
                               path.name, exc)
                continue
            configs[cfg.service] = cfg
        return configs


class PreCheckReading(BaseModel):
    """One service's pre-check outcome, as upserted into the
    arrangement row: the evidence (basis), the verdict it yields
    (state) and the session lists behind them."""

    basis: str = Field(description="The reading — label key of the "
                      "orchestration_plan_basis domain: data_present | "
                      "db_missing | csv_present | csv_missing | "
                      "check_failed.")
    state: str = Field(description="The verdict the reading yields — "
                      "label key of orchestration_plan_state: due | "
                      "not_due.")
    missing_count: int = Field(default=0, description="The sessions the "
                               "verdict turns on: absent from the table "
                               "(derive), to fetch (fetch), to load "
                               "(load).")
    missing_detail: dict = Field(default_factory=dict)

    @classmethod
    def of(cls, role: str, basis: str, count: int = 0,
           **detail: object) -> "PreCheckReading":
        return cls(basis=basis, state="due" if basis in _DUE_BASIS[role]
                   else "not_due", missing_count=count,
                   missing_detail=dict(detail))


# One distinct session-presence query: a table slice's filter (its
# coverage floor included — two floors over one slice read differently),
# the window width, and the template it renders. ``_LOOKBACK`` indexes
# the window width (it keys the expected-session list).
_LOOKBACK = 7
ProbeKey = tuple[str, str, str | None, str, str | None, str, int | None,
                 int, str]


class DataPreChecker:
    """One arrangement pass's data probe: the expected sessions, the
    session-presence queries, the csv-stage checks, and the per-service
    readings (one instance per pass — the arranger builds it over the
    tier universe it is about to arrange)."""

    def __init__(self, configs: dict[str, PreCheckConfig]) -> None:
        self._configs = configs

    @classmethod
    def for_services(cls, services) -> "DataPreChecker":
        """The checker over the declared configs of ``services`` (the
        tier universe minus its residents)."""
        declared = PreCheckConfig.load_all()
        return cls({service: declared[service] for service in services
                    if service in declared})

    def readings(self, now_cn: datetime) -> dict[str, PreCheckReading]:
        """Every configured service's reading at ``now_cn`` (China
        time). Sync face — the arranger's thread; asyncpg under a
        short-lived ``asyncio.run`` loop, like the arranger's own
        reads."""
        if not self._configs:
            return {}
        try:
            windows = self._windows(now_cn)
            return asyncio.run(self._readings_async(windows))
        except Exception as exc:  # noqa: BLE001 — the calendar itself
            logger.exception(  # failed: every service fails open
                "arrangement pre-check: no reading could be taken")
            error = f"{type(exc).__name__}: {exc}"
            # ONE caution for the pass (a whole-pass failure is one
            # fault — a per-service row each would flood the tab).
            record_service_error(
                "arrangement pre-check: no reading could be taken — "
                f"{len(self._configs)} service(s) arranged due fail-open "
                f"({error})",
                dedupe_key="pre-check:pass",
                detail={"error": error,
                        "services": list(self._configs)},
                service="server_cluster.monitor", severity="warning")
            return {
                service: self._failed(cfg, error, caution=False)
                for service, cfg in self._configs.items()}

    # ------------------------------------------------------------------
    # the expected sessions
    # ------------------------------------------------------------------

    def _windows(self, now_cn: datetime) -> dict[int, list[date]]:
        """Lookback → the expected sessions (trading days, newest
        last), each ending at the latest CLOSED session."""
        # expected_publish_session compares the hour of a NAIVE clock:
        # hand it the China wall clock, never the host's.
        target = expected_publish_session(now_cn.replace(tzinfo=None),
                                          eod_hour=_CLOSE_HOUR)
        return {
            lookback: business_days(
                recent_trading_day_cutoff(lookback, target), target,
                reverse=False)
            for lookback in {cfg.lookback for cfg in self._configs.values()}}

    # ------------------------------------------------------------------
    # the probes
    # ------------------------------------------------------------------

    async def _readings_async(self, windows: dict[int, list[date]]
                              ) -> dict[str, PreCheckReading]:
        """One query per distinct table slice, ONE connection; a probe
        that errors is the ``check_failed`` reading of every service it
        serves."""
        present: dict[ProbeKey, set[date] | str] = {}
        durations: dict[str, float] = {}
        started = time.monotonic()
        conn = await get_db_connection_async()
        try:
            for key in dict.fromkeys(
                    self._probe_key(cfg, table)
                    for cfg in self._configs.values()
                    for table in cfg.tables):
                try:
                    probed_at = time.monotonic()
                    present[key] = await self._present_sessions(
                        conn, key, windows[key[_LOOKBACK]])
                    durations[f"{key[0]}[{key[2] or key[4] or 'all'}]"] = (
                        time.monotonic() - probed_at)
                except Exception as exc:  # noqa: BLE001 — one probe's
                    logger.exception(  # failure is its services' own
                        "arrangement pre-check: probe of %s failed",
                        key[0])
                    present[key] = f"{type(exc).__name__}: {exc}"
        finally:
            await conn.close()
        logger.info("arrangement pre-check: %d probe(s) in %.1fs%s",
                    len(durations), time.monotonic() - started,
                    self._slowest(durations))
        readings: dict[str, PreCheckReading] = {}
        for service, cfg in self._configs.items():
            probes = [present[self._probe_key(cfg, table)]
                      for table in cfg.tables]
            failed = next((p for p in probes if isinstance(p, str)), None)
            if failed is not None:
                readings[service] = self._failed(cfg, failed)
                continue
            readings[service] = self._read(
                cfg, windows[cfg.lookback],
                [p for p in probes if not isinstance(p, str)])
        return readings

    @staticmethod
    def _probe_key(cfg: PreCheckConfig, table: TableSlice) -> ProbeKey:
        """The distinct-query key: two services reading the same table
        slice over the same window share ONE session-presence query
        (the same slice declared with both probe shapes runs twice —
        presence is identical, only the read differs)."""
        return (table.table, table.date_column, table.exchange,
                table.exchange_column, table.code_scope, table.code_column,
                table.min_codes, cfg.lookback, table.template)

    @staticmethod
    def _slowest(durations: dict[str, float]) -> str:
        """The pass's timing tail: the slices slower than the slow-probe
        mark, slowest first (empty when every probe was quick)."""
        slow = sorted(((-seconds, name) for name, seconds in durations.items()
                       if seconds >= _SLOW_PROBE_S))[:5]
        return ("; slowest: " + ", ".join(
            f"{name} {-neg:.1f}s" for neg, name in slow)) if slow else ""

    @staticmethod
    async def _present_sessions(conn, key: ProbeKey,
                                sessions: list[date]) -> set[date]:
        """Which of ``sessions`` the table slice holds any row of."""
        (table, date_column, exchange, exchange_column, code_scope,
         code_column, min_codes, _, template) = key
        names = {"table_name": frag(table),
                 "date_column": frag(date_column),
                 "sessions": sessions}
        filters: dict = {}
        if min_codes is not None:
            filters = {"code_scope": code_scope,
                       "code_column": frag(code_column),
                       "min_codes": min_codes}
        elif exchange is not None:
            filters = {"exchange": exchange,
                       "exchange_column": frag(exchange_column),
                       "code_scope": code_scope,
                       "code_column": frag(code_column)}
        elif code_scope is not None:
            filters = {"code_scope": code_scope,
                       "code_column": frag(code_column)}
        view = render_sql_view(template, **names, **filters)
        rows = await conn.fetch(view.sql, *view.params)
        return {row["session"] for row in rows}

    @staticmethod
    def _read(cfg: PreCheckConfig, sessions: list[date],
              probes: list[set[date]]) -> PreCheckReading:
        """One service's reading off its table slices' present sessions
        and its csv stage."""
        # With no table slice (a fetch staging its csv alone) nothing is
        # proven present — every expected session is absent, and the csv
        # stage alone decides.
        present = set(sessions) if cfg.tables else set()
        for held in probes:
            present &= held
        missing = [s for s in sessions if s not in present]
        detail: dict = {"kind": cfg.kind, "tables": [str(t) for t in cfg.tables],
                        "from": sessions[0].isoformat(),
                        "to": sessions[-1].isoformat(),
                        "missing": [d.isoformat() for d in missing]}
        if cfg.tables and not missing:
            return PreCheckReading.of(cfg.kind, "data_present", **detail)
        staged = {d for d in missing
                  if any(stage.holds(d) for stage in cfg.csv)}
        csv_present = [d for d in missing if d in staged]
        csv_missing = [d for d in missing if d not in staged]
        detail.update(csv_present=[d.isoformat() for d in csv_present],
                      csv_missing=[d.isoformat() for d in csv_missing])
        if len(cfg.tables) > 1:
            # Which slice(s) lack the session — a multi-slice service's
            # queue line names the exchange that is behind.
            detail["slice_missing"] = {
                str(table): [d.isoformat() for d in missing
                             if d not in held]
                for table, held in zip(cfg.tables, probes)
                if any(d not in held for d in missing)}
        # fetch runs while anything is left to download, load while
        # anything is there to load — the reading names the side that
        # decides.
        if cfg.kind == "fetch":
            if not cfg.csv:
                # No csv stage: an absent session is simply missing from
                # the table — there is no "staged" to speak of.
                return PreCheckReading.of(cfg.kind, "db_missing",
                                          len(missing), **detail)
            return PreCheckReading.of(
                cfg.kind, "csv_missing" if csv_missing else "csv_present",
                len(csv_missing), **detail)
        if cfg.kind == "load":
            return PreCheckReading.of(
                cfg.kind, "csv_present" if csv_present else "csv_missing",
                len(csv_present), **detail)
        return PreCheckReading.of(cfg.kind, "db_missing", len(missing),
                                  **detail)

    @staticmethod
    def _failed(cfg: PreCheckConfig, error: str,
                caution: bool = True) -> PreCheckReading:
        """The fail-open reading of a probe that errored — and its
        caution row (the Cautions tab is where a broken pre-check config
        gets seen; deduped per service — ``caution`` off when the whole
        pass failed and already recorded its own row)."""
        if caution:
            record_service_error(
                f"arrangement pre-check failed for {cfg.service} — arranged "
                f"due fail-open ({error})",
                dedupe_key=f"pre-check:{cfg.service}",
                detail={"service": cfg.service, "kind": cfg.kind,
                        "tables": [str(t) for t in cfg.tables],
                        "error": error},
                service="server_cluster.monitor", severity="warning")
        return PreCheckReading.of(cfg.kind, "check_failed", error=error)
