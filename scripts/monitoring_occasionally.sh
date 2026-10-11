#!/usr/bin/env bash

# Run before heavy writes so replica lag, WAL slot pinning, or a pg_wal
# dir growing past its max_wal_size recycle horizon are visible first.
# The default health check = replica state + replay backlog + slot WAL
# retention + pg_wal dir size + downloads-stream heartbeat liveness.
# Alert-only: monitoring failures do not prevent the scheduled work.
python -m database.monitoring
python -m database.monitoring --schema-drift
