"""llm_agents.deep_agents.planning.plan_json.review_wave — the
RESULT graph's built-in four-seat fallback (the wave the result-graph
builder runs when no type-result template row reads; the seeded
``default-result-wave`` row normally answers).
"""
from typing import List

from llm_agents.deep_agents.planning.store import PlannedStep

from llm_agents.deep_agents.planning.plan_json.contract import (
    REVIEW_WAVE_NODES,
)


def review_wave_steps() -> List[PlannedStep]:
    """The four RESULT-graph seats with their default goal titles -
    parallel-marked inline, never delegated (the built-in fallback the
    result-graph builder uses when no type-result template row
    reads)."""
    return [PlannedStep(node=spec.tag, title=spec.title, parallel=True)
            for spec in REVIEW_WAVE_NODES]
