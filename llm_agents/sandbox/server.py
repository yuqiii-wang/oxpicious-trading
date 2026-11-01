"""llm_agents.sandbox.server — SandboxMcpServer: the MCP middleware of
the dockerized Python sandbox.

THE DELEGATION CHAIN (this package replaced the old local subprocess
sandbox — ``git log`` for the previous llm_agents.deep_agents.core.
sandbox design):

    LLM agent (deep agent / any MCP client)
      → MCP tools (this daemon, streamable HTTP on 127.0.0.1:8789/mcp)
      → HTTP (docker_api.SandboxDockerClient, with last-mile self-heal)
      → oxpicious-py-sandbox container
        (sagacient/cutepandas py3.12 stack, runner.py API, sessions on
        the E:/docker-data/oxpicious-py-sandbox bind mount)

The middleware moves file text and JSON only; every script executes
INSIDE the container with the cutepandas stack (pandas/numpy/scipy/
scikit-learn/statsmodels/matplotlib/polars/pyarrow/duckdb/…) and the
compose-preset PG* env of the READ-ONLY readonly_analytics role — the
role is the real DB guard, the container is the isolation boundary.

Tools (mirroring the old local sandbox's four, plus session plumbing):

  * sandbox_new_session() — mint one session directory (the deep
    agent's Sandbox client mints one per run and shares it with its
    subagents);
  * sandbox_write_file(session, path, content) — UTF-8 text, 512 KB cap;
  * sandbox_run(session, path, timeout_s=120) — subprocess python -B -u,
    killed at the timeout (hard cap 600s), JSON {script, exit_code,
    timed_out, duration_s, db, stdout, stderr} with capped output;
  * sandbox_read_file(session, path, max_bytes=65536) — text back,
    head+tail truncation beyond the cap;
  * sandbox_list_files(session) — JSON array of {path, bytes, modified};
  * sandbox_health() — container liveness + python version + session
    count + the DB access note (the monitoring probe).

Failures surface as {"error": …} result objects (never exceptions) so
an LLM caller can read the message and recover.

Access model: bind 127.0.0.1 only, no auth — the repo's localhost
daemon convention; the docker port is loopback-published the same way.

No cudf/pandas in this module graph: the server only relays file text
and JSON between MCP clients and the container's HTTP API.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from llm_agents.sandbox.docker_api import (
    SandboxDockerClient, SandboxError,
)

logger = logging.getLogger(__name__)


class SandboxMcpServer:
    """MCP server exposing the dockerized py-sandbox runner API."""

    def __init__(
        self,
        name: str = "oxpicious-py-sandbox",
        title: str = "oxpicious-trading docker py sandbox",
        host: str = "127.0.0.1",
        port: int = 8789,
        base_url: str | None = None,
    ) -> None:
        self._default_host = host
        self._default_port = port
        self._client = SandboxDockerClient(
            base_url=base_url) if base_url else SandboxDockerClient()
        self._server = MCPServer(
            name=name,
            title=title,
            instructions=(
                "Dockerized Python 3.12 sandbox (sagacient/cutepandas) "
                "for real computation: pandas, numpy, scipy, "
                "scikit-learn, statsmodels, matplotlib, seaborn, "
                "polars, pyarrow, duckdb, openpyxl, requests. Mint a "
                "session with sandbox_new_session, then "
                "sandbox_write_file your .py script (and any data "
                "files), sandbox_run it, and sandbox_read_file / "
                "sandbox_list_files the results. The environment "
                "presets PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD to "
                "the READ-ONLY readonly_analytics role, so `import "
                "psycopg; psycopg.connect()` connects SELECT-only. "
                "Prefer ONE script over many small queries; print "
                "concisely (output capped) and write anything large to "
                "a session file."
            ),
        )
        self._register_tools()

    def _register_tools(self) -> None:
        register = self._server.tool()
        register(self.sandbox_new_session)
        register(self.sandbox_write_file)
        register(self.sandbox_run)
        register(self.sandbox_read_file)
        register(self.sandbox_list_files)
        register(self.sandbox_health)

    # ------------------------------------------------------------------
    # The tools (docstrings are the MCP descriptions)
    # ------------------------------------------------------------------

    def sandbox_new_session(self) -> dict:
        """Mint one fresh sandbox session directory (per-run scratch
        space inside the docker py sandbox). Returns {"session",
        "root", "db"}; pass the session id on every other sandbox_*
        call. Sessions idle >14 days are reaped automatically."""
        try:
            return self._client.new_session()
        except SandboxError as exc:
            return {"error": str(exc)}

    def sandbox_write_file(self, session: str, path: str,
                           content: str) -> dict:
        """Write a UTF-8 text file into the session's scratch directory
        inside the docker py sandbox (paths are session-relative, e.g.
        'study.py' or 'out/dates.csv'; no '..'; 512 KB cap). Use it for
        the .py scripts you will run with sandbox_run and for the data
        files those scripts consume."""
        try:
            return self._client.write_file(session, path, content)
        except SandboxError as exc:
            return {"error": str(exc)}

    def sandbox_run(self, session: str, path: str,
                    timeout_s: int = 120) -> dict:
        """Execute one .py file previously written into the session,
        inside the docker py sandbox (py3.12 + pandas/numpy/scipy/
        scikit-learn/statsmodels/polars/pyarrow/duckdb). The script's
        working directory IS the session directory; sibling files are
        importable; relative writes land inside the session. Returns
        JSON {script, exit_code, timed_out, duration_s, db, stdout,
        stderr} with output capped. The environment presets the
        READ-ONLY database credentials: PGHOST/PGPORT/PGDATABASE/
        PGUSER/PGPASSWORD point at the readonly_analytics role, so
        `psycopg.connect()` connects with SELECT-only rights and DB
        writes fail server-side."""
        try:
            return self._client.run(session, path, timeout_s)
        except SandboxError as exc:
            return {"error": str(exc)}

    def sandbox_read_file(self, session: str, path: str,
                          max_bytes: int = 65536) -> dict:
        """Read one file back out of the session directory (the files a
        script wrote, logs, …) as UTF-8 text; files beyond max_bytes
        return head+tail with a truncation marker. Returns {"session",
        "path", "bytes", "content"}."""
        try:
            return self._client.read_file(session, path, max_bytes)
        except SandboxError as exc:
            return {"error": str(exc)}

    def sandbox_list_files(self, session: str) -> str:
        """List every file currently in the session directory — JSON
        array of {path, bytes, modified}, sorted by path."""
        try:
            return _json(self._client.list_files(session))
        except SandboxError as exc:
            return _json({"error": str(exc)})

    def sandbox_health(self) -> dict:
        """Liveness + configuration of the docker py sandbox container:
        {"status", "service", "python", "root", "sessions", "db",
        "time_utc"}. The monitoring probe — also the first call to make
        when debugging sandbox tool errors."""
        try:
            return self._client.health()
        except SandboxError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self, stdio: bool = False, host: str | None = None,
            port: int | None = None) -> None:
        """Run until stopped: stdio transport for one subprocess client,
        or (default) the always-on streamable-HTTP daemon mode."""
        if stdio:
            logger.info("starting oxpicious-py-sandbox MCP middleware "
                        "(stdio)")
            self._server.run(transport="stdio")
        else:
            host = host if host is not None else self._default_host
            port = port if port is not None else self._default_port
            logger.info(
                "starting oxpicious-py-sandbox MCP middleware "
                "(streamable http on %s:%d/mcp; sandbox container at "
                "%s)", host, port, self._client.base_url,
            )
            self._server.run(transport="streamable-http", host=host,
                             port=port)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
