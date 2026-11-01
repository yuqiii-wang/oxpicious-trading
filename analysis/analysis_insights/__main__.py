"""analysis.analysis_insights CLI.

  python -m analysis.analysis_insights resolve \
      --tab commons --page ma-spread \
      [--code 510050 --sec-type etf --industry BK0475] \
      [--date 2026-10-17 | --date 2026-09] \
      [--only rsi_14d,close] [--json] [--marker]
      Resolve the page's metric values for one ask context. --json
      pretty-prints; --marker prints one `@@INSIGHT_METRICS@@<json>`
      line (the Express service's spawn parses that marker, mirroring
      the ai-ask payload marker pattern).

The registry itself lives in the per-tab INSERT files beside the DDL
(database/sql/analysis/static/analysis_insight_metrics/02..05 — apply them to
re-sync insight_templates; the former json→python seed subcommand is
retired with the SQL-first migration).

No cudf.pandas activation on purpose: this module's graph never imports
pandas, and resolve is spawned per Ask AI modal open where import cost
is latency. If a future change adds pandas to the graph, activate first
per the repo convention.
"""
from __future__ import annotations

import argparse
import json
import sys

MARKER = "@@INSIGHT_METRICS@@"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m analysis.analysis_insights")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve", help="resolve one page's metrics")
    resolve.add_argument("--tab", required=True)
    resolve.add_argument("--page", required=True)
    resolve.add_argument("--code")
    resolve.add_argument("--sec-type")
    resolve.add_argument("--industry")
    resolve.add_argument("--date", help="as-of: YYYY-MM-DD or YYYY-MM")
    resolve.add_argument("--only", help="comma-separated metric slugs")
    resolve.add_argument("--json", action="store_true")
    resolve.add_argument("--marker", action="store_true",
                         help="emit one @@INSIGHT_METRICS@@<json> line")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "resolve":
        from analysis.analysis_insights.resolver import resolve
        only = [m for m in (args.only or "").split(",") if m] or None
        result = resolve(
            tab=args.tab, page=args.page, code=args.code,
            sec_type=args.sec_type, industry=args.industry,
            as_of=args.date, only=only)
        if args.marker:
            print(MARKER + json.dumps(result, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False,
                             indent=2 if args.json else None))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
