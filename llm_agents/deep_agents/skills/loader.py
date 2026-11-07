"""llm_agents.deep_agents.skills.loader — fetching the run's selected
prompts into a SkillBook.

Fail-soft at the caller — prompt sourcing never fails a run, the same
contract as the system/agent prompt resolution. Two fetch shapes over
``planning.store``'s accessors: the KEY form (the ACTIVE rows among
the request's prompt_keys — today the PLAN-type rows, prompt_type 2,
the generation-plan prompts) and the ROWS form
    (:meth:`SkillLoader.from_rows`) — the GRADUAL LOADING LADDER's
post-verdict fetch (``planning.prompt_loading``: catalog previews →
keyword match → the planner's ``load_prompts`` verdict → the decided
set's full instructions), which loads SKILLS rows (prompt_type 1)
beside the plan rows. The SKILL-type rows stay agent-fetched JIT
beyond the loaded set: the unloaded ones ride the run as one preview
line each (the prompt catalog), fetched per-step by key when a step
works that skill's domain.
"""
from __future__ import annotations

from typing import Iterable, List

from _common.labels import PROMPT_TYPE_CODE

from llm_agents.deep_agents.planning.store import (PromptRow, PLAN_STORE)
from llm_agents.deep_agents.skills.base import Skill, SkillBook

# The label-code mirror's prompt_type domain (_common.labels — the
# Python mirror of static.label_dim): the types this layer names.
PROMPT_TYPE_SKILLS: int = PROMPT_TYPE_CODE["skills"]
PROMPT_TYPE_PLAN: int = PROMPT_TYPE_CODE["plan"]


class SkillLoader:
    """The run's selected prompts -> :class:`SkillBook`.

    *prompt_type* scopes the fetch (default: the plan prompts the
    runner distributes); keys not matching the type (or inactive) come
    back absent — the book simply holds what resolved.

    The GRADUAL LOADING LADDER (``planning.prompt_loading``) lands its
    post-verdict fetch here too: once the load set is decided, the
    runner fetches those rows' full instructions
    (:meth:`PLAN_STORE.fetch_prompt_instructions`) and builds the book with
    :meth:`from_rows` — the skills layer stays the pass-down owner
    whatever decided the set."""

    def __init__(self, prompt_type: int = PROMPT_TYPE_PLAN) -> None:
        self._prompt_type = prompt_type

    async def load(self, prompt_keys: List[str]) -> SkillBook:
        rows = await PLAN_STORE.fetch_plan_prompts(prompt_keys)
        return SkillBook([
            Skill(row.prompt_key, row.instruction, version=row.version)
            for row in rows
        ])

    @staticmethod
    def from_rows(rows: Iterable[PromptRow]) -> SkillBook:
        """The book over ALREADY-FETCHED rows (the ladder's on-demand
        fetch of the decided load set — skills and plan rows alike,
        the caller owning which)."""
        return SkillBook([
            Skill(row.prompt_key, row.instruction, version=row.version)
            for row in rows
        ])


async def fetch_skill_book(prompt_keys: List[str]) -> SkillBook:
    """The module-level convenience: the run's selected plan prompts
    as a SkillBook (an empty book when no keys resolve)."""
    return await SkillLoader().load(prompt_keys)

__all__ = [
    "PROMPT_TYPE_PLAN", "PROMPT_TYPE_SKILLS", "SkillLoader",
    "fetch_skill_book",
]
