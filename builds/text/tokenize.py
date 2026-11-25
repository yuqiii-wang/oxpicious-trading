"""builds.text.tokenize — naive tokenization of a question into corpus keywords.

The News page's question bar drives two searches; the LOCAL one searches the
stored corpus (text.news) via the same /api/news/items API as the old keyword
bar. A raw Chinese question can't be ILIKE-searched directly, so this module
splits it NAIVELY into keywords of the curated news taxonomy
(builds.text.keywords.KeywordTaxonomy — the static.keyword_maps seed ∪
catalog metadata): every taxonomy keyword that appears in the question
becomes a search token. Matching is plain substring (whole-word for
pure-ASCII), the same convention the build uses for extraction.

Stock codes: A-share / BSE / index codes and their exchange-prefixed forms
(``600519``, ``SH600519``, ``600519.SH``, ``931743.CSI``, ``0700.HK``) are
extracted alongside the taxonomy keywords (:func:`stock_codes`) and merged
into the token list by first occurrence — a question naming a code finds the
articles that print it (the items search ILIKEs each token against
title/content). Each code normalizes to its bare digit form (the form the
corpus prints), HK zero-padded to 5 digits.

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
from typing import List, Tuple

from builds.text.keywords import KeywordTaxonomy

# Stdout marker prefixing the JSON result — mirrors the zhihu downloader's
# SEARCH_RESULT_MARKER convention (defensive: keep stdout parseable even if
# something in the import chain prints).
TOKENIZE_RESULT_MARKER = "@@NEWS_TOKENIZE_JSON@@"

DEFAULT_LIMIT = 8

# ---------------------------------------------------------------------------
# Stock code extraction — A-share / BSE / index codes and their
# exchange-prefixed forms, plus HK dotted form. Bare 6-digit codes are
# PREFIX-GATED (only the established stock/index families) so amounts and
# dates don't match; the digit-run guards keep a code from matching inside
# an 8-digit date or a longer number. Every match normalizes to the bare
# digit form the corpus prints (SH600519 / 600519.SH -> 600519; HK
# zero-padded to 5 digits: 0700.HK -> 00700).
# ---------------------------------------------------------------------------
_STOCK_CODE_FAMILIES = (
    "600|601|603|605"      # SH main board
    "|688|689"             # STAR market
    "|000|001|002|003"     # SZ main board / SME
    "|300|301|302"         # ChiNext
    "|399"                 # SZ indexes
    "|93"                  # CSI indexes (930xxx..93xxxx)
    "|43|83|87|920"        # BSE / NEEQ
    "|899"                 # BSE indexes
)
_STOCK_CODE_RE = re.compile(
    r"(?<![0-9A-Za-z])(?:"
    r"(?:SH|SZ|BJ)(\d{6})"                 # prefix form: SH600519 / sz000001
    r"|(\d{6})\.(?:SH|SZ|BJ|CSI)"          # suffix form: 600519.SH / 931743.CSI
    r"|(\d{4,5})\.HK"                      # HK: 0700.HK / 00700.HK
    rf"|((?:{_STOCK_CODE_FAMILIES})\d{{3}})"  # bare 6-digit code families
    r")(?![0-9])",
    re.IGNORECASE,
)


def stock_codes(text: str) -> "List[Tuple[str, int]]":
    """Extract stock/index codes from *text* with their first positions.

    Returns (code, position) pairs — the code normalized to its bare digit
    form (HK zero-padded to 5 digits) — ordered by first occurrence. Bare
    codes are prefix-gated to the established stock/index families; an
    exchange prefix (SH600519) or suffix (600519.SH) matches regardless of
    the family gate.
    """
    out: "List[Tuple[str, int]]" = []
    seen: set = set()
    for m in _STOCK_CODE_RE.finditer(text):
        group = next(
            (i for i in range(1, 5) if m.group(i) is not None), None)
        if group is None:
            continue
        digits = m.group(group)
        if group == 3:  # HK dotted form — pad to 5 digits
            digits = digits.zfill(5)
        if digits not in seen:
            seen.add(digits)
            out.append((digits, m.start(group)))
    return out


def tokenize(text: str, *, limit: int = DEFAULT_LIMIT) -> List[str]:
    """Split *text* naively into taxonomy keywords + stock codes.

    Taxonomy keywords are most-specific kept; stock codes (see
    :func:`stock_codes`) ride alongside them. Returns at most *limit*
    tokens ordered by first occurrence in *text*.
    """
    if not text or not text.strip():
        return []
    taxonomy = KeywordTaxonomy()
    matched = list(taxonomy.match(text)[0].keys())

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

    # Taxonomy keywords + stock codes in one first-occurrence order (a code
    # is never a substring of a taxonomy keyword, so the containment drop
    # above can't have eaten one).
    entries: "List[Tuple[int, int, str]]" = [
        (first_pos(kw), -len(kw), kw) for kw in kept]
    entries.extend((pos, -len(code), code) for code, pos in stock_codes(text))
    entries.sort()
    return [t for _pos, _len, t in entries][:limit]


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
        # altchars: the API route sends the URL-SAFE alphabet (the cluster
        # arg charset forbids '/') — decode both alphabets.
        text = base64.b64decode(args.text_b64, altchars=b"-_").decode("utf-8")

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
