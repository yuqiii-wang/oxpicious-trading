"""server_cluster._common.maintainer_client — the DETECTORS' HTTP client
for the cluster's ONE resident-process executor (the maintainer server,
8775).

The monitor (its 7 server-residents) and the downloader (its 5 poller
residents) detect dead/stale children but no longer spawn them
themselves: a restart rides ``POST /processes/{service}/restart`` here.
The base URL resolves through ``ClusterConfig`` like every cluster HTTP
client (stdlib urllib only — no new dependency, the fleet's own rule).

The maintainer's OWN service is refused client-side (returns None → the
caller falls back to its in-process spawn): a process cannot reliably
restart itself over a request that dies with it, and at monitor boot the
maintainer is down anyway — that in-process fallback IS the bootstrap.
The maintainer's routes 409 the same request (defense in depth).
"""
from __future__ import annotations

import json
import logging
import urllib.request

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
        req = urllib.request.Request(
            url, method="POST", data=b"{}",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        pid = int(doc.get("pid") or 0)
        if pid <= 0:
            raise ValueError(f"no pid in the response: {doc!r}")
        return pid
    except Exception as exc:  # noqa: BLE001 — fallback is the contract
        logger.warning("%s: maintainer restart failed (%r) — falling "
                       "back to the in-process spawn", service, exc)
        return None
