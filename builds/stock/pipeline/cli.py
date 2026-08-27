"""CLI argument parsing for the stock builder (python -m builds.stock)."""
from __future__ import annotations

import argparse

from _common.build_commons import add_common_build_args


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the stock build pipeline."""
    ap = argparse.ArgumentParser(
        description="Build SZSE + SSE + BSE stock OHLCV+PE and insert to database (missing dates only)."
    )
    ap.add_argument("--limit", type=int, default=None, help="Dev: first N files only")
    ap.add_argument("--code", default=None,
                    help="Filter to a single stock code (e.g. 000001.SZ) for testing")
    add_common_build_args(ap)
    return ap.parse_args()


def normalize_code(code: str | None) -> str | None:
    """Normalize --code: strip whitespace and append an exchange suffix to
    bare 6-digit codes (inferred from the leading digit)."""
    if not code:
        return None
    c = code.strip()
    if "." not in c:
        if c.startswith("6"):
            c += ".SS"
        elif c.startswith(("0", "1", "3")):
            c += ".SZ"
        elif c.startswith(("4", "8", "9")):
            c += ".BJ"
        else:
            c += ".SZ"
    return c
