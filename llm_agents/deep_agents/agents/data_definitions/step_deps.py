"""StepDeps — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.agents.step_agent` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from llm_agents.deep_agents.core.loop import DeepAgent


@dataclass(frozen=True)
class StepDeps:
    """One inline hop's runner-side collaborators (injected — this
    module imports no langgraph; composition stays in the runner)."""
    # (step_row, broker, parent_context_id) -> the hooks trio for this
    # hop's subagent lane (langgraph.hooks.ForkHooks pre-bound
    # runner-side with store/model/state/biz context).
    make_hooks: Callable[[Any, Any, Optional[str]], Any]
    # (step_row, node_prompts, resume_block) -> the hop's user message
    # (step_user_message + the resume block appended).
    build_message: Callable[..., str]
    # (step_row, broker, plan_lock, writer) -> the builtin_tools dict
    # (plan kit bound to the broker's cancel + the wave's plan lock,
    # plus stats/kg/prompt kits, DB descriptions applied).
    make_tools: Callable[[Any, Any, Optional[Any], Any], Dict[str, Any]]
    # (thread_id) -> the hop's fork broker (langgraph.hooks.ForkBroker
    # — constructed runner-side; the agent stays langgraph-free).
    make_broker: Callable[[str], Any]
    # (directive_text) -> this step's dynamic-rung keyword-matched
    # prompts (runner closure over the state; awaited).
    get_node_prompts: Callable[[str], Any]
    # () -> this run's sandbox cell (run_sandbox(state)).
    sandbox: Callable[[], Optional[Any]]
    # The composed system prompt (standing + base + step addendum).
    system: str
    # The DeepAgent kwargs shared by every hop (model / max_steps /
    # temperature / subagent caps / tool_prompts).
    common: Dict[str, Any]
    # (thread_id) -> the hop's fork broker (langgraph.hooks.ForkBroker
    # — constructed runner-side; the agent stays langgraph-free).
    make_broker: Callable[[str], Any]
    # The MCP tool client (injected — core, not langgraph).
    mcp: Any
    # The conversation engine class (core.loop.DeepAgent; injectable
    # for stub tests).
    agent_factory: Callable[..., Any] = DeepAgent
