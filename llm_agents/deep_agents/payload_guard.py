"""llm_agents.deep_agents.payload_guard — the LARGE-PAYLOAD PRE-CHECK
every LLM request passes through, now a THIN ADAPTER over the shared
``llm_agents._core.preview`` envelope (2026-11-12: the metadata +
preview template migrated OUT of deep_agents into _core so every
llm_agents lane — not only the harness — can ship envelopes; the
former ``_guarded`` size-only stubs are REPLACED by the ``_preview``
envelope, which carries shape, per-column stats, head/mid/tail
batches and a how-to-read instruction instead of just a head taste).

The adapter keeps the standing API (the three message builders and the
loop seam call these; nothing else changed):

  * :func:`guard_payload` — the dict/list-aware walk of a structured
    payload; oversized subtrees are replaced IN PLACE at their
    shallowest oversized node by a ``_preview`` envelope (data-shaped
    subtrees get the full data branch: structure, column stats,
    head/mid/tail batches; everything else the text branch), oversized
    strings are head-truncated, and a note line is recorded per
    elision. Two tiers unchanged: any node over
    ``payload_entry_max_chars`` guards itself (children first); then,
    while the whole payload still exceeds ``payload_guard_chars``, its
    largest remaining top-level entries are guarded down to the gate.
  * :func:`guard_notes_block` — the notes rendered as the user-message
    block that tells the model what was elided and HOW TO FETCH it.
  * :func:`guard_text` — the head+tail string cap for tool-result
    texts (the loop seam), marker updated to the ``[preview: ...]``
    vocabulary.

Classification (sentiment / domain / industries) is NOT done here —
``_core`` knows nothing of the jev decision lane; the loop seam fires
the engine's content reads whose results stay OFF the envelope
(2026-11-13: jev decisions are excluded from LLM context — each read
records itself as a decision step in ``llm.deep_agent_aux_steps``).
Thresholds stay RUN LIMITS (``limits.run_limits()``), read per call;
fail-soft everywhere — a guard problem never blocks a request, the
ORIGINAL payload ships.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from llm_agents._core.preview import (
    PREVIEW_KEY, PreviewBuilder, PreviewEnvelope,
)
from llm_agents.deep_agents.limits import RunLimits, run_limits

__all__ = [
    "guard_payload",
    "guard_notes_block",
    "guard_text",
    "PREVIEW_KEY",
    "PreviewBuilder",
    "PreviewEnvelope",
]

# Walk depth cap: below this, an oversized container is guarded
# wholesale where it stands instead of descending further.
_MAX_DEPTH = 5


def _builder(limits: RunLimits) -> PreviewBuilder:
    """The limits-frozen builder for one guard pass."""
    return PreviewBuilder(
        preview_batch_rows=limits.preview_batch_rows,
        preview_chars=limits.payload_preview_chars,
        stats_sample_rows=limits.preview_stats_sample_rows)


def _guard_node(
    node: Any,
    path: str,
    depth: int,
    limits: RunLimits,
    notes: List[str],
) -> Any:
    """One node of the walk — children first, wholesale as the
    fallback (the two-tier rule, per node)."""
    entry_max = limits.payload_entry_max_chars
    builder = _builder(limits)
    if _size(node) <= entry_max:
        return node
    if isinstance(node, str):
        keep = limits.payload_preview_chars
        notes.append(f"{path} — text ~{len(node):,} chars — head/mid/"
                     f"tail slices kept ({keep:,}-char budget)")
        envelope = builder.build_text("context", node)
        return envelope.render()
    if not isinstance(node, (dict, list)):
        text = json.dumps(node, ensure_ascii=False, default=str)
        notes.append(f"{path} — {type(node).__name__} ~{len(text):,} chars")
        return builder.build_text("context", text).render()
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
        # The ROOT node is never wholesale-replaced (guard_payload's
        # contract): its children-first result stays navigable and the
        # second tier trims the largest entries down to the gate.
        if not path:
            return guarded
    envelope = builder.build("context", node)
    notes.append(f"{path} — {envelope.note}")
    return envelope.render()


def _size(node: Any) -> int:
    """The node's serialized size in chars (the thresholds' number)."""
    try:
        return len(json.dumps(node, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(node))


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
        # Second tier: trim the largest remaining top-level entries
        # (already-guarded entries are exempt) until the gate fits.
        gate = limits.payload_guard_chars
        while isinstance(guarded, dict) and _size(guarded) > gate:
            sizes = {k: _size(v) for k, v in guarded.items()
                     if not (isinstance(v, dict) and PREVIEW_KEY in v)}
            if sizes:
                biggest = max(sizes, key=lambda k: sizes[k])
                envelope = _builder(limits).build("context",
                                                  guarded[biggest])
                guarded[biggest] = envelope.render()
                notes.append(f"{biggest} — {envelope.note}")
                continue
            # Everything is already an envelope but the whole payload
            # still exceeds the gate — shrink the FATTEST envelope to
            # its head batch only (metadata stays, the mid/tail taste
            # is the expendable half).
            fat = max(
                (k for k, v in guarded.items()
                 if isinstance(v, dict) and PREVIEW_KEY in v),
                key=lambda k: _size(guarded[k]), default=None)
            if fat is None:
                break
            body = dict(guarded[fat][PREVIEW_KEY])
            preview = body.get("preview")
            if isinstance(preview, dict) and (
                    "mid" in preview or "tail" in preview):
                body["preview"] = {"head": preview.get("head", [])}
            elif preview is not None:
                del body["preview"]  # last resort: metadata only
            else:
                break  # already metadata-only — no progress possible
            guarded[fat] = {PREVIEW_KEY: body}
        return guarded, notes
    except Exception:  # fail-soft: ship the original payload
        return data, []


def guard_notes_block(notes: List[str]) -> str:
    """The notes as the user-message block: WHICH fields ship as
    envelopes only, and the standing instruction — read the metadata,
    decide what matters, fetch the substance through your tools."""
    if not notes:
        return ""
    return ("[Large data as metadata + preview — the real content is "
            "NOT in this message]\n"
            "These context fields exceeded the message's size budget "
            "and ship as _preview envelopes (shape, stats, head/mid/"
            "tail batches, how to read):\n"
            + "\n".join(f"- {n}" for n in notes)
            + "\nAn envelope proves SHAPE, not substance — and you "
              "never read the full data into this conversation: when "
              "your work touches any of these, follow its how_to_read "
              "— WRITE A SANDBOX SCRIPT that filters on the key "
              "columns, aggregates/derives, and prints only the small "
              "computed result (db_run_query stays for narrow "
              "metadata-sized lookups; text_search for the corpus); "
              "check the computed numbers before concluding — never "
              "invent the elided content.")


def guard_text(text: str, *, max_chars: Optional[int] = None) -> str:
    """The tool-result pre-check for UNSTRUCTURED text: one output
    string, measured before it enters the transcript — oversized ones
    keep the head and the tail with a marker naming what was elided
    (structured results take the ``_preview`` envelope at the loop
    seam instead; this is the fallback lane)."""
    if max_chars is None:
        max_chars = run_limits().tool_result_max_chars
    if len(text) <= max_chars:
        return text
    marker = ("\n…[preview: {:,} chars total — {:,} middle chars "
              "elided; re-run the tool with a narrower query/filter "
              "if you need the middle]…")
    body = max(0, max_chars - len(marker.format(0, 0)))
    head = body // 2
    tail = body - head
    elided = max(0, len(text) - head - tail)
    return (text[:head] + marker.format(len(text), elided)
            + (text[-tail:] if tail > 0 else ""))
