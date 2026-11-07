"""llm_agents.deep_agents.payload_guard — the LARGE-PAYLOAD PRE-CHECK
every LLM request passes through: arrays and dicts of data (the
structured context, tool results) are measured BEFORE they are
serialized into a request, and anything oversized ships as METADATA +
PREVIEW instead of the full payload, with a prompt block telling the
model WHICH data was elided so it can re-fetch and check the real
thing through its tools.

Why a guard: the structured-context ``data`` dict is rebuilt per run
from DB rows (analysis items with their db_keywords recipes, insight
docs with their FULL authored bodies, docs, scopes) and rode every
message unbounded — the measured driver was an all-scopes run whose
first user message carried a 76 KB data block, re-sent at EVERY step
hop (context rot: the model pays for bytes its step never reads). The
same shape can arrive from the tool lane — a ``db_run_query`` result
is bounded to 500 rows but not to bytes, and a wide 500-row dump rides
the whole hop's transcript. The guard implements the harness's
standing principle (artifacts in files, references in context; JIT
retrieval over pre-loading) at the request seam: keep the shape, keep
a taste of the content, name the path, and tell the model to fetch
the substance itself when its work needs it — the previews prove
SHAPE, not substance, and the model is told so.

The three entry points, all fail-soft (a guard problem never blocks a
request — the ORIGINAL payload ships):

  * :func:`guard_payload` — dict/list-aware walk of a structured
    payload; oversized subtrees are replaced IN PLACE at their
    shallowest oversized node by a ``_guarded`` metadata + preview
    object, oversized strings are head-truncated, and a note line is
    recorded per elision. Two tiers: any node over
    ``payload_entry_max_chars`` guards itself (children first, so a
    container whose children each shrink enough stays navigable);
    then, while the whole payload still exceeds
    ``payload_guard_chars``, its largest remaining top-level entries
    are guarded down to the gate.
  * :func:`guard_notes_block` — the notes rendered as the user-message
    block that prompts the model what data needs checking (message
    builders append it right after the JSON block).
  * :func:`guard_text` — head+tail string cap for tool-result texts
    (the loop applies it at the one seam every tool output crosses
    before entering the transcript), mirroring the sandbox runner's
    own head+tail marker discipline.

Thresholds are RUN LIMITS (``limits.run_limits()`` — the UI-editable
``deep_agent_config.json``), read per call like every other limit
read; a malformed file falls back to the built-in defaults. The
module imports only the stdlib and ``limits`` — the same
every-layer-may-import discipline as ``limits`` itself (core /
planning / graph all sit at request seams).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.deep_agents.limits import RunLimits, run_limits

__all__ = [
    "guard_payload",
    "guard_notes_block",
    "guard_text",
    "GUARD_KEY",
]

# The in-place marker a guarded subtree carries instead of its full
# content: {"_guarded": {kind, length, original_chars, ..., preview,
# omitted}}. Distinctive enough that the model reads it as a stub, not
# as data; small enough that re-guarding an already-guarded payload is
# a no-op (the marker never trips the size thresholds again).
GUARD_KEY = "_guarded"

# Walk depth cap: below this, an oversized container is guarded
# wholesale where it stands instead of descending further (paths like
# ``a.b[2].c`` are already deep enough to read; deeper descent has
# never paid in this house's data shapes).
_MAX_DEPTH = 5


def _size(node: Any) -> int:
    """The node's serialized size in chars — the number the thresholds
    compare. ``default=str`` keeps the measurement total for any
    payload a caller can build."""
    try:
        return len(json.dumps(node, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(node))


def _shrink(node: Any, max_chars: int) -> Any:
    """One preview element: small elements pass through unchanged;
    oversized ones collapse to a truncated JSON string — the taste of
    the content without the weight."""
    if _size(node) <= max_chars:
        return node
    text = json.dumps(node, ensure_ascii=False, default=str)
    return text[:max_chars].rstrip() + " …"


def _element_summary(elements: List[Any], max_keys: int = 8) -> str:
    """One line describing what an elided array holds — the metadata
    half of the deal (the model needs to know WHAT it is not seeing)."""
    if not elements:
        return "empty"
    first = elements[0]
    if isinstance(first, dict):
        keys = list(first.keys())
        shown = ", ".join(str(k) for k in keys[:max_keys])
        more = f" +{len(keys) - max_keys} more" if len(keys) > max_keys else ""
        suffix = " (uniform)" if all(
            isinstance(e, dict) and list(e.keys()) == keys
            for e in elements[1:]) else ""
        return f"objects with keys: {shown}{more}{suffix}"
    if isinstance(first, str):
        return "strings"
    return type(first).__name__ + "s"


def _wholesale(
    node: Any,
    path: str,
    preview_items: int,
    preview_chars: int,
) -> Tuple[Any, str]:
    """Replace one oversized container with its metadata + preview
    object; returns (replacement, note line)."""
    if isinstance(node, list):
        length = len(node)
        preview = [
            _shrink(e, preview_chars) for e in node[:preview_items]]
        meta: Dict[str, Any] = {
            "kind": "array",
            "length": length,
            "original_chars": _size(node),
            "elements": _element_summary(node[:1]),
            "preview": preview,
            "omitted": (
                f"{length - len(preview)} more elements not shown — "
                "fetch the real data through your tools when needed"),
        }
        note = (f"{path} — array of {length} elements, "
                f"~{meta['original_chars']:,} chars — metadata + preview "
                f"of first {len(preview)} kept")
    else:
        keys = list(node.keys())
        preview_keys = keys[:preview_items]
        preview = {k: _shrink(node[k], preview_chars)
                   for k in preview_keys}
        meta = {
            "kind": "object",
            "length": len(keys),
            "original_chars": _size(node),
            "keys": keys[:preview_items]
            + ([f"+{len(keys) - len(preview_keys)} more"]
               if len(keys) > len(preview_keys) else []),
            "preview": preview,
            "omitted": (
                f"{len(keys) - len(preview_keys)} more entries not "
                "shown — fetch the real data through your tools when "
                "needed"),
        }
        note = (f"{path} — object of {len(keys)} entries, "
                f"~{meta['original_chars']:,} chars — metadata + preview "
                f"of first {len(preview_keys)} kept")
    return {GUARD_KEY: meta}, note


def _guard_node(
    node: Any,
    path: str,
    depth: int,
    limits: RunLimits,
    notes: List[str],
) -> Any:
    """One node of the walk — children first, wholesale as the
    fallback (the module header's two-tier rule, per node)."""
    entry_max = limits.payload_entry_max_chars
    if _size(node) <= entry_max:
        return node
    if isinstance(node, str):
        keep = limits.payload_preview_chars
        notes.append(f"{path} — text ~{len(node):,} chars — first "
                     f"{keep:,} chars shown")
        return (node[:keep].rstrip()
                + f" …[guarded: {len(node) - keep:,} chars elided]")
    if not isinstance(node, (dict, list)):
        text = json.dumps(node, ensure_ascii=False, default=str)
        notes.append(f"{path} — {type(node).__name__} ~{len(text):,} chars")
        return text[:limits.payload_preview_chars].rstrip() + " …"
    if depth < _MAX_DEPTH:
        if isinstance(node, dict):
            guarded = {
                k: _guard_node(v, f"{path}.{k}" if path else str(k),
                               depth + 1, limits, notes)
                for k, v in node.items()
            }
        else:
            guarded = [
                _guard_node(v, f"{path}[{i}]" if path else f"[{i}]",
                            depth + 1, limits, notes)
                for i, v in enumerate(node)
            ]
        if _size(guarded) <= entry_max:
            return guarded
    replacement, note = _wholesale(
        node, path or "(root)", limits.payload_preview_items,
        limits.payload_preview_chars)
    notes.append(note)
    return replacement


def guard_payload(
    data: Any,
    *,
    limits: Optional[RunLimits] = None,
) -> Tuple[Any, List[str]]:
    """The pre-check: measure the payload, guard what is oversized,
    return (guarded_data, notes). ``notes`` is empty when nothing was
    elided — callers then render the payload exactly as before. The
    ROOT node is never wholesale-guarded (the walk descends into it
    and, as the second tier, trims its largest entries until the whole
    payload fits ``payload_guard_chars``); the guard never raises — an
    unexpected shape returns the input untouched."""
    if limits is None:
        limits = run_limits()
    if not isinstance(data, (dict, list)) or not data:
        return data, []
    try:
        notes: List[str] = []
        guarded = _guard_node(data, "", 0, limits, notes)
        # Second tier: the walk shrank every oversized node but the
        # payload can still exceed the total gate on many
        # medium entries — trim the largest remaining ones (already
        # guarded entries are exempt) down to the gate.
        gate = limits.payload_guard_chars
        while isinstance(guarded, dict) and _size(guarded) > gate:
            sizes = {k: _size(v) for k, v in guarded.items()
                     if not (isinstance(v, dict) and GUARD_KEY in v)}
            if not sizes:
                break
            biggest = max(sizes, key=lambda k: sizes[k])
            replacement, note = _wholesale(
                guarded[biggest], str(biggest),
                limits.payload_preview_items,
                limits.payload_preview_chars)
            guarded[biggest] = replacement
            notes.append(note)
        return guarded, notes
    except Exception:  # fail-soft: ship the original payload
        return data, []


def guard_notes_block(notes: List[str]) -> str:
    """The notes as the user-message block that prompts the model what
    data needs checking — WHICH fields were elided, and the standing
    instruction to re-fetch and check the real thing instead of
    answering from the previews. Empty string when nothing was
    elided."""
    if not notes:
        return ""
    return ("[Large data guarded — shown as metadata + preview only; "
            "do NOT answer from the previews]\n"
            "These context fields exceeded the message's size budget:\n"
            + "\n".join(f"- {n}" for n in notes)
            + "\nThe previews prove SHAPE, not substance: when your "
              "work touches any of these, fetch the real data through "
              "your tools (db_run_query in the sandbox over the item's "
              "db_keywords recipe, or the owning source table) and "
              "check it before concluding — never invent the elided "
              "content.")


def guard_text(text: str, *, max_chars: Optional[int] = None) -> str:
    """The tool-result pre-check: one output string, measured before it
    enters the transcript — oversized ones keep the head and the tail
    with a marker naming what was elided (the sandbox runner's own
    head+tail discipline, applied one level up at the loop seam)."""
    if max_chars is None:
        max_chars = run_limits().tool_result_max_chars
    if len(text) <= max_chars:
        return text
    marker = ("\n…[guarded: {:,} chars total — {:,} middle chars "
              "elided; re-run the tool with a narrower query/filter "
              "if you need the middle]…")
    body = max(0, max_chars - len(marker.format(0, 0)))
    head = body // 2
    tail = body - head
    elided = max(0, len(text) - head - tail)
    return (text[:head] + marker.format(len(text), elided)
            + (text[-tail:] if tail > 0 else ""))
