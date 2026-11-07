"""llm_agents.deep_agents.tools.base — the tool-object contract.

The tool LAYER of the harness: every capability the model calls (beside
the external MCP tools) is an OBJECT here — a :class:`Tool` subclass
carrying its own chat-completion schema, its ``run`` handler and its
DB-editable description — grouped into :class:`ToolKit` s the runner
mounts per conversation. The former shape (plain ``BuiltinTool``
dataclass + handler closures scattered across core/planning modules)
remains the loop's runtime contract: ``Tool.builtin()`` renders a tool
object down to the :class:`BuiltinTool` pair the loop appends and
dispatches, so the loop itself stays dumb (schemas in, tool text out).

DESCRIPTION SOURCING — the ``llm.ai_prompts`` tool rows (prompt_type
4, seeded by ``database/sql/text/llm/static/ai_prompts/10_tool_prompts.sql``
from these classes' ``DEFAULT_DESCRIPTION`` constants): a kit
constructed with a ``descriptions`` map (the runner resolves the active
rows once per run, fail-soft) overrides each tool's description with
the DB row's instruction — the same fail-soft override contract as the
agent's internal prompts (a missing key or a DB outage falls back to
the built-in constant). Tool descriptions are therefore DB-editable
without a code change, and the seed regenerates from the constants.

A tool's ``db_key`` — ``(prompt_key, prompt_sub_type)`` of its
ai_prompts row — is the join key in both directions (the override
lookup flattens it to ``"prompt_key|prompt_sub_type"``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Optional, Tuple

from llm_agents.deep_agents.tools.data_definitions import (
    BuiltinTool,
)
# The loop's runtime shape — the dataclass LIVES here now (tools/ is the
# tool layer); core.tools re-exports it for its existing importers.
# Handler failures come back as the tool's result text (the loop must
# survive a broken tool — handlers never raise into the loop by contract,
# and the loop's own per-call catch is the backstop).




class Tool(ABC):
    """One model-callable capability as an object.

    Subclasses own four things: the tool ``name`` (the model's call
    name), the ``DEFAULT_DESCRIPTION`` (the schema text — also the seed
    source for the tool's ai_prompts row), the ``parameters`` JSON
    schema, and :meth:`run` — the async handler for the model's parsed
    arguments, returning the result TEXT (never raising into the loop;
    failures come back as error text the model can react to).

    ``db_key`` names the tool's ``llm.ai_prompts`` row (prompt_type 4);
    a kit constructed with a descriptions map overrides the built-in
    description with the active row's instruction. ``prompt_addendum``
    (default empty) is teaching text a MOUNTING agent appends to its
    system prompt when the tool rides along (the sandbox kit's usage
    contract is the example).
    """

    # The model's call name and the schema's parameters — class
    # constants on each concrete tool.
    name: ClassVar[str] = ""
    parameters: ClassVar[Dict[str, Any]] = {}
    # The ai_prompts row (prompt_type 4) this tool's description is
    # seeded from / overridden by: (prompt_key, prompt_sub_type).
    db_key: ClassVar[Tuple[str, str]] = ("", "")
    # The built-in schema description — the seed source; a DB row's
    # instruction overrides it per run (fail-soft to this).
    DEFAULT_DESCRIPTION: ClassVar[str] = ""
    # Teaching text appended to a mounting agent's system prompt.
    prompt_addendum: ClassVar[str] = ""

    def __init__(self, *, description: Optional[str] = None) -> None:
        self._description = (description or "").strip() \
            or self.DEFAULT_DESCRIPTION

    @property
    def description(self) -> str:
        """The effective schema description: the DB row's instruction
        when the kit resolved one, else the built-in constant."""
        return self._description

    @property
    def schema(self) -> Dict[str, Any]:
        """The OpenAI-style chat-completion tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def builtin(self) -> BuiltinTool:
        """The loop's runtime pair for this tool (``core.loop`` mounts
        and dispatches these)."""
        return BuiltinTool(schema=self.schema, handler=self.run)

    def describe(self) -> str:
        """The catalog paragraph: the tool's db_key and its effective
        description (what the seed generator and ``--list-tools``
        render)."""
        key = f"{self.db_key[0]}|{self.db_key[1]}" if any(self.db_key) \
            else self.name
        return f"{self.name} [{key}]: {self.description}"

    @abstractmethod
    async def run(self, arguments: Dict[str, Any]) -> str:
        """Handle one parsed model call; return the result text."""


class ToolKit(ABC):
    """A mountable GROUP of tools — one per conversation role.

    A kit owns its member tool objects (constructed with the kit's
    descriptions map applied), renders the ``builtin_tools`` dict the
    loop consumes, and carries the kit-level system-prompt addendum.
    The runner mounts the sandbox kit + the main plan kit on every
    inline step conversation; the delegation hooks stamp the fork kit
    on every spawned child's request.
    """

    def __init__(self,
                 descriptions: Optional[Dict[str, str]] = None) -> None:
        # The run-start resolution of the ai_prompts tool rows
        # (prompt_type 4), flattened to "prompt_key|prompt_sub_type" ->
        # instruction (plain str keys, checkpoint-serializable). None or
        # empty = every tool keeps its built-in description.
        self._descriptions = descriptions or None

    def db_description(self, db_key: Tuple[str, str]) -> Optional[str]:
        """The active DB instruction for *db_key*, or None (the tool
        keeps its built-in constant)."""
        if not self._descriptions:
            return None
        return self._descriptions.get(f"{db_key[0]}|{db_key[1]}") or None

    @abstractmethod
    def tools(self) -> List[Tool]:
        """The kit's member tool objects, in mount order."""

    def builtin_tools(self) -> Dict[str, BuiltinTool]:
        """The loop's ``builtin_tools`` shape for the whole kit."""
        return {tool.name: tool.builtin() for tool in self.tools()}

    def prompt_addendum(self) -> str:
        """Teaching text for a mounting agent's system prompt ('' when
        the kit needs none)."""
        return ""

    def describe(self) -> str:
        """The whole kit's catalog (one describe() line per tool)."""
        return "\n".join(tool.describe() for tool in self.tools())
