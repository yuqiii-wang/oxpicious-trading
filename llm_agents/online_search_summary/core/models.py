"""llm_agents.online_search_summary.core.models — Normalized result models.

Provider-independent dataclasses + the shared request vocabulary used by
every provider and by the persistence layer. Parsing/formatting of the
citation markers lives in ``core.citations``; nothing here touches the
network or the DB.

Shared request vocabulary: ``RECENCY_FILTERS`` / ``CONTENT_SIZES`` are the
ZhiPu values carried by ``SearchOptions``; future providers translate them
to their own equivalents inside their subclass.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from llm_agents._core.models import SHANGHAI_TZ
from llm_agents.online_search_summary.core.citations import extract_cited_refs

# Shared "today" reference for fallback dates and prompt anchors — the
# corpus and the market calendar it serves are Asia/Shanghai (re-export
# of the llm_agents._core constant).

RECENCY_FILTERS = ("oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit")
CONTENT_SIZES = ("medium", "high")


# ----------------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------------
@dataclass
class SearchHit:
    """One normalized search-result reference."""
    refer: str                        # canonical citation tag (ref_N)
    title: str
    content: Optional[str] = None     # page summary / snippet
    link: Optional[str] = None
    media: Optional[str] = None       # source site name
    icon: Optional[str] = None        # site favicon URL
    publish_date: Optional[datetime.date] = None
    publish_date_raw: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "refer": self.refer,
            "title": self.title,
            "content": self.content,
            "link": self.link,
            "media": self.media,
            "icon": self.icon,
            "publish_date": (
                self.publish_date.isoformat() if self.publish_date else None),
            "publish_date_raw": self.publish_date_raw,
        }


@dataclass
class SearchIntent:
    """One rewritten query from the provider's intent recognition."""
    query: Optional[str] = None
    intent: Optional[str] = None     # e.g. SEARCH_ALL / SEARCH_NONE
    keywords: Optional[str] = None


@dataclass
class SearchOptions:
    """Provider-agnostic search knobs (passed through to the provider)."""
    engine: Optional[str] = None      # None -> provider default
    count: int = 10
    recency: str = "noLimit"
    domain: Optional[str] = None      # restrict to one site
    content_size: str = "medium"      # snippet length: medium | high
    intent: bool = True               # standalone search: recognize intent
    request_id: Optional[str] = None

    def validate(self) -> None:
        if not 1 <= self.count <= 50:
            raise ValueError(f"count must be 1-50, got {self.count}")
        if self.recency not in RECENCY_FILTERS:
            raise ValueError(f"recency must be one of {RECENCY_FILTERS}, "
                             f"got {self.recency!r}")
        if self.content_size not in CONTENT_SIZES:
            raise ValueError(f"content_size must be one of {CONTENT_SIZES}, "
                             f"got {self.content_size!r}")


@dataclass
class SearchResponse:
    """Normalized standalone-search response."""
    hits: List[SearchHit] = field(default_factory=list)
    intents: List[SearchIntent] = field(default_factory=list)
    provider: Optional[str] = None
    engine: Optional[str] = None
    request_id: Optional[str] = None
    created: Optional[datetime.datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "engine": self.engine,
            "request_id": self.request_id,
            "created": (
                self.created.isoformat() if self.created else None),
            "intents": [vars(i) for i in self.intents],
            "references": [h.to_dict() for h in self.hits],
        }


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
