"""server_cluster — the resident FastAPI cluster over the pipeline cmds.

Four always-on services, one per pipeline area, are the API entry to the
existing ``python -m`` cmds so the data_viz API (Express) no longer
spawns them directly:

=================  =========  ==========================================
service            port       owns (module prefixes)
=================  =========  ==========================================
downloader         8769       ``downloads.*``
builder            8770       ``builds.*``
analyzer           8771       ``analysis.*``
strategist         8772       ``strategy.*`` + ``live.*``
=================  =========  ==========================================

Each server is a light control-plane process: it NEVER imports a
pipeline package — area cmds run as detached ``python -u -m <module>``
subprocesses (the ``stream_watchdog._spawn`` shape: session-detached,
output appended to ``logs/<date>/<module>.log``) — and it takes the
supervision of its area's resident processes. The 2026-11 RESIDENT
MIGRATION is COMPLETE: the downloader's residents are the five stream
pollers, and the monitor's residents are everything else always-on —
the seven other cluster servers (four jobs areas + llm + config +
maintainer), the six infra daemons (db_health / db_table_audit /
sql_audit / mcp / sandbox / deep_agents server) and the stream WATCHDOG
itself (the mutual pair: the WSL boot hook starts the watchdog, the
monitor's supervisor ADOPTS it and revives it when it dies; the
watchdog's one scan charge is the monitor — a server cannot supervise
itself). Restarts EXECUTE on the maintainer (the cluster's ONE
resident-process executor); the supervisors stay the detectors. The
maintainer also owns the SCHEDULES: one scheduler thread, per-job fire
groups — the 03:30 maintenance pass, the 07:00 fleet bring-up, and the
16:00 (A-share trading days) nightly pipeline chain (the sh tier's
successor: the ``scripts/orchestration/*.json`` tiers driven serially
through the monitor area's orchestration runner —
``maintainer/pipeline.py``).

Shared logic lives in ``server_cluster._common`` (config, catalog,
process manager, run registry, resident supervisor, app factory,
routers); each area package is a thin ``SERVICE`` + ``__main__``
launcher. House style follows the llm area (the original template,
``server_cluster/llm`` ex-``llm_agents.local_model_host``):
``create_app`` factory + ``app.state`` deps + per-resource routers +
lifespan threads, pydantic request/response models, loopback-only
bind, ``register_service`` heartbeat (the service name IS the module
path — the watchdog's spawn contract), light ``pre_check`` before the
heavy imports (``service_heartbeat`` transitively pulls pandas, so it
imports function-locally in ``main()``).

Run one: ``python -m server_cluster.downloader`` (etc.). The cluster
config of record is ``server_cluster/cluster_config.json``; resident
desired-state persists under the gitignored ``server_cluster/_state/``.
"""
