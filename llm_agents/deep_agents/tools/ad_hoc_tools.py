"""llm_agents.deep_agents.tools.ad_hoc_tools — the model-facing face
of the independent LLM request (``ad_hoc_llm_request`` module at the
package root): :class:`AdHocRequestTool` lets a conversation fire ONE
fresh-context request instead of working a sub-question through its
own history.

WHY A TOOL: a conversation's every turn re-sends its accumulated
context; some side questions — classify this one list, summarize this
one document, translate one passage, judge one wording — need none of
that context and would only pay for it. This tool hands the model the
cheap lane: its ``prompt`` argument becomes a standalone TWO-message
request (the tool closes over the run's ``chat_complete`` + model);
nothing of THIS conversation rides along, the answer comes back as the
tool result text. The token contract is explicit in the description:
put everything the sub-question needs INTO the prompt, keep the answer
compact — the request carries no context and the result should stay
small enough to be worth one.

Fail-soft per the ``tools.base`` never-raise contract: a failed
request is error text the model can react to (retry with a tighter
prompt, or do the work in-conversation instead), never a crash. The
requests log as their OWN one-row chains (the module's chain
isolation) — honest tokens, no cache-rollup pollution.
"""
from __future__ import annotations

from typing import Awaitable, Callable, ClassVar, Dict, List, Optional, Tuple

from llm_agents.deep_agents.ad_hoc_llm_request import (
    AdHocLlmRequest, AdHocRequestError,
)
from llm_agents.deep_agents.tools.base import Tool, ToolKit


class AdHocRequestTool(Tool):
    """``ad_hoc_llm_request`` — one independent fresh-context LLM
    request; the answer rides back as this tool's result text."""

    name: ClassVar[str] = "ad_hoc_llm_request"
    db_key: ClassVar[Tuple[str, str]] = ("ad_hoc_llm_request", "ad_hoc")
    parameters: ClassVar[Dict[str, object]] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The COMPLETE standalone request — the "
                               "sub-question plus every fact, number "
                               "or text fragment it needs (nothing of "
                               "your conversation is carried).",
            },
            "system": {
                "type": "string",
                "description": "Optional one-line role/framing for the "
                               "request (e.g. 'Reply in JSON: {verdict,"
                               " reason}'). Omit for a plain answer.",
            },
        },
        "required": ["prompt"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Make ONE independent LLM request over a FRESH minimal context — "
        "for a self-contained side question that does not need your "
        "conversation (classify this list, summarize this one document, "
        "reformat this passage). NOTHING of your context is carried: put "
        "everything the request needs INTO prompt (quote the facts or "
        "text verbatim); keep the ask narrow and the expected answer "
        "compact. SPLIT RULE: whenever a sub-task does not need your "
        "carried context, split it OUT of your own work and send it "
        "here as a fresh-context request instead of doing it inline — "
        "each such request carries only its own small prompt, so this "
        "conversation stays lean; several independent sub-tasks become "
        "several separate requests. Do NOT use it for anything needing "
        "your tools, the run's data, or multi-turn back-and-forth."
    )

    def __init__(
        self,
        chat_complete: Callable[..., Awaitable[Dict[str, object]]],
        *,
        model: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(description=description)
        self._asker = AdHocLlmRequest(chat_complete, model=model)

    async def run(self, arguments: Dict[str, object]) -> str:
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            return ("ad_hoc_llm_request error: pass the complete "
                    "standalone prompt — nothing of this conversation "
                    "is carried for you")
        system = str(arguments.get("system") or "").strip() or None
        try:
            reply = await self._asker.ask(prompt, system=system)
        except AdHocRequestError as exc:
            return f"ad_hoc_llm_request error: {exc}"
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return (f"ad_hoc_llm_request error: request failed: "
                    f"{type(exc).__name__}: {exc}")
        note = " (truncated to the cap)" if reply.truncated else ""
        return f"{reply.text}{note}"


class AdHocRequestToolKit(ToolKit):
    """The independent-request kit the runner mounts beside the
    prompt/stats/kg kits on every conversation that carries
    ``load_prompt`` — one tool, the run's chat completion closed over."""

    def __init__(
        self,
        chat_complete: Callable[..., Awaitable[Dict[str, object]]],
        *,
        model: Optional[str] = None,
        descriptions: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(descriptions)
        self._chat_complete = chat_complete
        self._model = model

    def tools(self) -> List[Tool]:
        return [AdHocRequestTool(
            self._chat_complete, model=self._model,
            description=self.db_description(
                AdHocRequestTool.db_key))]
