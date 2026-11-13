"""server_cluster.downloader.__main__ — run the resident downloader API.

    python -m server_cluster.downloader [--host H] [--port P] [--config PATH]

ALWAYS-ON: registered in ``_common/service_heartbeat.py``
MONITORED_SERVICES (the service name IS this module path — the stream
watchdog's spawn contract), so the watchdog starts/restarts it like the
other daemons; ``main()`` registers the heartbeat. The pre_check gate is
deliberately RAM-only: a downloader API needs no GPU (the cmd children
do their own cudf bootstrap). Its RESIDENTS — the five stream pollers —
auto-start on the supervisor's first pass and are THIS server's
supervision responsibility (two-level supervision).
"""
from __future__ import annotations

import argparse

# Light resource gate (RAM only) — before any heavy import.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("server_cluster.downloader")


def main() -> None:
    from server_cluster.downloader import SERVICE
    from server_cluster._common.server import launch

    launch(
        SERVICE, "downloader",
        "Resident downloader API: the entry over the downloads.* area's "
        "cmds (batch download jobs, status/results, stop) + the "
        "resident stream-poller control (start/stop/restart), serving "
        "the data_viz API instead of direct WSL spawns.")


if __name__ == "__main__":
    main()
