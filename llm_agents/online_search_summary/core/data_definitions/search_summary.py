"""SearchSummary — data definition.

Migrated verbatim from :mod:`llm_agents.online_search_summary.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from llm_agents.online_search_summary.core.citations import extract_cited_refs


@dataclass
class SearchSummary:
    """Normalized search + summary result (the agent's unit of work)."""
    question: str
    answer: str                       # inline [来源：ref_N] markers kept
    hits: List[SearchHit] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    engine: Optional[str] = None
    mode: Optional[str] = None        # native | compose
    request_id: Optional[str] = None
    created: Optional[datetime.datetime] = None
    usage: Optional[Dict[str, Any]] = None

    @property
    def cited_refs(self) -> List[str]:
        return extract_cited_refs(self.answer)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "provider": self.provider,
            "model": self.model,
            "engine": self.engine,
            "mode": self.mode,
            "request_id": self.request_id,
            "created": (
                self.created.isoformat() if self.created else None),
            "usage": self.usage,
            "cited_refs": self.cited_refs,
            "references": [h.to_dict() for h in self.hits],
        }

    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "SearchSummary":
        """Rebuild the summary from :meth:`to_dict` output — the ai_daily
        artifact envelope. Unknown keys (target_date / stored_qa_id /
        movers / …) are ignored; malformed optional fields degrade to
        None so a partially-written artifact still stores."""
        hits: List[SearchHit] = []
        for h in obj.get("references") or []:
            if not isinstance(h, dict) or not h.get("title"):
                continue
            publish_date = None
            if h.get("publish_date"):
                try:
                    publish_date = datetime.date.fromisoformat(
                        h["publish_date"])
                except ValueError:
                    pass
            hits.append(SearchHit(
                refer=h.get("refer") or "",
                title=h["title"],
                content=h.get("content"),
                link=h.get("link"),
                media=h.get("media"),
                icon=h.get("icon"),
                publish_date=publish_date,
                publish_date_raw=h.get("publish_date_raw"),
            ))
        created = None
        if obj.get("created"):
            try:
                created = datetime.datetime.fromisoformat(obj["created"])
            except ValueError:
                pass
        return cls(
            question=obj.get("question") or "",
            answer=obj.get("answer") or "",
            hits=hits,
            provider=obj.get("provider"),
            model=obj.get("model"),
            engine=obj.get("engine"),
            mode=obj.get("mode"),
            request_id=obj.get("request_id"),
            created=created,
            usage=obj.get("usage"),
        )
