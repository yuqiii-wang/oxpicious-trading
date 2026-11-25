"""server_cluster._common.maintainer_client — the cluster's HTTP client
for the maintainer's process face (port 8775).

Since the 2026-11-21 executor demotion (control unification plan
Phase 2) the maintainer is clock + janitor + EMERGENCY spawn only: the
supervisors spawn their own residents in process again, and the ONE
remaining direct-control path is the emergency spawn of another area's
resident, gated server-side on that area's scope server being
unreachable. This module carries the client side of that face:

* :func:`emergency_start_via_maintainer` — the FleetOps ensure-up leg:
  an unreachable jobs area's down residents are asked of the maintainer
  (``POST /processes/{service}/start`` — idempotent there: a live
  instance is reported back, never duplicated). Returns the pid or
  None (refused / unreachable / bad answer — the caller records the
  failure row and moves on; ensure-up never raises).
* :func:`restart_via_maintainer` — DEPRECATED 2026-11-21, no callers
  left (the supervisors spawn in process): kept through the
  observation window, then deleted together with the
  ``AreaConfig.restart_via_maintainer`` field.

The request itself rides :mod:`_common.internal_request` (the repo's
ONE internal HTTP executor — stdlib urllib under the hood, the fleet's
no-new-dependency rule).
"""
from __future__ import annotations

import logging
from typing import Any

from _common.internal_request import InternalRequestError, post_json

from .config import ClusterConfig

logger = logging.getLogger(__name__)

# terminate grace (the maintainer's stop_grace_s, 10s) + spawn, padded.
_REQUEST_TIMEOUT_S = 25.0


def _maintainer_base_url() -> str | None:
    """The maintainer server's base url from the cluster config (None on
    any config trouble — the caller degrades to its fallback)."""
    try:
        area = ClusterConfig.load().areas["maintainer"]
    except Exception as exc:  # noqa: BLE001 — config trouble = fallback
        logger.warning("maintainer base url unresolved (%r)", exc)
        return None
    return f"http://{area.host}:{area.port}"


def emergency_start_via_maintainer(service: str) -> int | None:
    """POST one EMERGENCY resident start to the maintainer; returns the
    pid (a fresh spawn's or an already-running instance's), or None
    when the maintainer refused / is unreachable / answered badly.
    Never raises — the ensure-up leg records None as that resident's
    failed action row and moves on."""
    base = _maintainer_base_url()
    if base is None:
        return None
    url = f"{base}/processes/{service}/start"
    try:
        return _new_pid_doc(post_json(url, timeout_s=_REQUEST_TIMEOUT_S))
    except (InternalRequestError, ValueError) as exc:  # noqa: BLE001 —
        logger.warning("%s: emergency maintainer start failed (%r)",
                       service, exc)  # the caller owns the fallback row
        return None


def restart_via_maintainer(service: str) -> int | None:
    """DEPRECATED 2026-11-21 (executor demotion) — no callers left; the
    supervisors spawn their residents in process. Former contract: POST
    one resident restart to the maintainer; returns the NEW pid, or
    None when the maintainer refused / is unreachable / answered badly.
    Kept through the observation window, then deleted with the
    ``restart_via_maintainer`` config field."""
    base = _maintainer_base_url()
    if base is None:
        return None
    try:
        if service == ClusterConfig.load().areas["maintainer"].service:
            return None  # the maintainer cannot restart itself
    except Exception:  # noqa: BLE001 — config trouble = fallback
        return None
    url = f"{base}/processes/{service}/restart"
    try:
        return _new_pid_doc(post_json(url, timeout_s=_REQUEST_TIMEOUT_S))
    except (InternalRequestError, ValueError) as exc:  # noqa: BLE001 —
        logger.warning("%s: maintainer restart failed (%r)", service, exc)
        return None


def _new_pid_doc(doc: Any) -> int:
    """The control response's pid — one typed decode: a non-object body
    or a missing / non-positive pid is a ValueError like any other
    failure (the caller owns its fallback)."""
    if not isinstance(doc, dict):
        raise ValueError(f"non-object control response: {doc!r}")
    pid = int(doc.get("pid") or 0)
    if pid <= 0:
        raise ValueError(f"no pid in the response: {doc!r}")
    return pid
