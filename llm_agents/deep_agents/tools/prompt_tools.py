"""llm_agents.deep_agents.tools.prompt_tools — the DYNAMIC rung of the
gradual prompt-loading ladder as a model-callable tool.

The ladder (``planning.prompt_loading``) runs at the plan node (catalog
→ keyword match → verdict); this module makes its LAST rung live for
the WHOLE graph: :class:`LoadPromptTool` lets ANY conversation — an
inline step hop, a forked subagent, a sub-sub child, the degraded
execute lane — pull ONE cataloged skill's full instruction the moment
its work touches that skill's domain, instead of hand-writing SQL
against ``llm.ai_prompts`` (or worse, fetching every skill wholesale).

Structural bounds, per the ladder's contract:

  * CATALOG-GATED — the tool serves only keys in the run's PROMPT
    CATALOG (the not-yet-loaded rows; the loaded ones already ride the
    conversation's message). A key outside the catalog comes back as
    error text naming the available keys — the model cannot pull
    arbitrary registry rows the user never picked;
  * CACHED + TEMPLATE-RENDERED — the runner hands one loader callable
    closed over a per-run cache (one fetch per key per run) and the
    run's ``{{key}}`` template args, so a loaded text renders exactly
    like the plan-time loaded ones;
  * fail-soft — a fetch failure is tool error text, never a crash
    (``tools.base``'s never-raise contract).

Keyword matching stays the higher-priority path: rows the runner
already keyword-matched for a node (bound/optional labels, or a key
NAMED in the step's directive — ``prompt_loading.node_prompt_matches``)
load without the model asking; this tool is the discretionary rung for
everything the code could not see.
"""
from __future__ import annotations

from typing import Awaitable, Callable, ClassVar, Dict, List, Optional, Tuple

from llm_agents.deep_agents.tools.base import Tool, ToolKit


class LoadPromptTool(Tool):
    """``load_prompt`` — load ONE cataloged skill's instruction text.

    The runner mounts it beside the plan/stats kits on every
    conversation; the catalog (prompt_key -> preview line) and the
    cached loader close over the run."""

    name: ClassVar[str] = "load_prompt"
    db_key: ClassVar[Tuple[str, str]] = ("load_prompt", "prompts")
    parameters: ClassVar[Dict[str, object]] = {
        "type": "object",
        "properties": {
            "prompt_key": {
                "type": "string",
                "description": "The cataloged skill's prompt_key (one "
                               "of the [Prompt catalog] block's keys).",
            },
        },
        "required": ["prompt_key"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Load ONE cataloged skill's full instruction text by prompt_key "
        "(a key from the [Prompt catalog] block) — call it the moment "
        "YOUR work enters that skill's domain, then apply the skill to "
        "the rest of your work. One key per call; never fetch skills "
        "wholesale."
    )

    def __init__(
        self,
        catalog: Dict[str, str],
        loader: Callable[[str], Awaitable[Optional[str]]],
        *,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(description=description)
        self._catalog = catalog
        self._loader = loader

    async def run(self, arguments: Dict[str, object]) -> str:
        key = str(arguments.get("prompt_key") or "").strip()
        if not key:
            return "load_prompt error: pass the skill's prompt_key " \
                   "(one of the [Prompt catalog] block's keys)"
        if key not in self._catalog:
            available = ", ".join(sorted(self._catalog)[:12]) or "(none)"
            return (f"load_prompt error: '{key}' is not in this run's "
                    f"prompt catalog — available keys: {available}")
        try:
            text = await self._loader(key)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"load_prompt error: fetch failed: {type(exc).__name__}: {exc}"
        if not text:
            return (f"load_prompt error: '{key}' resolved to no active "
                    "row — it may have been deactivated")
        preview = self._catalog.get(key) or ""
        return (f"[{key} — {preview}]\n{text}\n(loaded for THIS "
                "conversation; honor it for the rest of your work)")


class PromptLoadToolKit(ToolKit):
    """The dynamic-loading kit the runner mounts on EVERY conversation
    beside its role kit (main plan / fork plan / stats / none): the
    catalog-gated ``load_prompt`` tool over the run's shared cache.
    An empty catalog mounts nothing (no menu, no tool)."""

    def __init__(
        self,
        catalog: Dict[str, str],
        loader: Callable[[str], Awaitable[Optional[str]]],
        *,
        descriptions: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(descriptions)
        self._catalog = catalog
        self._loader = loader

    def tools(self) -> List[Tool]:
        if not self._catalog:
            return []
        return [LoadPromptTool(
            self._catalog, self._loader,
            description=self.db_description(LoadPromptTool.db_key))]
