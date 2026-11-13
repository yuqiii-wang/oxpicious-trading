"""server_cluster.analyzer.__main__ — run the resident analyzer API.

    python -m server_cluster.analyzer [--host H] [--port P] [--config PATH]

ALWAYS-ON: registered in ``_common/service_heartbeat.py``
MONITORED_SERVICES (the service name IS this module path — the stream
watchdog's spawn contract); RAM-only pre_check (the analyzer API itself
never touches frames — its cmd children do their own cudf bootstrap
and GPU pre-checks). No residents today: analysis.* are one-shot batch
pipelines.
"""
from __future__ import annotations

import argparse

# Light resource gate (RAM only) — before any heavy import.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("server_cluster.analyzer")


def main() -> None:
    from server_cluster.analyzer import SERVICE
    from server_cluster._common.server import launch

    launch(
        SERVICE, "analyzer",
        "Resident analyzer API: the entry over the analysis.* area's "
        "cmds (run/status/result/stop of the analysis pipelines), "
        "serving the data_viz API instead of direct WSL spawns.")


if __name__ == "__main__":
    main()
