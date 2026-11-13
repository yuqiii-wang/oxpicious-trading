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
supervision of its area's resident processes (the stream pollers for
the downloader) while the stream watchdog supervises the servers
themselves (two-level supervision; the servers are in
``_common/service_heartbeat.py`` MONITORED_SERVICES). The FIFTH area
``llm`` (migrated from ``llm_agents.local_model_host`` 2026-11) is the
resident local-model host: it serves its OWN model-host app instead of
the shared jobs surface, so it keeps its own ``create_app`` /
composition root in ``server_cluster/llm`` and no jobs routers.

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
