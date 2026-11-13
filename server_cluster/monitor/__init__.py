"""server_cluster.monitor — the cluster's ops area (2026-11).

The resident FastAPI over the operations surface: the shared jobs
surface runs the ``database.*`` / ``llm_agents.*`` / ``test.*`` modules
the data_viz API used to spawn locally, and this package's own routers
add the fleet health check, the resident aggregation + ensure-up
bring-back, and the orchestration tier runner. (The daily 07:00
bring-up schedule moved to the maintainer area 2026-11 —
``maintainer/bring_up.py`` fires this area's
``POST /residents/ensure-up``.)
"""
SERVICE = "server_cluster.monitor"
