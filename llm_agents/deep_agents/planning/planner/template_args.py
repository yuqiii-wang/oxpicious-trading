"""llm_agents.deep_agents.planning.planner.template_args — the run's
TEMPLATE ARGS + the {{key}} RENDERER + the STUDY-SCOPE prompt family.

The DYNAMIC-TEMPLATE machinery (moved here from langgraph.prompts
2026-11-06 so planning is self-contained): ``TemplateArgs`` flattens
the run's structured input to the ``{{key}}`` vocabulary
(``sec_type`` / ``code`` / ``name`` / ``question``), ``render_template``
substitutes known keys always (empty string when the run carries none)
and leaves unknown keys literal so a typo is visible. The STUDY-SCOPE
plan-prompt family (static/ai_prompts/09_plan_prompts seeds) lives
here too: four MUTUALLY EXCLUSIVE scope directives — exactly one
governs how wide a run studies; the runner auto-loads the applicable
DEFAULT when the pick names none of them.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

_TEMPLATE_ARG_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _scope_entries(data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The run's scope picks — the ``scopes`` list (the multi-pick
    subject tags), falling back to the legacy single ``scope`` object
    wrapped as a one-entry list; dict entries only, order preserved."""
    data = data or {}
    raw = data.get("scopes")
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    if isinstance(data.get("scope"), dict):
        return [data["scope"]]
    return []


class TemplateArgs(BaseModel):
    """The run's template args (2026-11-06 — the former loose dict): the
    scope picks (each ``{secType, code | industry, name?}``) and the
    question, flattened to the ``{{key}}`` vocabulary (``sec_type`` /
    ``code`` / ``name`` / ``question``). The multi-pick list joins:
    ``sec_type`` reads the FIRST pick, ``code`` the '+'-joined codes and
    ``name`` the '+'-joined names, so a saved plan's ``{{code}}``
    placeholder reads the whole set (a single pick renders exactly as
    before); ``code`` carries the industry id on an industry pick,
    mirroring llm.deep_agent_identities. A run with NO pick is
    market-wide and names itself ``All Market`` (the summary page's
    default disabled tag — a ``{{name}}`` placeholder in a saved plan
    renders it), with empty ``sec_type`` / ``code``.

    Dict-compatible (``get`` / ``__getitem__`` / ``in``) so the
    ``{{key}}`` renderers (``render_template``, ``SkillLoader.render_all``)
    keep their mapping shape."""

    sec_type: str = ""
    code: str = ""
    name: str = ""
    question: str = ""

    @classmethod
    def from_data(cls, data: Optional[Dict[str, Any]]) -> "TemplateArgs":
        """The args from the run's structured input ``data``."""
        entries = _scope_entries(data)
        question = (data or {}).get("question")
        codes = [str(e.get("code") or e.get("industry") or "").strip()
                 for e in entries]
        names = [n for n in (str(e.get("name") or "").strip()
                             for e in entries) if n]
        return cls(
            sec_type=str(entries[0].get("secType") or "").strip()
            if entries else "",
            code="+".join(c for c in codes if c),
            name="+".join(names) if entries else "All Market",
            question=str(question or "").strip(),
        )

    def get(self, key: str, default: str = "") -> str:
        """The dict-``get`` the template renderers use."""
        return str(getattr(self, key, default) or default)

    def __contains__(self, key: object) -> bool:
        """The dict-``in`` the ``{{key}}`` renderers test BEFORE they
        read: a pydantic model's default ``in`` iterates (name, value)
        field tuples, so a bare ``key in args`` is False for every key
        — render_template would leave every placeholder literal (the
        2026-11-07 saved-template fix; the loose dict this class
        replaced answered ``in`` by keys)."""
        return isinstance(key, str) and hasattr(self, key)

    def __getitem__(self, key: str) -> str:
        return str(getattr(self, key, ""))


def template_args(data: Optional[Dict[str, Any]]) -> TemplateArgs:
    """The run's template args — :meth:`TemplateArgs.from_data`."""
    return TemplateArgs.from_data(data)


def render_template(text: str, args: Dict[str, str]) -> str:
    """Substitute ``{{key}}`` placeholders: known keys -> the arg's value
    (empty string when the run carries none), unknown keys untouched."""
    return _TEMPLATE_ARG_RE.sub(
        lambda m: args[m.group(1)] if m.group(1) in args else m.group(0),
        text or "",
    )


# The STUDY-SCOPE plan-prompt family (static/ai_prompts/09_plan_prompts
# seeds): four MUTUALLY EXCLUSIVE scope directives — exactly one governs
# how wide a run studies. The runner auto-loads the applicable DEFAULT
# (default_scope_plan_key) when the pick names none of them; any
# explicit family pick suppresses the default.
STUDY_SCOPE_PLAN_PROMPTS = frozenset({
    "study-codes", "study-codes-related",
    "study-market-codes", "study-market-whole",
})


def default_scope_plan_key(args: Dict[str, str]) -> str:
    """The family member that governs a run whose pick named none of
    ``STUDY_SCOPE_PLAN_PROMPTS``: codes named (any scope pick —
    ``{{code}}`` carries the code or the industry id) → ``study-codes``;
    no code tag (the All Market run) → ``study-market-codes``."""
    return ("study-codes" if str(args.get("code") or "").strip()
            else "study-market-codes")
