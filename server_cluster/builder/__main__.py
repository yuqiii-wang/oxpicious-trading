"""server_cluster.builder.__main__ — run the resident builder API.

    python -m server_cluster.builder [--host H] [--port P] [--config PATH]

ALWAYS-ON: registered in ``_common/service_heartbeat.py``
MONITORED_SERVICES (the service name IS this module path — the stream
watchdog's spawn contract); RAM-only pre_check (a builder API needs no
GPU — its cmd children do their own cudf bootstrap). No residents
today: builds.* are one-shot batch pipelines.
"""
from __future__ import annotations

import argparse

# Light resource gate (RAM only) — before any heavy import.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("server_cluster.builder")


def main() -> None:
    from server_cluster.builder import SERVICE
    from server_cluster._common.server import launch

    launch(
        SERVICE, "builder",
        "Resident builder API: the entry over the builds.* area's cmds "
        "(run/status/result/stop of the stats-building pipelines), "
        "serving the data_viz API instead of direct WSL spawns.")


if __name__ == "__main__":
    main()
