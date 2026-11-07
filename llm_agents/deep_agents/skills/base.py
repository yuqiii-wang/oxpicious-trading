"""llm_agents.deep_agents.skills.base — the Skill / SkillBook objects.

The run's selected prompts as objects: one :class:`Skill` per
``llm.ai_prompts`` row, one :class:`SkillBook` per run. The book owns
the two renderings the harness needs — the per-run template pass-down
(``render_all``) and the quoted selected-prompts block
(``items_block``, shared by the planner's and the step hop's user
messages, which historically duplicated the join).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from llm_agents.deep_agents.planning.planner import render_template


class Skill:
    """One selected prompt row: its identity (key / sub_type / title /
    version) and its instruction text.

    ``render`` substitutes the row's ``{{key}}`` placeholders from the
    run's template args (sec_type / code / name / question) — known
    keys always substitute (empty string when the run carries no
    scope), unknown keys stay literal so a typo is visible."""

    def __init__(self, prompt_key: str, instruction: str, *,
                 sub_type: str = "", title: str = "",
                 version: int = 1) -> None:
        self.prompt_key = prompt_key
        self.instruction = instruction
        self.sub_type = sub_type
        self.title = title
        self.version = version

    def render(self, args: Dict[str, str]) -> str:
        """The instruction with the run's ``{{key}}`` args substituted
        (no args = the raw text)."""
        if not args:
            return self.instruction
        return render_template(self.instruction, args)

    def quoted(self, args: Optional[Dict[str, str]] = None) -> str:
        """One list-item line of a selected-prompts block:
        ``- <prompt_key>: <rendered instruction>``."""
        return f"- {self.prompt_key}: {self.render(args or {})}"

    def __repr__(self) -> str:
        return (f"Skill({self.prompt_key!r}, sub_type={self.sub_type!r}, "
                f"version={self.version})")


class SkillBook:
    """The run's selected skills, in fetch (ordinal) order — the
    immutable study-instruction set the planner plans under and every
    step hop / forked child receives."""

    def __init__(self, skills: Iterable[Skill]) -> None:
        self.skills: List[Skill] = list(skills)

    def __len__(self) -> int:
        return len(self.skills)

    def __bool__(self) -> bool:
        return bool(self.skills)

    def keys(self) -> List[str]:
        """The books' prompt keys, in order."""
        return [s.prompt_key for s in self.skills]

    def render_all(self, args: Optional[Dict[str, str]] = None) \
            -> Dict[str, str]:
        """The runner's pass-down shape: prompt_key -> RENDERED
        instruction (the dict every step hop's conversation and every
        forked child's structured data receives)."""
        return {s.prompt_key: s.render(args or {})
                for s in self.skills}

    def items(self, args: Optional[Dict[str, str]] = None) \
            -> List[Tuple[str, str]]:
        """(prompt_key, rendered instruction) pairs, in order."""
        return [(s.prompt_key, s.render(args or {}))
                for s in self.skills]

    @staticmethod
    def items_block(items: Iterable[Tuple[str, str]], header: str) -> str:
        """The quoted selected-prompts block both consumers build:
        the header line, then one ``- key: text`` item per prompt
        (blank-line separated — the texts may be multi-line)."""
        quoted = "\n\n".join(f"- {key}: {text}" for key, text in items)
        return f"{header}\n{quoted}"

    def block(self, header: str,
              args: Optional[Dict[str, str]] = None) -> str:
        """This book's selected-prompts block under *header*."""
        return self.items_block(self.items(args), header)
