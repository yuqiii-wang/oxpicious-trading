"""llm_agents.deep_agents.supervisor.loop — the SUPERVISOR's standalone
mini tool-loop.

``core.loop.DeepAgent.run`` is the harness's only agentic loop, but it
is coupled to the MCP client and the subagent machinery — a standalone
one-shot agent (the planner, compaction, the supervisor) could not
carry tools. This module is the missing piece for ONE of them: a
BOUNDED loop over the plain ``chat_complete`` callable that dispatches
``BuiltinTool`` handlers (the same schema + handler pairs the
``ToolKit.builtin_tools()`` contract yields, so a kit mounts with zero
adaptation and its DB description overrides ride along exactly as the
delegation hooks stamp them on forks).

Bounds: ``SUPERVISOR_MAX_TOOL_ROUNDS`` tool rounds total — when the
model still asks for tools past the bound, the remaining calls are
answered with a "tool budget spent — answer now" tool result, so the
loop always terminates with a text reply. Every handler is already
never-raise by the ``Tool`` contract; the belt-wrap keeps a broken
handler rendering as the tool's result text regardless.

Pure module, stub-testable: takes the same duck-typed
``chat_complete`` the planner, compaction and the engine take.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from llm_agents.deep_agents.tools.base import BuiltinTool

logger = logging.getLogger("deep_agents.supervisor")

# The intervention's tool budget: the supervisor's diagnostic loop is
# BOUNDED — a couple of evidence checks, then it must judge.
SUPERVISOR_MAX_TOOL_ROUNDS = 3


def _tool_result_message(
        call: Dict[str, Any], content: str) -> Dict[str, Any]:
    """The assistant-tool_calls protocol's tool-role reply for ONE call."""
    return {"role": "tool",
            "tool_call_id": call.get("id"),
            "content": content}


class BoundedToolLoop:
    """The bounded tool loop as an object (2026-11-06 — the former
    :func:`supervisor_tool_loop`): the duck-typed ``chat_complete`` and
    the ``builtin_tools`` handlers are the object's fields,
    :meth:`run` drives one conversation through them. The same schema +
    handler pairs the ``ToolKit.builtin_tools()`` contract yields, so a
    kit mounts with zero adaptation and its DB description overrides
    ride along exactly as the delegation hooks stamp them on forks."""

    def __init__(
        self,
        chat_complete: Callable[..., Any],
        builtin_tools: Dict[str, BuiltinTool],
        *,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_rounds: int = SUPERVISOR_MAX_TOOL_ROUNDS,
    ) -> None:
        self._chat = chat_complete
        self._tools = builtin_tools
        self._model = model
        self._temperature = temperature
        self._max_rounds = max_rounds

    async def run(
        self, messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run the bounded tool loop: schemas out, tool_calls dispatched
        to the ``builtin_tools`` handlers, results appended, until a
        text-only reply (or the round bound). Returns the FINAL message
        dict — the engine caps and validates its content. Never raises
        on a handler failure (rendered as the tool's result text);
        provider errors propagate to the caller's fail-soft path like a
        one-shot call."""
        tools = [bt.schema for bt in self._tools.values()]
        conversation = list(messages)
        for _round in range(self._max_rounds + 1):
            message = await self._chat(
                conversation, model=self._model, tools=tools,
                temperature=self._temperature)
            calls = message.get("tool_calls") or []
            if not calls or _round == self._max_rounds:
                if calls:
                    # Budget spent mid-ask: answer every pending call with
                    # the stop notice so the protocol stays well-formed,
                    # then take one final no-tools reply.
                    logger.info("supervisor tool budget spent after %d "
                                "round(s)", self._max_rounds)
                    conversation.append(message)
                    for call in calls:
                        conversation.append(_tool_result_message(
                            call, "tool budget spent — answer now from "
                                  "what you have"))
                    message = await self._chat(
                        conversation, model=self._model, tools=None,
                        temperature=self._temperature)
                return message
            conversation.append(message)
            for call in calls:
                name = str(call.get("name") or "")
                try:
                    raw = call.get("arguments")
                    arguments = (json.loads(raw)
                                 if isinstance(raw, str) else (raw or {}))
                except (TypeError, ValueError):
                    arguments = {}
                handler = self._tools.get(name)
                if handler is None:
                    result = f"{name} error: no such tool"
                else:
                    try:
                        result = await handler.handler(arguments)
                    except Exception as exc:  # noqa: BLE001 — belt under
                        result = (f"{name} error: "
                                  f"{type(exc).__name__}: {exc}")
                conversation.append(_tool_result_message(call, result))
        # Unreachable (the loop returns on _round == self._max_rounds);
        # kept for the type checker's definite-return.
        raise AssertionError("supervisor tool loop fell through")
