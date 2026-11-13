"""server_cluster._common.server — the composition root + the shared
launcher.

``ClusterServer`` is the thin composition the four jobs-area packages
instantiate (ONE config + the wired app — the LocalModelHost shape; the
``llm`` area keeps its own shell + app, see ``server_cluster/llm``).
``launch`` is the single ``__main__`` body the four jobs areas share
(the ``llm`` area keeps its own model-host body): the light
``pre_check`` runs FIRST in each area's ``__main__`` (before the heavy
imports — the local_model_host discipline), then argparse, config
resolution (defaults ← cluster_config.json ← CLI), the heartbeat
registration (the service name IS the module path — the stream
watchdog's spawn contract; imported LAZILY: it transitively pulls
pandas via db_commons), and uvicorn."""
from __future__ import annotations

import argparse
import logging
from typing import Optional

from .config import AreaConfig, ClusterConfig

logger = logging.getLogger(__name__)


class ClusterServer:
    """One area server: one config + the wired app."""

    def __init__(self, config: AreaConfig) -> None:
        from .app_factory import create_app

        self.config = config
        self.app = create_app(config)

    def serve(self, host: Optional[str] = None,
              port: Optional[int] = None) -> None:
        """Bind uvicorn (config defaults; CLI overrides win)."""
        import uvicorn

        uvicorn.run(self.app, host=host or self.config.host,
                    port=port or self.config.port, log_level="info")


def launch(service: str, area: str, description: str) -> None:
    """The four ``__main__`` bodies in one place.

    Precondition (each ``__main__``): the light ``pre_check`` ran as the
    first executable line. Expects the caller's package to define
    ``SERVICE`` (imported lazily HERE so the argparse stays light)."""
    parser = argparse.ArgumentParser(
        prog=f"python -m {service}", description=description)
    parser.add_argument("--host", default=None,
                        help="Bind host (default: the config's, "
                        "127.0.0.1).")
    parser.add_argument("--port", type=int, default=None,
                        help="Bind port (default: the config's area "
                        "port).")
    parser.add_argument("--config", default=None,
                        help="cluster_config.json path (default: the "
                        "package's own file; absent -> built-in "
                        "defaults).")
    args = parser.parse_args()

    config = ClusterConfig.load(args.config).area(area)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    # Always-on as a resident (the monitor area's SERVER_RESIDENTS — the
    # service name IS this module path, the resident spawn contract). The
    # heartbeat keeps its historical scope ("server_cluster", area) even
    # though the service left MONITORED_SERVICES in the 2026-11 migration
    # — register_service needs the scope passed explicitly for services
    # outside the registry. Import lazily: service_heartbeat pulls
    # db_commons (and transitively pandas) — the __main__'s pre_check
    # import stays light.
    from _common.service_heartbeat import register_service

    register_service(service, scope="server_cluster", sub_scope=area)

    server = ClusterServer(config)
    logger.info("%s: serving %s:%d (residents=%s)", service,
                config.host, config.port,
                ", ".join(r.service for r in config.residents) or "none")
    server.serve()
