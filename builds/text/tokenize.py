"""builds.text.tokenize — naive tokenization of a question into corpus keywords.

The News page's question bar drives two searches; the LOCAL one searches the
stored corpus (text.news) via the same /api/news/items API as the old keyword
bar. A raw Chinese question can't be ILIKE-searched directly, so this module
splits it NAIVELY into keywords of the curated news taxonomy
(builds.text.keywords.KeywordTaxonomy — downloads/macro/gov/keywords.json ∪
catalog labels): every taxonomy keyword that appears in the question becomes
a search token. Matching is plain substring (whole-word for pure-ASCII), the
same convention the build uses for extraction.

Post-processing: tokens that are substrings of another matched token are
dropped (工商银行 keeps 银行 out) and the rest are ordered by first occurrence
in the question, so the API-side OR search and the UI chip read naturally.

CLI (the API route spawns this via the shared WSL py-runner):

    python -m builds.text.tokenize --text "…" [--limit 8]
    python -m builds.text.tokenize --text-b64 <base64(utf-8)>

Output: a single marker-prefixed JSON line on stdout
(``TOKENIZE_RESULT_MARKER``) — {"text", "tokens", "matched"} — that the API
parses; nothing else is printed (no logging setup in this module).
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from typing import List

from builds.text.keywords import KeywordTaxonomy

# Stdout marker prefixing the JSON result — mirrors the zhihu downloader's
# SEARCH_RESULT_MARKER convention (defensive: keep stdout parseable even if
# something in the import chain prints).
TOKENIZE_RESULT_MARKER = "@@NEWS_TOKENIZE_JSON@@"

DEFAULT_LIMIT = 8


def tokenize(text: str, *, limit: int = DEFAULT_LIMIT) -> List[str]:
    """Split *text* naively into taxonomy keywords, most-specific kept.

    Returns at most *limit* keywords ordered by first occurrence in *text*.
    """
    if not text or not text.strip():
        return []
    taxonomy = KeywordTaxonomy()
    matched = list(taxonomy.match(text)[0].keys())
    if not matched:
        return []

    # Drop tokens contained in another matched token (keep the specific one):
    # 工商银行 matched → its substring 银行 adds nothing to an OR search.
    kept = [m for m in matched if not any(m != o and m in o for o in matched)]

    # Order by first occurrence in the question (ASCII keywords
    # case-insensitively), longer first at the same position.
    lowered = text.lower()

    def first_pos(kw: str) -> int:
        pos = text.find(kw)
        if pos < 0:
            pos = lowered.find(kw.lower())  # ASCII keyword, different case
        return pos if pos >= 0 else len(text)

    kept.sort(key=lambda kw: (first_pos(kw), -len(kw)))
    return kept[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Naively tokenize a question into news-taxonomy keywords")
    parser.add_argument("--text", type=str, default=None,
                        help="Raw question text to tokenize.")
    parser.add_argument("--text-b64", type=str, default=None,
                        help="Same as --text but base64(utf-8) encoded (API route).")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max tokens returned. Default: {DEFAULT_LIMIT}")
    args = parser.parse_args()

    text = args.text
    if not text and args.text_b64:
        text = base64.b64decode(args.text_b64).decode("utf-8")

    tokens = tokenize(text or "", limit=args.limit)
    try:  # WSL is UTF-8 already; guard native-Windows invocations
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    print(
        TOKENIZE_RESULT_MARKER
        + json.dumps({"text": text or "", "tokens": tokens,
                      "matched": len(tokens)}, ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
