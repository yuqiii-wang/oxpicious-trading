"""server_cluster.maintainer — the cluster's maintenance area (2026-11).

A lean resident FastAPI (no jobs surface, no residents — the
config-area shape) that runs the cluster's periodic maintenance jobs on
a daily schedule: for now the log cleanup (delete ``logs/`` files and
date dirs older than one month). Jobs register on the
:class:`~server_cluster.maintainer.scheduler.MaintenanceScheduler`; the
``/jobs`` surface lists them and can fire one on demand.
"""
SERVICE = "server_cluster.maintainer"
