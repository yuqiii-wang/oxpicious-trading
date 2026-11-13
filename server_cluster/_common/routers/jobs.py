"""server_cluster._common.routers.jobs — the area's job surface: run,
poll status, fetch result, stop. THE API entry the Express
``py-runner.service.ts`` proxy replaces its WSL spawns with.

Contract notes:
* Tags are the process-id-tag identity the frontend reconstructs
  (``analysis-run:<module>:<sec_type>:<code>``, ``singleton-run:...``) —
  the registry treats them as opaque strings; the charset check only
  keeps them URL/registry safe.
* ``already_running`` mirrors the Express contract verbatim: the same
  tag in flight means NOTHING was spawned.
* A run's captured output is its service-log tail (one file, both
  streams); ``result.stdout``/``result.stderr`` carry the same tail so
  the Express ``RunScriptResult`` maps 1:1.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query, Request

from ..catalog import owns, validate_args, validate_module
from ..data_definitions import RunAccepted, RunRequest, RunStatusResponse, \
    StopResponse
from ..deps import get_config, get_registry

logger = logging.getLogger(__name__)

# Opaque-but-safe tag charset (the frontend's reconstructed tags are
# word chars plus : - _ . @ + = ; bounded length keeps paths sane).
TAG_RE = re.compile(r"^[\w:@.\-+=]{1,200}$")

router = APIRouter(tags=["jobs"])


def _derive_tag(module: str, args: list[str]) -> str:
    """Deterministic fallback tag when the caller omits one (module +
    argv joined — the same identity for the same logical run)."""
    return ":".join([module, *args])


@router.post("/jobs/run", response_model=RunAccepted)
async def run_job(request: Request, req: RunRequest) -> RunAccepted:
    config = get_config(request)
    registry = get_registry(request)
    try:
        module = validate_module(req.module)
        args = validate_args(req.args)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not owns(config.module_prefixes, module):
        raise HTTPException(
            status_code=403,
            detail=f"module {module!r} is outside this server's area "
                   f"({config.area}: {config.module_prefixes})")
    tag = req.tag or _derive_tag(module, args)
    if not TAG_RE.fullmatch(tag):
        raise HTTPException(
            status_code=422,
            detail=f"tag {tag!r} does not fit the tag charset "
                   f"({TAG_RE.pattern})")
    entry, already_running = registry.submit(module, args, tag)
    if already_running:
        logger.info("job deduped: %s (%s) already running", tag, module)
    return RunAccepted(
        tag=entry.tag, module=entry.module, args=entry.args, pid=entry.pid,
        already_running=already_running, log_path=entry.log_path)


@router.get("/jobs/status", response_model=RunStatusResponse)
async def job_status(
        request: Request,
        tags: str = Query(description="Comma-separated process-id-tags."),
) -> RunStatusResponse:
    registry = get_registry(request)
    wanted = [t for t in (tags or "").split(",") if t]
    return RunStatusResponse(statuses=registry.status_many(wanted))


@router.get("/jobs/result")
async def job_result(request: Request, tag: str = Query(
        description="The process-id-tag.")) -> dict:
    """One run's outcome + captured output; 404 (the Express error
    envelope) for an unknown or evicted tag."""
    result = get_registry(request).result(tag)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"no run known under tag {tag!r} "
            "(finished runs evict LRU-bounded)")
    return result


@router.post("/jobs/{tag}/stop", response_model=StopResponse)
async def job_stop(request: Request, tag: str) -> StopResponse:
    stopped = get_registry(request).stop(tag)
    if not stopped:
        logger.info("stop asked for tag %r — no active run", tag)
    return StopResponse(tag=tag, stopped=stopped)
