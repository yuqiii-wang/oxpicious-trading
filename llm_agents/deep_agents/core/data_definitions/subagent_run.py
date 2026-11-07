"""SubagentRun — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.core.delegation` (one file per ``@dataclass``)."""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from typing import Optional, Literal


SubagentStatus = Literal[
    "queued", "running", "completed", "failed", "cancelled", "timed_out",
]


@dataclass
class SubagentRun:
    """One delegation's lifecycle record — registry entry and the hooks'
    interception surface."""

    run_id: str
    request: SubagentRequest
    status: SubagentStatus = "queued"
    answer: Optional[str] = None
    error: Optional[str] = None
    steps: int = 0
    tool_call_count: int = 0
    started_at: Optional[datetime.datetime] = None
    ended_at: Optional[datetime.datetime] = None
    # True from spawn when the run detached (background fork): its
    # result waits in the agent's background queue until collected.
    background: bool = False
    task: Optional["asyncio.Task[str]"] = field(
        default=None, repr=False, compare=False)
