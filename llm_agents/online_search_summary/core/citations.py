"""llm_agents.online_search_summary.core.citations — Reference/citation format.

The refer/citation/date helpers shared by every provider and by the
persistence layer. Nothing here touches the network or the DB.

Reference / citation format (studied from the ZhiPu docs,
docs.bigmodel.cn/cn/guide/tools/web-search):

  * Every search result carries a ``refer`` tag addressing it. ZhiPu emits
    ``"ref_1"``-style tags in chat responses and bare index strings
    (``"1"``) in standalone-search responses — ``normalize_refer`` maps
    both onto the canonical ``ref_N`` form (position-derived fallback when
    the field is absent/garbage).
  * Summary answers embed citation markers built from those tags:
    ``[ref_2]``, ``[来源：ref_1]``, date-suffixed ``[来源：ref_1,
    2026-09-11]`` and multi-ref brackets ``[来源：ref_1，2026-09；
    来源：ref_2，2026-09-10]`` (fullwidth ，/；, per-model variance);
    ``extract_cited_refs`` recovers the tags in citation order so stored
    context can be limited to the actually-cited references.
  * ``format_references`` renders hits back into the reference list stored
    as text.llm_qa.context (and embedded in compose-flow prompts).
"""
from __future__ import annotations

import datetime
import re
from typing import Any, List, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from llm_agents.online_search_summary.core.models import SearchHit

# Inline citation markers in summary answers. Observed forms (glm-4-air
# per the docs, glm-5.x in practice): "[ref_2]", "[来源：ref_1]",
# "[来源：ref_1, 2026-09-11]" (date suffix), and multi-ref brackets
# "[来源：ref_1，2026-09；来源：ref_2，2026-09-10]" (fullwidth ，/；).
# Parsing is therefore two-stage: scan bracketed spans, then collect every
# ref_N tag inside — plain-text ref_N outside brackets is ignored.
_BRACKET_SPAN_RE = re.compile(r"\[([^\[\]]*)\]")
REF_TAG_RE = re.compile(r"ref_(\d+)")

_DATE_IN_TEXT_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def normalize_refer(value: Any, position: int) -> str:
    """Canonical refer tag ``ref_N`` for a search-result item.

    ZhiPu chat responses tag results ``ref_1`` while standalone-search
    responses may use the bare index string ``"1"``; both map onto
    ``ref_N``. Items without a usable refer fall back to their 1-based
    position so every hit stays addressable by citation markers.
    """
    m = re.search(r"(\d+)", str(value or ""))
    n = int(m.group(1)) if m else max(position, 1)
    return f"ref_{n}"


def parse_publish_date(value: Any) -> Optional[datetime.date]:
    """Parse 'YYYY-MM-DD' (embedded in longer text) -> date, or None."""
    if not value:
        return None
    m = _DATE_IN_TEXT_RE.search(str(value))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def extract_cited_refs(text: str) -> List[str]:
    """Refer tags cited in *text*, in citation order, deduplicated.

    Handles every observed citation form: ``[ref_2]``, ``[来源：ref_1]``,
    ``[来源：ref_1, 2026-09-11]`` and multi-ref brackets like
    ``[来源：ref_1，2026-09；来源：ref_2，2026-09-10]``.
    """
    seen: set = set()
    ordered: List[str] = []
    for span in _BRACKET_SPAN_RE.finditer(text or ""):
        for tag in REF_TAG_RE.finditer(span.group(1)):
            ref = f"ref_{tag.group(1)}"
            if ref not in seen:
                seen.add(ref)
                ordered.append(ref)
    return ordered


def format_references(
    hits: Sequence["SearchHit"],
    *,
    only_refs: Optional[Sequence[str]] = None,
) -> str:
    """Render hits as the reference list stored in text.llm_qa.context.

    One block per hit, ``[ref_N] title — media · date`` header + link +
    snippet. With *only_refs* (e.g. ``summary.cited_refs``) just those tags
    are rendered, in hit order.
    """
    wanted = set(only_refs) if only_refs is not None else None
    blocks: List[str] = []
    for h in hits:
        if wanted is not None and h.refer not in wanted:
            continue
        head = f"[{h.refer}] {h.title}"
        meta = " · ".join(str(x) for x in (h.media, h.publish_date_raw
                                           or (h.publish_date.isoformat()
                                               if h.publish_date else None))
                          if x)
        if meta:
            head = f"{head} — {meta}"
        lines = [head]
        if h.link:
            lines.append(h.link)
        if h.content:
            lines.append(h.content)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
