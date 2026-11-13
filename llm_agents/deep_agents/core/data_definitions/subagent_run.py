"""SubagentRun — data definition (RE-EXPORT SHIM).

The 2026-11-07 split put ``SubagentRequest`` and ``SubagentRun`` in two
files; pydantic resolves every dataclass field annotation against its
OWN module's globals and the pair is mutually referential
(``SubagentRun.request`` → ``SubagentRequest``; the hook ALIASES carry
``"SubagentRun"`` forward refs) — the split deferred every
``AgentResult`` schema build and no agent could answer. The canonical
definitions live in :mod:`...data_definitions.subagent_request` (one
namespace again); this shim keeps the import path (the lazy
``data_definitions.__getattr__`` stems unchanged).
"""
from llm_agents.deep_agents.core.data_definitions.subagent_request import (
    SUBAGENT_SYSTEM_PROMPT, SubagentInputHook, SubagentOutputHook,
    SubagentRequest, SubagentRun, SubagentRunWrapper, SubagentStatus,
)
