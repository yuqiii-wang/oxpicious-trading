"""_common.tokenization CLI — query-side search segmentation for the
text/llm schemas' GIN ``search_tsv`` columns.

The document vectors are StructBERT-segmented (search_indexing), so a
query must be segmented the SAME way or ``search_tsv @@ tsquery``
matches nothing (a raw Chinese sentence is one giant ``simple``-config
lexeme).  The data_viz API (Node) cannot run the segmenter, so its
search services spawn this CLI — mirroring ``builds.text.tokenize`` —
via the shared WSL py-runner, then build the per-term full-text
clauses around the returned tokens:

    python -m _common.tokenization --text "央行 降准 银行"
    python -m _common.tokenization --text-b64 <base64(utf-8)>

The search string is split on whitespace into TERMS; every term goes
through :class:`SearchIndexer` — CJK-bearing terms are segmented (one
batched model call), non-CJK terms skip the model but still go through
the same post-process (skip-word filter + per-token cap; the ``simple``
config already handles latin text) — and each term returns its
space-joined token string, position-aligned with the input terms.  The
static.keyword_maps skip vocabulary (low-semantic function words:
其实/也/就是说, the/a/of) drops out of every term's tokens — a term made
ONLY of skip words comes back ``null`` so the caller falls back to its
substring search:

    @@SEARCH_TOKENS_JSON@@{"search": "…", "terms": ["…", "…"]}

Output: a single marker-prefixed JSON line on stdout (nothing else is
printed).  A term that fails to segment — or whose tokens are ALL skip
words — comes back as ``null`` so the caller can fall back to its
substring search for that term only.  The payload also carries
``keywords``: the static.keyword_maps taxonomy keywords the whole query's
token stream reconstructs via 1..4-gram joins
(keyword_ngrams.match_keywords, the document side's one-stream
granularity) — additive; readers of ``terms`` only are unaffected.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys

# Stdout marker prefixing the JSON result — mirrors the news tokenizer's
# TOKENIZE_RESULT_MARKER convention (defensive: keep stdout parseable even
# if something in the import chain prints).
SEARCH_TOKENS_MARKER = "@@SEARCH_TOKENS_JSON@@"

MAX_TERMS = 16


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Segment a search string into text/llm-schema query tokens")
    parser.add_argument("--text", type=str, default=None,
                        help="Raw search string (whitespace = term split).")
    parser.add_argument("--text-b64", type=str, default=None,
                        help="Same as --text but base64(utf-8) encoded (API route).")
    args = parser.parse_args()

    text = args.text
    if not text and args.text_b64:
        text = base64.b64decode(args.text_b64).decode("utf-8")
    text = (text or "").strip()

    terms = text.split()[:MAX_TERMS]
    # Every term goes through the indexer's post-process: CJK terms are
    # segmented, non-CJK terms skip the model but still hit the skip-word
    # filter and the per-token cap (English stops the same way — pure-ASCII
    # input never loads torch, the segmenter imports stay lazy).  An empty
    # result (a term made only of skip words, in either language) -> null
    # so the caller falls back to its substring search for that term.
    from .search_indexing import SearchIndexer
    from .keyword_ngrams import match_keywords

    tokens = SearchIndexer.get().tokenize_for_search_batch(terms)
    out: list[str | None] = [t if t else None for t in tokens]

    # Keyword n-gram matches over the whole query's concatenated token
    # stream — the document side's granularity (one stream per text), so a
    # multi-token keyword (大幅/上涨, limit/up) matches no matter where the
    # whitespace term-split fell.  A cross-term n-gram only fires when it
    # exactly equals a seeded keyword, i.e. the query really spells it.
    stream: list[str] = []
    for token_str in out:
        if token_str:
            stream.extend(token_str.split())
    keywords = match_keywords(stream)

    try:  # WSL is UTF-8 already; guard native-Windows invocations
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    print(
        SEARCH_TOKENS_MARKER
        + json.dumps({"search": text, "terms": out, "keywords": keywords},
                     ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
