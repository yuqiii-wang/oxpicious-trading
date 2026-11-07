"""llm_agents.deep_agents.planning.store.prompts — the AI_PROMPTS
ACCESSORS (the PromptStoreMixin) + the internal row KEYS.

Every prompt text the harness resolves at run start reads through
here, all over ``llm.ai_prompts`` (prompt_type 2 plan rows, 1+2 full
instruction fetches, 3 internal agent rows, 4 tool-description rows,
0 the standing system row) plus the KG-HOTPATH evidence read the
prompt loader's corroboration rung rides (2026-11-07).

The AGENT_PROMPT_* constants are the type-3 'internal' row keys, seeded
from the inline Python constants by
``database/sql/llm/static/ai_prompts/01_deep_agent_prompts.sql`` — the
DB-managed overrides the runner resolves per run. Each consumer falls
back to its built-in constant when its key is missing; the seed and
the constants must stay in sync (the drift checker
``temp_scripts/check_agent_prompts_sync.py`` asserts it live).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from llm_agents.deep_agents.planning.store.base import _StoreBase
from llm_agents.deep_agents.planning.store.models import (
    HotpathChainRow, PromptCatalogRow, PromptRow,
)

# The deep agent's INTERNAL prompt rows (prompt_type 3 'internal') —
# see this module's docstring for the seed/sync contract.
AGENT_PROMPT_PLAN_SYSTEM = "deep-agent-plan-system"
AGENT_PROMPT_STEP_ADDENDUM = "deep-agent-step-addendum"
AGENT_PROMPT_SUBAGENT_SYSTEM = "deep-agent-subagent-system"
AGENT_PROMPT_FORK_PLAN_ADDENDUM = "deep-agent-fork-plan-addendum"
AGENT_PROMPT_FORK_SPAWN_ADDENDUM = "deep-agent-fork-spawn-addendum"
AGENT_PROMPT_SUPERVISOR_SYSTEM = "deep-agent-supervisor-system"
AGENT_PROMPT_STANDING_SYSTEM = "deep-agent-standing-system"
# The 2026-11-07 migration round — the output node's two charters (each
# opening with the output node's place in the three-graph chain), the
# fork pre-planner's system prompt, the plan session's correction and
# batch-followup message frames, the compact node's system prompt, and
# the supervisor's intervention message frame.
AGENT_PROMPT_CUSTOM_OUTPUT_DIRECTIVE = "deep-agent-custom-output-directive"
AGENT_PROMPT_RESULT_OUTPUT_DIRECTIVE = "deep-agent-result-output-directive"
AGENT_PROMPT_SUBPLAN_SYSTEM = "deep-agent-subplan-system"
AGENT_PROMPT_PLAN_REPAIR_MESSAGE = "deep-agent-plan-repair-message"
AGENT_PROMPT_PLAN_BATCH_FOLLOWUP = "deep-agent-plan-batch-followup"
AGENT_PROMPT_COMPACT_SYSTEM = "deep-agent-compact-system"
AGENT_PROMPT_SUPERVISOR_INTERVENTION = "deep-agent-supervisor-intervention"


class PromptStoreMixin(_StoreBase):
    """The ai_prompts accessors + the KG-hotpath evidence read."""

    async def fetch_plan_prompts(self, prompt_keys: Sequence[str]) -> List[PromptRow]:
        """The active plan-type rows (prompt_type 2) among *prompt_keys*."""
        conn = await self._connect()
        try:
            records = await conn.fetch(
                "SELECT prompt_key, version, instruction FROM llm.ai_prompts "
                "WHERE prompt_key = ANY($1::text[]) AND prompt_type = 2 "
                "AND is_active ORDER BY ordinal, prompt_key",
                list(prompt_keys),
            )
        finally:
            await conn.close()
        return [
            PromptRow(prompt_key=r[0], version=int(r[1]), instruction=r[2])
            for r in records
        ]


    async def fetch_prompt_catalog(self,
        prompt_keys: Sequence[str],
    ) -> List[PromptCatalogRow]:
        """The active UI-pickable rows (prompt_type 1 skills + 2 plan) among
        *prompt_keys* as CATALOG rows — one cheap read of the preview/label
        columns plus the instruction LENGTH (never the text), the gradual
        loading ladder's first rung (see ``planning.prompt_loading``)."""
        conn = await self._connect()
        try:
            records = await conn.fetch(
                "SELECT prompt_key, prompt_sub_type, prompt_type, title, "
                "preview, version, bound_labels, optional_labels, "
                "length(instruction) FROM llm.ai_prompts "
                "WHERE prompt_key = ANY($1::text[]) "
                "AND prompt_type IN (1, 2) AND is_active "
                "ORDER BY prompt_type DESC, ordinal, prompt_key",
                list(prompt_keys),
            )
        finally:
            await conn.close()
        return [
            PromptCatalogRow(
                prompt_key=str(r[0]), sub_type=str(r[1]), prompt_type=int(r[2]),
                title=str(r[3]), preview=str(r[4] or ""), version=int(r[5]),
                bound_labels=tuple(str(x) for x in (r[6] or [])),
                optional_labels=tuple(str(x) for x in (r[7] or [])),
                instruction_chars=int(r[8] or 0))
            for r in records
        ]


    async def fetch_hotpath_evidence(
        self,
        needles: Sequence[str],
        namespaces: Sequence[int] = (0, 1),
        limit: int = 5,
    ) -> List[HotpathChainRow]:
        """The KG-HOTPATH evidence read for the prompt loader's
        corroboration rung (2026-11-07, ``planning/kg_prompts``): the
        PROMOTED chains (``llm.kg_hotpaths`` — the two-hit-verified
        replay layer) whose query text is trigram-SIMILAR to any of the
        run's needles (question / scope / item labels — the same
        haystack pieces the ladder matches), one row per chain with its
        edge ids in depth order. Rides ``ix_kg_hotpaths_query_gin``
        (the DDL documents this exact similarity-probing affordance;
        the exact-match ``lookup`` is nearly useless here — a fresh
        run's question text is never verbatim-equal to a past
        kg_search's keywords). Similarity threshold lowered to 0.25 for
        recall (hotpath queries are short keyword strings, the needles
        whole sentences); the strongest ``hit_count`` chains come
        first. Fail-soft at the caller."""
        conn = await self._connect()
        try:
            await conn.execute(
                "SET pg_trgm.similarity_threshold = 0.25")
            records = await conn.fetch(
                "SELECT h.namespace, h.query, h.chain_id, "
                "max(h.entity_hash) AS entity_hash, "
                "max(h.hit_count) AS hit_count, "
                "array_agg(h.edge_id ORDER BY h.depth) AS edge_ids "
                "FROM llm.kg_hotpaths h "
                "WHERE h.namespace = ANY($1::smallint[]) "
                "AND h.query % ANY($2::text[]) "
                "GROUP BY h.namespace, h.query, h.chain_id "
                "ORDER BY max(h.hit_count) DESC, h.namespace, h.query "
                "LIMIT $3",
                [int(n) for n in namespaces],
                [str(n) for n in needles],
                max(1, int(limit)),
            )
        finally:
            await conn.close()
        return [
            HotpathChainRow(
                namespace=int(r[0]), query=str(r[1]), chain_id=str(r[2]),
                entity_hash=str(r[3] or ""), hit_count=int(r[4] or 0),
                edge_ids=tuple(int(e) for e in (r[5] or [])))
            for r in records
        ]

    async def fetch_prompt_instructions(self,
        prompt_keys: Sequence[str],
    ) -> List[PromptRow]:
        """The active skills/plan rows (prompt_type 1 + 2) among
        *prompt_keys* with their FULL instruction texts — the ladder's
        on-demand fetch AFTER the load set is decided (keyword-forced +
        planner-verdict + plan rows; ``fetch_plan_prompts`` stays the
        type-2-only convenience)."""
        conn = await self._connect()
        try:
            records = await conn.fetch(
                "SELECT prompt_key, version, instruction FROM llm.ai_prompts "
                "WHERE prompt_key = ANY($1::text[]) "
                "AND prompt_type IN (1, 2) AND is_active "
                "ORDER BY prompt_type DESC, ordinal, prompt_key",
                list(prompt_keys),
            )
        finally:
            await conn.close()
        return [
            PromptRow(prompt_key=r[0], version=int(r[1]), instruction=r[2])
            for r in records
        ]


    async def fetch_agent_prompts(self) -> Dict[str, str]:
        """The ACTIVE internal agent-prompt rows (prompt_type 3) keyed by
        prompt_key — the DB-managed overrides of the deep agent's inline
        prompt constants: the planner's system prompt, the per-step
        addendum, and the forked child's base prompt and addenda. A missing
        key = the consumer's built-in constant; the caller wraps the fetch
        fail-soft (prompt sourcing never fails a run, the same contract as
        fetch_system_prompt)."""
        conn = await self._connect()
        try:
            records = await conn.fetch(
                "SELECT prompt_key, instruction FROM llm.ai_prompts "
                "WHERE prompt_type = 3 AND is_active")
        finally:
            await conn.close()
        return {str(r[0]): str(r[1]) for r in records}


    async def fetch_tool_prompts(self) -> Dict[str, str]:
        """The ACTIVE tool-description rows (prompt_type 4 'tool' —
        static/ai_prompts/10_tool_prompts.sql, seeded from the tool
        classes' DEFAULT_DESCRIPTION constants) flattened to
        ``"prompt_key|prompt_sub_type"`` -> instruction — the map the tool
        kits (``tools.base``'s override contract) apply over each tool's
        built-in schema description at mount time. Plain str keys so the
        dict rides the checkpointed ThreadState; the caller wraps the fetch
        fail-soft (a DB outage leaves every tool its built-in text)."""
        conn = await self._connect()
        try:
            records = await conn.fetch(
                "SELECT prompt_key, prompt_sub_type, instruction "
                "FROM llm.ai_prompts WHERE prompt_type = 4 AND is_active")
        finally:
            await conn.close()
        return {f"{str(r[0])}|{str(r[1])}": str(r[2]) for r in records}


    async def fetch_system_prompt(self) -> Optional[str]:
        """The run's standing system prompt: the ACTIVE system-type row of
        llm.ai_prompts (prompt_type 0, lowest ordinal) — the DB-managed
        override for agent.DEFAULT_SYSTEM_PROMPT. None = no system row (the
        runner falls back to the built-in default). The runner renders the
        text as a template with the run's input args before use."""
        conn = await self._connect()
        try:
            record = await conn.fetchrow(
                "SELECT instruction FROM llm.ai_prompts "
                "WHERE prompt_type = 0 AND is_active "
                "ORDER BY ordinal, prompt_key LIMIT 1",
            )
        finally:
            await conn.close()
        return str(record[0]) if record is not None else None
