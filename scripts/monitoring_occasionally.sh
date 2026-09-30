#!/usr/bin/env bash

# Run before heavy writes so replica lag or WAL slot pinning is visible.
# Alert-only: monitoring failures do not prevent the scheduled work.
python -m database.monitoring
python -m database.monitoring --schema-drift