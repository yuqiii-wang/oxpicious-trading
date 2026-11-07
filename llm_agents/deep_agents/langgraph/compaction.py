"""llm_agents.deep_agents.langgraph.compaction — CONTEXT COMPACTION for the
thread graph: the step lane's accumulated evidence (the findings bridge
between the per-step conversations) is bounded by the model's context
window, and when it APPROACHES that limit the router detours through a
COMPACT node — one LLM call that merges the evidence into a bounded
digest the later hops carry instead of the raw lines.

Why a node: every step hop is its own conversation whose only bridge to
the earlier steps is the FINDINGS block in its user message
(``prompts.step_user_message``). That block grows with every finished
step (each ``finding_line`` ≤ the configured finding_max_chars);
left alone the render
fallback (``prompts.findings_block``'s aging) bounds it by destroying
older findings down to their title lines — lossy, dumb, and it fires
only at render time. Compaction replaces that at the SOURCE: the
trigger reads the UNAGED evidence, and the compact node's digest keeps
the substance the aging would discard (verdicts, numbers, unresolved
threads). The aging stays as the never-fail backstop (a failed
compaction must never lose evidence).

THE MERGE CYCLE (finished sub nodes merge into the compacted node): a
finished step hop appends its finding line to the raw list as always;
the next hop renders digest + raw tail; when the tail again approaches
the limit, the NEXT compact node folds prior digest + tail into a
fresh digest — every finished node's evidence ends up merged into the
latest compacted node, never silently dropped.

Calibration (token estimates, CJK-aware — ``estimate_tokens``):
  * the EVIDENCE budget (``COMPACT_EVIDENCE_TOKENS``) is sized UNDER
    the render aging ceiling (the configured findings_block_max_chars ≈
    that many tokens worst-case CJK), so compaction always fires
    BEFORE the aging fallback would destroy a finding;
  * the WINDOW guard on top bounds the whole projected hop input
    (goal + structured context + prompt texts + directive + evidence)
    to ``INPUT_WINDOW_SHARE`` of the model's window — live only for
    small-window models, but correct for every one.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

# ----------------------------------------------------------------------
# The context windows (tokens) — a small map + the family default.
# ----------------------------------------------------------------------

MODEL_CONTEXT_TOKENS: Dict[str, int] = {
    # ZhiPu GLM family: 128K-class context windows.
    "glm-5.3": 131072,
    "glm-5.3-flash": 131072,
    "glm-5.3-air": 131072,
}
DEFAULT_CONTEXT_TOKENS = 131072

# The share of the window a hop's INPUT may claim before compaction —
# the rest must absorb the conversation the hop itself grows (tool
# results, assistant text; bounded per hop by the per-step max_steps
# cap, but a heavy research step spends tens of k tokens on tool
# traffic alone).
INPUT_WINDOW_SHARE = 0.35

# The EVIDENCE budget: how many tokens of accumulated evidence (the
# digest + the unaged raw findings) a hop may carry before compaction
# fires — see the module header's calibration note.
COMPACT_EVIDENCE_TOKENS = 4000

# The digest's hard char bound (the prompt asks for less; this is the
# belt under it — a runaway digest must not outgrow the raw list it
# replaced).
COMPACT_DIGEST_CHARS = 4000

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    """Rough token count for mixed zh/en text: CJK ≈ one token a char,
    everything else ≈ four chars a token. A budgeting estimate, not a
    billable count."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    return cjk + (len(text) - cjk + 3) // 4


def model_context_tokens(model: Optional[str]) -> int:
    """The model's context window; unknown models take the family
    default (over-estimating the budget never fires compaction late)."""
    return MODEL_CONTEXT_TOKENS.get(str(model or ""), DEFAULT_CONTEXT_TOKENS)


def evidence_tokens(
    digest: Optional[str], findings: List[str],
) -> int:
    """The UNAGED evidence size — the digest plus every raw finding
    line, before any render aging. This is what compaction shrinks."""
    total = estimate_tokens(digest or "")
    for line in findings:
        total += estimate_tokens(line)
    return total


def should_compact(
    *,
    model: Optional[str],
    goal: str,
    data: Optional[Dict[str, Any]],
    prompt_texts: Dict[str, str],
    digest: Optional[str],
    findings: List[str],
    directive: str = "",
    prompt_catalog: Optional[Dict[str, str]] = None,
) -> bool:
    """Whether the NEXT hop's input approaches the LLM context limit and
    must detour through the compact node first. False when there is no
    evidence to compact (nothing before this hop ran — compaction
    cannot shrink the base context itself). ``prompt_catalog`` (the
    loading ladder's not-loaded preview lines) counts toward the base —
    the menu rides every hop's message, so the window guard must see
    it."""
    evidence = evidence_tokens(digest, findings)
    if evidence <= 0:
        return False
    if evidence >= COMPACT_EVIDENCE_TOKENS:
        return True
    window = model_context_tokens(model)
    # Small-window guard: the whole projected input (base + evidence)
    # against the window share.
    base = (estimate_tokens(goal)
            + estimate_tokens(json.dumps(data or {}, ensure_ascii=False))
            + sum(estimate_tokens(t) for t in prompt_texts.values())
            + sum(estimate_tokens(t) for t in (prompt_catalog or {}).values())
            + estimate_tokens(directive))
    return base + evidence >= int(window * INPUT_WINDOW_SHARE)


# ----------------------------------------------------------------------
# The compact node's engine — ONE LLM call: digest + raw findings in,
# one bounded digest out.
# ----------------------------------------------------------------------

COMPACT_SYSTEM_PROMPT = (
    "You are the CONTEXT COMPACTION step of a planned multi-step "
    "research run. The run's finished research steps leave findings — "
    "one line each, verdict + key numbers — and they accumulate toward "
    "the model context limit. Merge EVERYTHING you are given (an "
    "earlier compacted digest when one exists, plus the newer raw "
    "findings) into ONE self-contained digest the remaining steps will "
    "carry instead of the raw lines.\n"
    "Keep, in the findings' order: each step's verdict "
    "(pass/fail/hold), its key numbers (levels, dates, magnitudes), "
    "named entities, and anything still unresolved or contradicted "
    "between steps — those last two are the easiest to lose and the "
    "most expensive to lose. Drop narrative, method talk and "
    "repetition. Add NO facts of your own. Stay under ~3000 "
    "characters; plain lines keyed by step number."
)


def _compact_user_message(
    goal: str,
    digest: Optional[str],
    findings: List[str],
) -> str:
    parts = [f"[Run goal — what every finding is about]\n{goal}"]
    if digest:
        parts.append(
            "[Compacted digest so far — merge it WITH the new findings "
            "below; neither may survive alone]\n" + digest)
    parts.append("[Raw findings since the last compaction]\n"
                 + "\n".join(findings))
    parts.append(
        "[Your output]\n"
        "The merged digest: every verdict and number above, keyed by "
        "step number, unresolved items flagged — nothing else.")
    return "\n\n".join(parts)


async def compact_findings(
    chat_complete: Callable[..., Any],
    *,
    goal: str,
    digest: Optional[str],
    findings: List[str],
    model: Optional[str] = None,
    temperature: float = 0.1,
    system_prompt: Optional[str] = None,
) -> str:
    """Run ONE compaction call: merge the prior digest (when one
    exists) and the raw findings into a fresh bounded digest. The
    digest is hard-capped at COMPACT_DIGEST_CHARS (the prompt asks for
    less; this is the backstop against a runaway reply). Raises on a
    provider failure — the node's fail-soft path keeps the raw
    findings untouched. ``system_prompt`` is the fetched
    ``deep-agent-compact-system`` row (the runner passes it from the
    run's agent_prompts map); None falls back to the built-in
    constant (the seed and the constant must stay in sync)."""
    messages: List[Dict[str, Any]] = [
        {"role": "system",
         "content": system_prompt or COMPACT_SYSTEM_PROMPT},
        {"role": "user",
         "content": _compact_user_message(goal, digest, findings)},
    ]
    message = await chat_complete(
        messages, model=model, tools=None, temperature=temperature)
    text = " ".join(str(message.get("content") or "").split())
    if not text:
        raise ValueError("compaction reply was empty")
    if len(text) > COMPACT_DIGEST_CHARS:
        text = text[:COMPACT_DIGEST_CHARS - 1] + "…"
    return text


def digest_block(digest: str, n_compacted: int) -> str:
    """The digest's render in a step hop's user message — the compacted
    evidence block that rides BESIDE the (small, post-compaction) raw
    findings tail."""
    return (
        f"[Compacted findings — the first {n_compacted} step(s), merged "
        "by the compact node; the numbers and verdicts the later steps "
        f"must keep]\n{digest}")
