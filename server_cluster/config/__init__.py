"""server_cluster.config — the cluster's read-only configuration area
(2026-11).

A lean resident FastAPI (no jobs surface, no residents — the llm-area
shape): the UI API reads configuration over HTTP instead of from
disk/DB. GET /catalog serves the service catalog (MONITORED_SERVICES +
the service_config tree + per-area residents), GET /orchestration the
step files, GET /cluster the cluster topology, GET /health and /stats
the standard liveness.
"""
SERVICE = "server_cluster.config"
