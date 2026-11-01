---
name: py-sandbox-env
description: The dockerized Python sandbox env for LLM-generated code in the oxpicious-trading repo — py3.12 (sagacient/cutepandas) with pandas, numpy, scipy, scikit-learn, statsmodels, polars, duckdb, psycopg; reached through the llm_agents.sandbox MCP middleware; kept always-live by compose restart + healthcheck + the middleware's docker self-heal. Use whenever writing or debugging code that RUNS in the LLM sandbox (sandbox_* tools, deep-agent compute), changing the sandbox env/limits (llm_agents/sandbox/), or checking why the sandbox is down.
---

# Docker py sandbox env (`llm_agents.sandbox` + `py-sandbox` compose service)

## What the env is

`sagacient/cutepandas` py3.12 image (labels report 1.0.4; tag `latest`
is what exists locally), plus `psycopg` added by
`llm_agents/sandbox/Dockerfile`. Python **3.12.13**. Key libs:
pandas 2.3.3, numpy 1.26.4, scipy 1.17.1, scikit-learn 1.8.0,
statsmodels 0.14.6, matplotlib 3.10.8, seaborn 0.13.2, polars 1.38.1,
pyarrow 23.0.1, duckdb 1.4.4, geopandas 1.1.2, networkx, sympy,
openpyxl / xlsxwriter, scikit-image, requests, psycopg 3. NO cudf, no
torch, no DB-write drivers beyond read-only psycopg. Scripts that
import cudf or repo code (`_common.*`) CANNOT run there — that env is
for standalone pandas/numpy computation over fetched data, not repo
pipelines.

## How code gets in (the delegation chain)

```
LLM agent (sandbox_* builtin tools / any MCP client)
  → llm_agents.sandbox MCP middleware  (127.0.0.1:8789/mcp, streamable
    HTTP; --stdio also supported; watchdog-supervised daemon)
  → oxpicious-py-sandbox container    (127.0.0.1:8788, runner API =
    llm_agents/sandbox/runner.py, stdlib-only)
  → python -B -u subprocess, cwd = /home/pandas/sandbox/<session>/
```

MCP tools: `sandbox_new_session` / `sandbox_write_file` /
`sandbox_run` / `sandbox_read_file` / `sandbox_list_files` /
`sandbox_health`. The deep agent's `Sandbox`
(`llm_agents/deep_agents/core/sandbox.py`) is a thin client: one
remote session per run, shared with subagents, minted lazily.

## Rules scripts must follow

- DB: plain `psycopg.connect()` picks up the preset PG* env → the
  **readonly_analytics** role (SELECT-only; writes fail server-side,
  `ReadOnlySqlTransaction`). Host is `db` (compose network), database
  `oxpicious-stats`. Prefer SQL aggregation over wide fetches.
- Caps: one write 512 KB; one read 1 MB (head+tail beyond that); run
  timeout default 120s / hard cap 600s (killed, partial output kept);
  stdout+stderr capped at 16k chars. Parent dirs of runtime writes do
  NOT auto-create — `os.makedirs(..., exist_ok=True)` first.
- Everything stays session-relative; `..`/absolute paths are rejected.

## Monitoring — the sandbox must be live as always

Three layers, check in this order when sandbox tools error:

1. Container: `docker ps --filter name=oxpicious-py-sandbox` (want
   `Up (healthy)`), `docker logs oxpicious-py-sandbox`,
   `curl http://127.0.0.1:8788/health`. Kept up by compose
   `restart: unless-stopped` + image HEALTHCHECK
   (`database/docker-compose.yml` service `py-sandbox`).
2. Middleware daemon: heartbeat row `llm_agents.sandbox` in
   `monitor.service_heartbeat` (watchdog starts/restarts it like the
   pollers); log `logs/<date>/llm_agents.sandbox.log`; spawn is
   `python -m llm_agents.sandbox`. The middleware also self-heals a
   stopped container (`docker start oxpicious-py-sandbox`) before
   failing a tool call.
3. From an agent's view: call the `sandbox_health` MCP tool — its
   `{"error": ...}` text names the layer that is down.

Host WSL full restart: `bash main_run.sh` (or
`scripts/stream_watchdog.sh` from an interactive shell) restarts the
watchdog, which restarts the middleware.

## Changing the sandbox

- Env/limits/session GC: edit `llm_agents/sandbox/runner.py` (runner
  caps) or `docker_api.py` (middleware side), then
  `docker compose build py-sandbox && docker compose up -d py-sandbox`
  (from `database/`) — session files survive on the repo-root
  `oxpicious-py-sandbox/` bind mount (gitignored: track nothing but
  its .gitignore).
- Tool surface/descriptions: `llm_agents/sandbox/server.py` (docstrings
  are the MCP descriptions) — restart the middleware daemon.
- The middleware talks plain HTTP to the runner; for a quick local
  runner without docker:
  `python llm_agents/sandbox/runner.py --host 127.0.0.1 --port 8788 --root temp_scripts/sandbox-root`.
