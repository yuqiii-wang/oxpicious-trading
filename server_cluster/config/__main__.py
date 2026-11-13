"""server_cluster.config.__main__ — run the resident configuration API.

    python -m server_cluster.config [--host H] [--port P] [--config PATH]

ALWAYS-ON: registered in ``_common/service_heartbeat.py``
MONITORED_SERVICES (the service name IS this module path — the stream
watchdog's spawn contract); ``main()`` registers the heartbeat. The
pre_check gate is deliberately RAM-only: a read-only config server
needs no GPU and its handlers do no frame compute.
"""
from __future__ import annotations

import argparse

# Light resource gate (RAM only) — before any heavy import.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("server_cluster.config")


def main() -> None:
    from server_cluster.config import SERVICE
    from server_cluster._common.config import ClusterConfig
    from server_cluster.config.app import create_config_app

    parser = argparse.ArgumentParser(
        prog=f"python -m {SERVICE}",
        description="Resident read-only configuration API: the service "
        "catalog, orchestration step files, and cluster topology as "
        "JSON for the data_viz API.")
    parser.add_argument("--host", default=None,
                        help="Bind host (default: the config's, 127.0.0.1).")
    parser.add_argument("--port", type=int, default=None,
                        help="Bind port (default: the config's area port).")
    parser.add_argument("--cluster-config", default=None,
                        help="cluster_config.json path (default: the "
                        "package's own file; absent -> built-in defaults).")
    args = parser.parse_args()

    cluster_config = ClusterConfig.load(args.cluster_config)
    config = cluster_config.areas["config"]
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    # Always-on as a monitor-area resident (SERVER_RESIDENTS — the
    # service name IS this module path, the resident spawn contract); the
    # heartbeat keeps its historical ("server_cluster", "config") scope.
    # Import lazily: service_heartbeat pulls db_commons (and transitively
    # pandas) — the __main__'s pre_check import stays light.
    from _common.service_heartbeat import register_service

    register_service(SERVICE, scope="server_cluster", sub_scope="config")

    import uvicorn

    app = create_config_app(config, cluster_config)
    logger.info("%s: serving %s:%d", SERVICE, config.host, config.port)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
