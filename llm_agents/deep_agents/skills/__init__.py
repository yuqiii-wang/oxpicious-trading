"""llm_agents.deep_agents.skills — the SKILL LAYER: the run's selected
study instructions (``llm.ai_prompts`` rows) as OBJECTS.

A SKILL is one prompt row the user picks for a run — an approach
instruction (prompt_type 1 ``skills``: db_query / online_search /
biz_guide conventions) or a generation-plan prompt (prompt_type 2
``plan``) — carried by the request's ``prompt_key`` list. This layer
models them as :class:`Skill` objects (key / sub_type / title /
version / instruction) grouped into a :class:`SkillBook`, and owns the
rendering both consumers share:

  * ``Skill.render``  — the DYNAMIC-TEMPLATE substitution (``{{key}}``
    placeholders filled from the run's scope/question args);
  * ``SkillBook.render_all``      — the runner's pass-down shape
    (prompt_key -> rendered instruction — every step hop's conversation
    and every forked child receives the SAME rendered texts the planner
    planned under);
  * ``SkillBook.items_block``     — the quoted "[Selected prompts —
    …]" block builder; the graph's per-step user message
    (``langgraph.prompts.step_user_message``) composes its prompt section
    from it (the planner's plan-side block stays inline — planning
    must not import skills, and skills.base sits ON planning for the
    template machinery).

Loading lives in ``skills.loader`` (a thin DB fetch over
``planning.store``'s prompt accessor). The skill-TYPE rows (prompt_type
1) stay AGENT-FETCHED by design (JIT retrieval — the agent queries
``llm.ai_prompts`` itself through its read-only DB tools when its work
needs a convention); this layer's loader resolves the run's PLAN-type
rows once at run start, the pass-down the runner distributes.

Boundary: ``skills`` may import ``planning`` (rendering + store
access); nothing imports ``skills`` back — the graph composes it.
"""
from llm_agents.deep_agents.skills.base import Skill, SkillBook
from llm_agents.deep_agents.skills.loader import (
    SkillLoader, fetch_skill_book,
)

__all__ = ["Skill", "SkillBook", "SkillLoader", "fetch_skill_book"]
