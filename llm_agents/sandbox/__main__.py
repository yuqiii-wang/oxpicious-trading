"""__main__.py — start the docker py-sandbox MCP middleware (stdio or
the always-on HTTP daemon).

The server itself lives in ``server.py`` as ``SandboxMcpServer``; this
module is the thin launcher: CLI args, the stream-watchdog heartbeat,
and ``run()`` — mirroring ``llm_agents.mcp``.

The heartbeat service name IS the module path (``llm_agents.sandbox`` —
the stream watchdog's spawn contract: ``python -m <service>``). The
daemon must stay live as always: the watchdog starts it when no
heartbeat row exists and restarts it when the beat goes stale, while
the CONTAINER half of the stack is kept live by the compose restart
policy + its HEALTHCHECK plus the middleware's ``docker start``
self-heal (docker_api.py).

No cudf/pandas in this module graph: the middleware relays file text
and JSON between MCP clients and the container's HTTP API.
"""
from __future__ import annotations

import argparse
import logging

from _common.service_heartbeat import register_service

from .server import SandboxMcpServer


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="oxpicious docker py-sandbox MCP middleware",
    )
    ap.add_argument(
        "--stdio", action="store_true",
        help="serve ONE client over stdin/stdout (the mode an MCP client "
             "launches as a subprocess). Without this flag the server "
             "runs as the always-on daemon: streamable HTTP on "
             "127.0.0.1, the mode the stream watchdog spawns.",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8789)
    args = ap.parse_args()

    # Liveness heartbeat — same contract as llm_agents.mcp, a
    # monitor-area resident now (2026-11 resident migration; the
    # monitor's supervisor starts/revives it, scope passed explicitly
    # since the service left MONITORED_SERVICES). Must stay registered
    # for the daemon's whole life; the exit hooks record the stop on
    # shutdown. Logging is stderr-only: the stdio transport carries MCP
    # JSON-RPC on stdout.
    register_service("llm_agents.sandbox", scope="database",
                     sub_scope="stream")
    SandboxMcpServer(host=args.host, port=args.port).run(
        stdio=args.stdio)


if __name__ == "__main__":
    main()
