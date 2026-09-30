"""Shared database schema/DDL ownership marker.

The `database/` directory holds the DB cluster definition
(docker-compose.yml, init scripts, replica entrypoint, pg_hba.conf,
sql/) plus the replica monitoring tooling (database.monitoring). Marked
as a package so `python -m database.monitoring` resolves from the repo
root like every other pipeline entry point.
"""
