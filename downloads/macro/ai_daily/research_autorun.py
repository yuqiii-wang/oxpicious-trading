"""downloads.macro.ai_daily.research_autorun — the movers-embedded AI Research run.

The AUTO half of the research-logic knowledge base
(``text.llm_qa_by_research``): embedded in the ai_daily movers phase —
the industry llm-qa step that asks WHICH industries sit excess
above/below the broad market after observed large fluctuations. Every
industry the movers plan loads (rise/drop survivors) triggers ONE
``llm_agents.llm_qa_by_research.generate_research_qa`` run (the
five-request research flow), so a big mover's research row exists by the
time a human opens the Research page.

QUARTERLY COOLDOWN — the generation is expensive (five searches + five
chat requests per row), so each industry is generated at most once per
season: an industry whose newest ACTIVE research row is inside the last
``RESEARCH_COOLDOWN_DAYS`` (91d) is skipped. ``force`` (the ai_daily
``--force``) overrides the gate for a deliberate regeneration. The gate
binds the INDUSTRY picks only — broad-market tags never reach here (the
movers plan excludes them by contract).

A failed industry never kills the batch (movers-style): its outcome is
logged and returned; the movers envelope carries the research block.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ai_daily.research")


def research_subject(label: str) -> str:
    """The research subject handed to the five dimension prompts."""
    return f"{label}行业"


def _sector_of(industry_id: str) -> Optional[str]:
    """The industry's parent sector (taxonomy-denormalized, cli contract)."""
    from builds.text.keywords import get_taxonomy
    return get_taxonomy().sector_of(industry_id)


async def run_research_autorun(
    conn,
    picks: List[Dict[str, Any]],
    *,
    provider_name: str = "zhipu",
    model: Optional[str] = None,
    lang: str = "zh",
    force: bool = False,
) -> List[Dict[str, Any]]:
    """Generate research rows for the movers picks; return the outcomes.

    *picks* are the movers plan's candidate dicts (``industry_id`` +
    ``label`` present, both sides merged, duplicates collapsed). Each
    pick passes the quarterly cooldown gate first
    (:func:`industry_quarter_present` — skipped when a row exists within
    the last season, unless *force*), then the five-request flow stores
    the row. Returns one outcome dict per pick.
    """
    from llm_agents.llm_qa_by_research.generation import generate_research_qa
    from llm_agents.llm_qa_by_research.storage import (
        RESEARCH_COOLDOWN_DAYS, industry_quarter_present)
    from llm_agents.online_search_summary.providers.registry import (
        get_provider)

    outcomes: List[Dict[str, Any]] = []
    seen: set = set()
    for p in picks:
        industry_id = p.get("industry_id")
        if not industry_id or industry_id in seen:
            continue
        seen.add(industry_id)
        label = p.get("label") or industry_id
        outcome: Dict[str, Any] = {"industry_id": industry_id,
                                   "label": label}
        try:
            if not force:
                present = await industry_quarter_present(conn, industry_id)
                if present is not None:
                    outcome.update(status="cooldown", last_qa_date=(
                        present.date().isoformat()
                        if isinstance(present, datetime.datetime) else None))
                    logger.info("  research[%s]: skip — row present %s "
                                "(quarterly cooldown)", industry_id,
                                outcome["last_qa_date"])
                    outcomes.append(outcome)
                    continue
            qa_id = await generate_research_qa(
                conn, get_provider(provider_name)(),
                research_subject(label), industry_id=industry_id,
                sector_id=_sector_of(industry_id), model=model, lang=lang)
            outcome.update(status="ok", qa_id=qa_id)
            logger.info("  research[%s]: stored qa_id=%s", industry_id, qa_id)
        except Exception as e:  # one failed industry must not kill the batch
            outcome.update(status="failed",
                           error=f"{type(e).__name__}: {e}")
            logger.warning("  research[%s]: FAILED %s: %s", industry_id,
                           type(e).__name__, e)
        outcomes.append(outcome)
    if outcomes:
        logger.info("research autorun: %d industry(ies) processed "
                    "(cooldown window %dd)", len(outcomes),
                    RESEARCH_COOLDOWN_DAYS)
    return outcomes
