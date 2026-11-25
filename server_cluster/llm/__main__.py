"""server_cluster.llm.__main__ — run the resident host.

    python -m server_cluster.llm [--host H] [--port P]
        [--config PATH] [--idle-offload-s S] [--idle-unload-s S]
        [--no-warmup]

ALWAYS-ON: registered in ``_common/service_heartbeat.py``
MONITORED_SERVICES (the service name IS this module path — the stream
watchdog's spawn contract), so the watchdog starts/restarts it like
the other daemons; ``main()`` registers the heartbeat (the app-factory
form — create_app / TestClient — never does). This area KEEPS its own
launch body instead of the shared ``server_cluster._common.server
.launch()``: that launcher wires the cluster jobs app (ProcessManager /
RunRegistry), while the llm area serves its own model-host app. The
pre_check gate is deliberately RAM-only: the host must come up even
without CUDA (a missing GPU degrades per-model loads to CPU, surfaced
per request — never at startup).
"""
from __future__ import annotations

import argparse

# Light resource gate (RAM only) — no pandas/cudf in this process.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("server_cluster.llm")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m server_cluster.llm",
        description="Resident local-model host: OpenAI-compatible chat "
                    "completions + tokenization + load/offload "
                    "management over the repo's local models.")
    parser.add_argument("--host", default=None,
                        help="Bind host (default: the config's, 127.0.0.1).")
    parser.add_argument("--port", type=int, default=None,
                        help="Bind port (default: the config's, 8767).")
    parser.add_argument("--config", default=None,
                        help="host_config.json path (default: the package's "
                        "own file; absent -> built-in defaults).")
    parser.add_argument("--idle-offload-s", type=float, default=None,
                        help="Override the tier-1 idle-offload age in "
                        "seconds, GPU -> CPU (default: the config's, "
                        "600).")
    parser.add_argument("--idle-unload-s", type=float, default=None,
                        help="Override the tier-2 idle age in seconds "
                        "on top of the offload, CPU RAM weights drop "
                        "(default: the config's, 1200).")
    parser.add_argument("--no-warmup", action="store_true",
                        help="Skip the startup warm-up pass (models load "
                        "on first use / admin load instead).")
    args = parser.parse_args()

    from server_cluster.llm import SERVICE
    from server_cluster.llm.config import HostConfig

    config = HostConfig.load(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.idle_offload_s is not None:
        config.idle_offload_s = args.idle_offload_s
    if args.idle_unload_s is not None:
        config.idle_unload_s = args.idle_unload_s
    if args.no_warmup:
        config.warmup_on_start = False

    # Always-on as a monitor-area resident (SERVER_RESIDENTS — the
    # service name IS this module path, the resident spawn contract): the
    # heartbeat keeps the host resident so pipeline clients never meet a
    # cold server; it keeps the historical ("server_cluster", "llm")
    # scope. Import lazily: service_heartbeat pulls db_commons
    # (and transitively pandas) — the import above stays light.
    from _common.service_heartbeat import register_service

    register_service(SERVICE, scope="server_cluster", sub_scope="llm")

    import uvicorn

    from server_cluster.llm.server import LocalModelHost

    host = LocalModelHost(config)
    logger.info(
        "local model host: serving %s:%d (warmup=%s idle_offload_s=%.0f "
        "idle_unload_s=%.0f models=%s)", config.host, config.port,
        config.warmup_on_start, config.idle_offload_s,
        config.idle_unload_s, ", ".join(host.manager.ids()))
    uvicorn.run(host.app, host=config.host, port=config.port,
                log_level="info")


if __name__ == "__main__":
    main()
