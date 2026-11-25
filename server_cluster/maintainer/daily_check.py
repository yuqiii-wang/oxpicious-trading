"""server_cluster.maintainer.daily_check — the daily 07:00 morning
data audit.

The 16:00 nightly chain (monitoring → downloads → builds → analysis →
strategy) is expected to have loaded the latest trading day into every
daily data item by the next morning. This job is the morning AFTER's
audit: it (1) checks the UI API (the data_viz Express health endpoint —
the surface every browser session rides) and (2) checks EVERY daily
data item across the three consumer domains — ``data_viz`` (the UI's
data sources), ``analysis`` (the analysis tier's output tables) and
``strategy`` (the strategy tier's outputs) — loading each item's data
and comparing its latest loaded date against its own data frontier
(the source's newest date the item is built from). An item whose
latest data is behind its frontier — the nightly fire stopped writing,
a partial load, a dead family — is reported to ``monitor.caution_log``
(the Monitor → Cautions tab), ONE deduped open row per item that
recurrence bumps; a recovered item's row is resolved from the tab.

The ITEMS are declared, not coded: git-reviewed configs in
``database/sql/monitoring/static/daily_data_checks/<domain>/<item>.json``
(the non_daily_checks shape — one JSON per item carrying its check
view + notes), each rendered through ``_common.sql_views`` and executed
over ONE asyncpg connection. The check view is the whole verdict: it
returns ZERO rows when the item's latest data is loaded, one row per
stale sub-item otherwise (the ``oracle_signals_coverage`` contract —
per-code coverage / per-sec_type lag / a single table-wide row); the
Python stays dumb (render → fetch → rows>0 ⇒ stale) so a view can
encode any granularity without a job change. A view that ERRORS is
that item's ``check_failed`` caution — never the pass's failure.

The job follows the maintainer's own-fire-group shape (``fire_at``
07:00 China time, joining the fleet bring-up's 07:00 pass;
``MAINTAINER_DAILY_CHECK_AT`` env, ``HH:MM`` China time, overrides for
testing) and appends its ``monitor.service_audit`` pass row. Item
findings do NOT fail the pass — the cautions are the reporting channel
— only a pass-level crash (DB unreachable, the guarded ``run()``) is
``ok=False``.

The UI API check is HTTP, not SQL: ``GET {ui_api}/api/health`` expecting
``{"success": true}`` (the Express app's own liveness face). The base
url defaults to ``http://127.0.0.1:3001`` (the data_viz API's dev port)
and is overridable via ``MAINTAINER_UI_API_URL`` for a non-default
deployment.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from _common.caution_log import record_service_error
from _common.db_commons import get_db_connection_async
from _common.internal_request import InternalRequestError, get_json
from _common.sql_views import render_sql_view
from server_cluster.maintainer.jobs import (MaintenanceJob, MissedFireGuard,
                                            PassAudit)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")

# The fire time (China local) and the audit trail constants.
DEFAULT_FIRE_AT = "07:00"
FIRE_AT_ENV = "MAINTAINER_DAILY_CHECK_AT"
AUDIT_SERVICE = "server_cluster.maintainer.daily_check"
AUDIT_EXIT_REASON = "daily 07:00 data check"

# The check-config static tree (git-reviewed, read-only — no UI write
# path, like non_daily_checks). The domain is the config's first path
# segment under the tree (data_viz / analysis / strategy); the item is
# the dotted path below it.
CHECKS_DIR = (_REPO_ROOT / "database" / "sql" / "monitoring" / "static"
              / "daily_data_checks")

# The UI API (data_viz Express) liveness probe — base url overridable
# for a non-default deployment; /api/health answers {"success": true}.
DEFAULT_UI_API_URL = "http://127.0.0.1:3001"
UI_API_URL_ENV = "MAINTAINER_UI_API_URL"
_UI_API_HEALTH_PATH = "/api/health"
_REQUEST_TIMEOUT_S = 15.0

# The service the cautions name (this process writes them — the
# log_context points at the maintainer's own log where the pass runs).
_CAUTION_SERVICE = "server_cluster.maintainer"

# Per-item caution detail sample cap (enough to eyeball, never the list
# — a per-code check over the whole stock universe can return thousands
# of stale rows; the caution carries the first few).
_DETAIL_SAMPLE = 10


class DailyCheckConfig(BaseModel):
    """One item's check config file from the static tree."""

    item: str = Field(description="The dotted item identity — also the "
                      "caution dedupe key suffix "
                      "(daily_data_check:<item>).")
    view: str = Field(description="The sql_views path of the item's "
                      "check view (zero rows = loaded).")
    params: dict = Field(default_factory=dict,
                         description="Slot args the view declares "
                         "(most views are argless).")
    notes: str | None = Field(default=None,
                              description="What the item is and what "
                              "staleness means (rides the caution "
                              "detail).")

    @classmethod
    def load_all(cls) -> list["DailyCheckConfig"]:
        """Every config in the static tree, in path order. A malformed
        file is logged and skipped — it must never take the pass down.
        The item identity comes from the file's path:
        ``<domain>/<item...>.json`` → item ``<domain>.<item...>``."""
        configs: list[DailyCheckConfig] = []
        for path in sorted(CHECKS_DIR.rglob("*.json")):
            rel = path.relative_to(CHECKS_DIR)
            dotted = ".".join(rel.with_suffix("").parts)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                configs.append(cls.model_validate(
                    {"item": dotted, **raw}))
            except (OSError, ValueError) as exc:
                logger.warning("daily check config unreadable (%s): %s",
                               path.name, exc)
        return configs


class UiApiOutcome(BaseModel):
    """The UI API liveness probe's outcome."""

    ok: bool
    detail: str = Field(default="", description="On failure: the url "
                        "and what answered (status / transport error).")


class ItemOutcome(BaseModel):
    """One item's verdict — the JobResult detail's atoms and the
    caution writers' input."""

    item: str
    domain: str
    state: str = Field(description="loaded | stale | check_failed")
    stale_count: int = 0
    sample: list[dict] = Field(default_factory=list,
                               description="The first stale rows "
                               "(serialized; the caution detail).")
    notes: str | None = Field(default=None,
                              description="The item config's notes "
                              "(rides the caution detail).")


class DailyDataCheckJob(MaintenanceJob):
    """The daily 07:00 morning data audit — UI API health + every
    configured item's latest-loaded-vs-frontier check, findings to
    ``monitor.caution_log``. Its own ``fire_at`` group puts it on the
    shared scheduler's 07:00 pass (next to the fleet bring-up)."""

    name = "daily_data_check"
    description = ("Daily 07:00 morning data audit: UI API health + "
                   "every daily_data_checks item (data_viz / analysis "
                   "/ strategy) loaded and compared against its data "
                   "frontier; stale items report to monitor.caution_log "
                   "(one deduped row per item).")

    def __init__(self) -> None:
        super().__init__()
        self.fire_at = os.environ.get(FIRE_AT_ENV, DEFAULT_FIRE_AT)

    # ------------------------------------------------------------------
    # the pass
    # ------------------------------------------------------------------

    def _run(self) -> tuple[list[str], str]:
        started = datetime.now(timezone.utc)
        # The missed-fire tripwire first (the previous pass predating the
        # previous 07:00 fire → caution; best-effort, never fatal — the
        # 2026-11-22 Sunday fire's silent skip is the case it catches).
        MissedFireGuard(AUDIT_SERVICE, self.fire_at, self.name).check()
        try:
            ui = self._check_ui_api()
            outcomes = self._check_items(DailyCheckConfig.load_all())
        except Exception:
            PassAudit().record(AUDIT_SERVICE, started, False,
                               AUDIT_EXIT_REASON)
            raise
        PassAudit().record(AUDIT_SERVICE, started, True, AUDIT_EXIT_REASON)
        self._report_ui(ui)
        for outcome in outcomes:
            self._report(outcome)
        detail = self._detail_line(ui, outcomes)
        logger.info("daily data check: %s", detail)
        return [], detail

    # ------------------------------------------------------------------
    # UI API liveness
    # ------------------------------------------------------------------

    def _check_ui_api(self) -> UiApiOutcome:
        """GET the data_viz Express health endpoint. A failure is a
        caution (see _report_ui) — the pass still audits the DB items,
        which don't need the API up."""
        base = os.environ.get(UI_API_URL_ENV, DEFAULT_UI_API_URL)
        url = f"{base}{_UI_API_HEALTH_PATH}"
        try:
            body = get_json(url, timeout_s=_REQUEST_TIMEOUT_S)
        except InternalRequestError as exc:
            return UiApiOutcome(ok=False, detail=f"{url} — {exc}")
        if not isinstance(body, dict) or body.get("success") is not True:
            return UiApiOutcome(
                ok=False, detail=f"{url} — unexpected body "
                f"{json.dumps(body, default=str)[:200]}")
        return UiApiOutcome(ok=True, detail=url)

    def _report_ui(self, ui: UiApiOutcome) -> None:
        """The UI API's own caution row (dedupe key ``ui_api``) when
        the liveness probe failed; a healthy API writes nothing."""
        if ui.ok:
            return
        record_service_error(
            "daily data check: ui api unreachable",
            dedupe_key="daily_data_check:ui_api",
            detail={"ui_api": ui.detail},
            service=_CAUTION_SERVICE, severity="error")

    # ------------------------------------------------------------------
    # item checks (ONE asyncpg connection, per-item fetch)
    # ------------------------------------------------------------------

    def _check_items(self, configs: list[DailyCheckConfig]) -> list:
        return asyncio.run(self._check_items_async(configs))

    async def _check_items_async(
            self, configs: list[DailyCheckConfig]) -> list[ItemOutcome]:
        """Run each config's check view and collect its verdict (one
        shared connection; a task's failure is its own check_failed
        verdict, never the pass's)."""
        outcomes: list[ItemOutcome] = []
        conn = await get_db_connection_async()
        try:
            for cfg in configs:
                outcomes.append(await self._check_one(conn, cfg))
        finally:
            await conn.close()
        return outcomes

    @staticmethod
    async def _check_one(conn, cfg: DailyCheckConfig) -> ItemOutcome:
        """One item's verdict from its view (zero rows = loaded)."""
        domain = cfg.item.split(".", 1)[0]
        try:
            view = render_sql_view(cfg.view, **cfg.params)
            rows = await conn.fetch(view.sql, *view.params)
        except Exception as exc:  # noqa: BLE001 — one item's check
            logger.exception(  # failure is its own verdict
                "daily data check failed for %s (view %s)",
                cfg.item, cfg.view)
            return ItemOutcome(
                item=cfg.item, domain=domain, state="check_failed",
                sample=[{"error": f"{type(exc).__name__}: {exc}"}],
                notes=cfg.notes)
        if not rows:
            return ItemOutcome(item=cfg.item, domain=domain,
                               state="loaded", notes=cfg.notes)
        sample = [_row_dict(r) for r in rows[:_DETAIL_SAMPLE]]
        return ItemOutcome(item=cfg.item, domain=domain, state="stale",
                           stale_count=len(rows), sample=sample,
                           notes=cfg.notes)

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------

    def _report(self, outcome: ItemOutcome) -> None:
        """One caution row per stale / failed item (the fire-and-forget
        ``_common.caution_log`` writer; dedupe key per item so daily
        recurrence bumps ONE open row). A loaded item writes nothing —
        its previous open row stays for the tab's resolve action."""
        if outcome.state == "loaded":
            return
        title = (f"daily data not loaded: {outcome.item}"
                 if outcome.state == "stale"
                 else f"daily data check failed: {outcome.item}")
        record_service_error(
            title, dedupe_key=f"daily_data_check:{outcome.item}",
            detail={
                "item": outcome.item, "domain": outcome.domain,
                "state": outcome.state,
                "stale_count": outcome.stale_count,
                "rows": outcome.sample,
                "notes": outcome.notes,
            },
            service=_CAUTION_SERVICE, severity="error")

    @staticmethod
    def _detail_line(ui: UiApiOutcome,
                     outcomes: list[ItemOutcome]) -> str:
        """The JobResult detail: the UI API face + the item verdict
        counts with the not-loaded item names."""
        stale = [o.item for o in outcomes if o.state == "stale"]
        failed = [o.item for o in outcomes if o.state == "check_failed"]
        parts = [f"ui api {'ok' if ui.ok else f'UNREACHABLE ({ui.detail})'}",
                 f"checked {len(outcomes)} item(s)"]
        parts.append(f"{len(outcomes) - len(stale) - len(failed)} loaded")
        if stale:
            parts.append(f"{len(stale)} stale: {', '.join(stale)}")
        if failed:
            parts.append(f"{len(failed)} check-failed: "
                         f"{', '.join(failed)}")
        return "; ".join(parts)


def _row_dict(row) -> dict:
    """One asyncpg Record as a JSON-able dict (dates/timestamps ISO'd —
    the caution detail serializes through json.dumps(default=str), the
    isoform keeps the tab readable)."""
    out: dict = {}
    for key, value in dict(row).items():
        out[key] = (value.isoformat()
                    if isinstance(value, date) else value)  # date+datetime
    return out
