"""server_cluster._common.maintainer_client — the DETECTORS' HTTP client
for the cluster's ONE resident-process executor (the maintainer server,
8775).

The monitor (its 7 server-residents) and the downloader (its 5 poller
residents) detect dead/stale children but no longer spawn them
themselves: a restart rides ``POST /processes/{service}/restart`` here.
The request itself rides :mod:`_common.internal_request` (the repo's
ONE internal HTTP executor — stdlib urllib under the hood, the fleet's
no-new-dependency rule); this module owns the maintainer-specific
contract on top of it.

The maintainer's OWN service is refused client-side (returns None → the
caller falls back to its in-process spawn): a process cannot reliably
restart itself over a request that dies with it, and at monitor boot the
maintainer is down anyway — that in-process fallback IS the bootstrap.
The maintainer's routes 409 the same request (defense in depth).
"""
from __future__ import annotations

import logging
from typing import Any

from _common.internal_request import InternalRequestError, post_json

from .config import ClusterConfig

logger = logging.getLogger(__name__)

# terminate grace (the maintainer's stop_grace_s, 10s) + spawn, padded.
_REQUEST_TIMEOUT_S = 25.0


def restart_via_maintainer(service: str) -> int | None:
    """POST one resident restart to the maintainer; returns the NEW pid,
    or None when the maintainer refused / is unreachable / answered
    badly — the supervisor then spawns in-process (never leave a
    resident unsupervised because the executor is down)."""
    try:
        area = ClusterConfig.load().areas["maintainer"]
    except Exception as exc:  # noqa: BLE001 — config trouble = fallback
        logger.warning("maintainer base url unresolved (%r) — the "
                       "restart falls back in-process", exc)
        return None
    if service == area.service:
        return None  # the maintainer cannot restart itself (bootstrap)
    url = f"http://{area.host}:{area.port}/processes/{service}/restart"
    try:
        return _new_pid_doc(post_json(url, timeout_s=_REQUEST_TIMEOUT_S))
    except (InternalRequestError, ValueError) as exc:  # noqa: BLE001 — fallback is the contract
        logger.warning("%s: maintainer restart failed (%r) — falling "
                       "back to the in-process spawn", service, exc)
        return None


def _new_pid_doc(doc: Any) -> int:
    """The restart response's pid — one typed decode: a non-object body
    or a missing / non-positive pid is a ValueError like any other
    failure (the caller falls back in-process)."""
    if not isinstance(doc, dict):
        raise ValueError(f"non-object restart response: {doc!r}")
    pid = int(doc.get("pid") or 0)
    if pid <= 0:
        raise ValueError(f"no pid in the response: {doc!r}")
    return pid
