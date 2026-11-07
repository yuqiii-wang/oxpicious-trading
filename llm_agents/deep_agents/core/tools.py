"""llm_agents.deep_agents.core.tools — the user-message assembly every
conversation shares, plus the builtin-tool type re-export.

The TOOL OBJECTS live in the tool layer now (``deep_agents.tools`` —
:class:`~llm_agents.deep_agents.tools.base.Tool` / ``ToolKit``; the
sandbox, plan, thread-search and delegation tool classes); this module
keeps the two pieces the loop and its callers have always imported from
here:

  * ``BuiltinTool`` — the loop's runtime pair (schema + async handler),
    defined in ``tools.base`` and re-exported under its historical
    import path; ``Tool.builtin()`` renders a tool object down to one.
  * ``user_content`` — the goal-plus-structured-context user message.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from llm_agents.deep_agents.payload_guard import (
    guard_notes_block, guard_payload,
)
from llm_agents.deep_agents.tools.base import BuiltinTool

__all__ = ["BuiltinTool", "user_content"]


def user_content(goal: str, data: Optional[Dict[str, Any]] = None) -> str:
    """The user message: the goal, plus *data* (whatever JSON object the
    caller wants the run to see — analysis items, skills, …) appended as
    a fenced structured-context block. The LARGE-PAYLOAD PRE-CHECK
    (``payload_guard.guard_payload``) runs first: oversized arrays /
    dicts ride as metadata + preview instead of full content, and the
    notes block after the fence prompts the model which data needs
    re-fetching and checking. Under the guard's thresholds the message
    renders exactly as before."""
    if not data:
        return goal
    guarded, notes = guard_payload(data)
    parts = [f"{goal}\n\n[Structured context — JSON]\n"
             f"```json\n{json.dumps(guarded, ensure_ascii=False, indent=2)}\n```"]
    block = guard_notes_block(notes)
    if block:
        parts.append(block)
    return "\n\n".join(parts)
