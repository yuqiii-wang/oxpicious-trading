"""llm_agents.sandbox — the dockerized Python sandbox stack.

Replaces the former local subprocess sandbox (the old
``llm_agents/deep_agents/core/sandbox.py`` design): LLM-generated files
now run inside the ``sagacient/cutepandas`` py3.12 container (compose
service ``py-sandbox``, always live), with this package's MCP daemon as
the middleware delegating to it.

  * ``runner.py`` — the IN-CONTAINER stdlib-only HTTP API (copied into
    the image by the Dockerfile; sessions under /home/pandas/sandbox).
  * ``docker_api.py`` — the middleware-side HTTP client with the
    last-mile ``docker start`` self-heal.
  * ``server.py`` — ``SandboxMcpServer``: the MCP middleware exposing
    sandbox_new_session / sandbox_write_file / sandbox_run /
    sandbox_read_file / sandbox_list_files / sandbox_health.
  * ``__main__.py`` — launcher (``python -m llm_agents.sandbox``): the
    always-on streamable-HTTP daemon on 127.0.0.1:8789/mcp the stream
    watchdog supervises, or ``--stdio`` for one subprocess client.
  * ``Dockerfile`` — the image build (cutepandas + psycopg + runner).

The deep agent's ``core.sandbox.Sandbox`` is now a thin client of this
middleware (per-run MCP session, shared with its subagents).

No cudf/pandas in this package: files and JSON move between MCP
clients, this daemon and the container; computation happens inside the
container.
"""
