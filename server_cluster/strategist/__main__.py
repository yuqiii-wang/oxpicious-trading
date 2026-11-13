"""server_cluster.strategist.__main__ — run the resident strategist API.

    python -m server_cluster.strategist [--host H] [--port P] [--config PATH]

ALWAYS-ON: registered in ``_common/service_heartbeat.py``
MONITORED_SERVICES (the service name IS this module path — the stream
watchdog's spawn contract); RAM-only pre_check (the strategist API
never touches frames — its cmd children do their own cudf bootstrap /
GPU handling, e.g. _optm_engine's ``--gpu`` pre-scan). No residents
today: strategy.*/live.* are one-shot pipelines.
"""
from __future__ import annotations

import argparse

# Light resource gate (RAM only) — before any heavy import.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("server_cluster.strategist")


def main() -> None:
    from server_cluster.strategist import SERVICE
    from server_cluster._common.server import launch

    launch(
        SERVICE, "strategist",
        "Resident strategist API: the entry over the strategy.*/live.* "
        "areas' cmds (backtest runs, algo training, live signal checks "
        "- run/status/result/stop), serving the data_viz API instead "
        "of direct WSL spawns.")


if __name__ == "__main__":
    main()
