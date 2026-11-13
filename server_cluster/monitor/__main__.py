"""server_cluster.monitor.__main__ — run the resident ops API.

    python -m server_cluster.monitor [--host H] [--port P] [--config PATH]

ALWAYS-ON: registered in ``_common/service_heartbeat.py``
MONITORED_SERVICES (the service name IS this module path — the stream
watchdog's spawn contract), so the watchdog starts/restarts it like the
other daemons; ``main()`` registers the heartbeat. Keeps its own launch
body (like the llm area) because its app is the shared jobs surface
PLUS this package's fleet/orchestration routers — ``launch()``'s plain
``ClusterServer`` builds only the shared surface. (The daily 07:00
bring-up scheduler moved to the maintainer area 2026-11.) The pre_check
gate is deliberately RAM-only.
"""
from __future__ import annotations

import argparse

# Light resource gate (RAM only) — before any heavy import.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("server_cluster.monitor")


def main() -> None:
    from server_cluster.monitor import SERVICE
    from server_cluster._common.server import ClusterServer
    from server_cluster.monitor.app import create_monitor_app

    parser = argparse.ArgumentParser(
        prog=f"python -m {SERVICE}",
        description="Resident ops API: jobs for the database.*/"
        "llm_agents.*/test.* modules + the fleet health check, resident "
        "ensure-up, and the orchestration tier runner.")
    parser.add_argument("--host", default=None,
                        help="Bind host (default: the config's, 127.0.0.1).")
    parser.add_argument("--port", type=int, default=None,
                        help="Bind port (default: the config's area port).")
    parser.add_argument("--config", default=None,
                        help="cluster_config.json path (default: the "
                        "package's own file; absent -> built-in defaults).")
    args = parser.parse_args()

    from server_cluster._common.config import ClusterConfig

    config = ClusterConfig.load(args.config).area("monitor")
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    # Always-on under the stream watchdog (MONITORED_SERVICES — the
    # service name IS this module path, its spawn contract). Import
    # lazily: service_heartbeat pulls db_commons (and transitively
    # pandas) — the __main__'s pre_check import stays light.
    from _common.service_heartbeat import register_service

    register_service(SERVICE)

    import uvicorn

    app = create_monitor_app(config)
    logger.info("%s: serving %s:%d", SERVICE, config.host, config.port)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
