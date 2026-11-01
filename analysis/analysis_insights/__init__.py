"""analysis_insight_metrics — the Ask AI insight-templates registry.

One row per METRIC OF PARTICULAR INTEREST of one analysis page,
keyed ``analysis.<tab>.<page>.<metric>`` in
``analysis_insight_metrics.insight_templates``. The single source of truth is
the per-tab INSERT files beside the DDL —
``database/sql/analysis/static/analysis_insight_metrics/02..05_insight_templates_*.sql``
(each a per-tab DELETE+INSERT re-sync; the SQL-first migration retired
the former analysis_items.json → python seed path, content proven
identical). This package resolves per-ask values (actual curve values
from the analysis tables, on-demand computed when missing) for
``GET /api/analysis-insights/metrics`` (spawned by the Express service)
and the CLI.

Deliberately pandas/cudf-free: the resolve path is spawned per Ask AI
modal open, so the module graph stays psycopg + stdlib only (the
cudf.pandas activation convention guards pipelines whose graph imports
pandas — this one never does).
"""
