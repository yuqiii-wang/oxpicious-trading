"""server_cluster._common.routers.processes — the area's resident-process
control: list + start/stop/restart + the downward command mailbox. This
is the takeover surface: the supervisor's desired-state loop honors
these flips ACROSS restarts (a stopped resident stays stopped — the
property the central watchdog never had). The command route (2026-11-21
control unification plan Phase 4) is the ONE downward control beyond
kill-level stops: cooperative flush/pause/resume, enqueued into
``monitor.service_command`` and consumed by the child's own heartbeat
thread at its safe checkpoints."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..data_definitions import ResidentControlResponse, \
    ResidentStatus
from ..deps import get_config, get_processes, get_supervisor

logger = logging.getLogger(__name__)

# The enqueue write (database/sql_writes — the SQL-first rule; the
# action file owns the statement, this module keeps the args).
_ENQUEUE_COMMAND_WRITE = (
    "server_cluster/_common/routers/processes/service_command/insert")

router = APIRouter(tags=["processes"])


@router.get("/processes")
def processes(request: Request,
              detail: bool = Query(
                  default=False,
                  description="Fold each resident's unified detail "
                  "payload (heartbeat evidence + derived progress "
                  "+ reported progress + log tail) into its row — "
                  "the fleet fold's one-call-per-area shape (the "
                  "control unification plan Phase 3). The plain "
                  "rows stay the default: a liveness poll should "
                  "not pay for progress reads. NOTE: no "
                  "response_model here — the folded rows carry an "
                  "extra ``detail`` object a list[ResidentStatus] "
                  "model would silently strip.")) -> list:
    """The area's resident rows (liveness-probed; empty for areas with
    no residents — every server serves the same surface). With
    ``detail=1`` each row becomes its ResidentStatus fields PLUS a
    ``detail`` object (the folding consumers already park unknown keys
    in their ``detail`` bag)."""
    supervisor = get_supervisor(request)
    rows = supervisor.snapshot()
    if not detail:
        return rows
    from .process_detail import ProcessDetailBuilder

    builder = ProcessDetailBuilder(get_config(request),
                                   get_processes(request), supervisor)
    # ONE snapshot feeds both the rows and the builds (the fold's
    # liveness probes and progress reads happen once per resident). The
    # response rows STAY ResidentStatus-shaped with the unified payload
    # under ``detail`` — the row's own fields (service/desired/state/pid)
    # must survive the fold: the fleet fold and the UI merge key on
    # them (returning the payload dumps AS the rows would null the
    # resident fields out).
    details = {d["service"]: d
               for d in builder.fold(rows=rows, include_fingerprint=False)}
    folded: list[dict] = []
    for row in rows:
        record = row.model_dump()
        record["detail"] = details.get(row.service)
        folded.append(record)
    return folded


@router.post("/processes/{service}/start",
             response_model=ResidentControlResponse)
async def start_process(request: Request, service: str
                        ) -> ResidentControlResponse:
    return _control(request, service, "start")


@router.post("/processes/{service}/stop",
             response_model=ResidentControlResponse)
async def stop_process(request: Request, service: str
                       ) -> ResidentControlResponse:
    return _control(request, service, "stop")


@router.post("/processes/{service}/restart",
             response_model=ResidentControlResponse)
async def restart_process(request: Request, service: str
                          ) -> ResidentControlResponse:
    return _control(request, service, "restart")


# ---------------------------------------------------------------------------
# the downward command mailbox (Phase 4, 2026-11-21 control unification)
# ---------------------------------------------------------------------------

class CommandRequest(BaseModel):
    """POST /processes/{service}/command — one cooperative command for
    a running child (the child's heartbeat thread claims + consumes it
    atomically; SIGTERM stays the backstop)."""

    command: str = Field(description="flush_now | pause | resume (the "
                         "service_command label vocabulary).")
    payload: dict | None = Field(default=None,
                                 description="Optional arguments (e.g. "
                                 "{\"max_pause_s\": 600} on a pause — "
                                 "the child's own config bound still "
                                 "caps it).")


class CommandEnqueued(BaseModel):
    """The enqueue verdict (the mailbox row is the durable record)."""

    service: str
    command: str
    enqueued: bool


_COMMAND_TTL_S = 120  # the planned-stop marker's stale-marker precedent


@router.post("/processes/{service}/command",
             response_model=CommandEnqueued)
async def command_process(request: Request, service: str,
                          req: CommandRequest) -> CommandEnqueued:
    """Enqueue one cooperative command into the child's mailbox — the
    ONE downward control face beyond kill-level stops. Class-gated by
    the service_config tree (``_common.service_heartbeat``): a PAUSE is
    refused (409) for a service that is not pausable or is gpu-class
    (a paused GPU step pins the CUDA context until exit — kill-and-
    restart stays its only give-way); a SAMPLER is enqueued freely (the
    CHILD refuses mid-session at its checkpoint, with the data-loss
    reason it alone knows). The command expires unconsumed after the
    120s planned-stop-marker precedent."""
    from _common.labels import SERVICE_COMMAND_CODE

    command_code = SERVICE_COMMAND_CODE.get(req.command)
    if command_code is None:
        raise HTTPException(
            status_code=422,
            detail=f"command {req.command!r} is not one of "
                   f"{sorted(SERVICE_COMMAND_CODE)}")
    if req.command == "pause":
        from _common.service_heartbeat import pausable_for, \
            resource_class_for

        resource_class = resource_class_for(service)
        if not pausable_for(service) or resource_class == "gpu":
            raise HTTPException(
                status_code=409,
                detail=f"{service!r} does not accept PAUSE "
                       f"(pausable=false or resource_class="
                       f"{resource_class!r} — a paused gpu step pins "
                       "the CUDA context until exit; kill-and-restart "
                       "stays its only give-way)")
    _require_known_service(request, service)
    # Lazy imports: sql_writes is light but db_commons transitively
    # pulls pandas — this router module stays import-light (the cluster
    # servers' boot discipline).
    from _common.db_commons import get_db_connection_async
    from _common.sql_writes import render_sql_write

    write = render_sql_write(
        _ENQUEUE_COMMAND_WRITE, service, command_code,
        json.dumps(req.payload) if req.payload else None,
        _COMMAND_TTL_S)
    conn = await get_db_connection_async()
    try:
        await conn.execute(write.sql, *write.params)
    finally:
        await conn.close()
    logger.info("command %s enqueued for %s (ttl %ds)",
                req.command, service, _COMMAND_TTL_S)
    return CommandEnqueued(service=service, command=req.command,
                           enqueued=True)


def _require_known_service(request: Request, service: str) -> None:
    """The enqueue target must be plausibly OURS: a module under the
    area's prefixes or an area resident (a typo should not enqueue a
    command that would sit unclaimed in a dead mailbox)."""
    from ..catalog import owns

    config = get_config(request)
    if not owns(config.module_prefixes, service) and service not in {
            r.service for r in config.residents}:
        raise HTTPException(
            status_code=404,
            detail=f"service {service!r} is not a module or resident of "
                   f"the {config.area} area — enqueue at its owning "
                   "server")


def _control(request: Request, service: str,
             action: str) -> ResidentControlResponse:
    supervisor = get_supervisor(request)
    if action == "start":
        ok = supervisor.set_desired(service, "running")
    elif action == "stop":
        ok = supervisor.set_desired(service, "stopped")
    else:
        ok = supervisor.restart(service)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"service {service!r} is not a resident of this "
            "server's area")
    return ResidentControlResponse(service=service, action=action, ok=True)
