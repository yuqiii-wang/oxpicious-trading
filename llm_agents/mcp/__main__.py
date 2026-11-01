"""__main__.py — start the read-only DB MCP server (stdio transport).

The server itself lives in ``readonly_server.py`` as ``DbReadonlyServer``;
this module is the thin launcher: CLI args, the stream-watchdog
heartbeat, and ``run()``.

The heartbeat service name IS the module path (``llm_agents.mcp`` —
the stream watchdog's spawn contract: ``python -m <service>``).

No cudf/pandas in this module graph: the server only passes SQL text
and JSON-able row results, so the cudf.pandas activation that pipeline
entry points perform is not needed here.
"""
from __future__ import annotations

import argparse
import logging

from _common.service_heartbeat import register_service

from .readonly_server import DbReadonlyServer


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="oxpicious read-only DB MCP server",
    )
    ap.add_argument(
        "--stdio", action="store_true",
        help="serve ONE client over stdin/stdout (the mode an MCP client "
             "launches as a subprocess). Without this flag the server "
             "runs as the always-on daemon: streamable HTTP on "
             "127.0.0.1, the mode the stream watchdog spawns.",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    # Liveness heartbeat for database/stream_watchdog.py — the watchdog
    # starts this service when no heartbeat row exists and restarts it
    # when the beat goes stale. Must stay registered for the server's
    # whole life: the exit hooks record the stop on shutdown. Logging is
    # stderr-only (logging default): the stdio transport carries the
    # MCP JSON-RPC stream on stdout and must stay clean.
    register_service("llm_agents.mcp")
    DbReadonlyServer(host=args.host, port=args.port).run(stdio=args.stdio)


if __name__ == "__main__":
    main()
