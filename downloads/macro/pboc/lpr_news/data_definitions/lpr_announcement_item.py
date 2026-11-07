"""LprAnnouncementItem — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.lpr_news.__main__` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


LPR_CATEGORY = "lpr"


LPR_FILE_PREFIX = "pboc_lpr"


@dataclass
class LprAnnouncementItem:
    title: str
    detail_url: str
    list_page: int = 0
    pub_date: Optional[str] = None
    detail_slug: Optional[str] = None
    lpr_1y: Optional[float] = None
    lpr_5y: Optional[float] = None
    raw_body: str = ""
    parse_warnings: List[str] = field(default_factory=list)

    def md_filename(self) -> str:
        d = self.pub_date or "00000000"
        slug = self.detail_slug or "x"
        return f"{LPR_FILE_PREFIX}_{d}_{slug}.md"

    def to_markdown(self) -> str:
        lines = [
            "---",
            f"category: {LPR_CATEGORY}",
            f"title: {self.title}",
            f"detail_url: {self.detail_url}",
            f"pub_date: {self.pub_date or ''}",
            f"lpr_1y: {'' if self.lpr_1y is None else f'{self.lpr_1y:g}'}",
            f"lpr_5y: {'' if self.lpr_5y is None else f'{self.lpr_5y:g}'}",
        ]
        if self.parse_warnings:
            lines.append(f"parse_warnings: {self.parse_warnings!r}")
        lines += [
            "---",
            "",
            f"# {self.title}",
            "",
            f"- Pub date: **{self.pub_date or 'n/a'}**",
            f"- Detail: {self.detail_url}",
            f"- 1Y LPR: {'' if self.lpr_1y is None else f'{self.lpr_1y:g}%'}",
            f"- 5Y+ LPR: {'' if self.lpr_5y is None else f'{self.lpr_5y:g}%'}",
            "",
            "## Raw body",
            "",
            "```",
            self.raw_body.strip(),
            "```",
            "",
        ]
        return "\n".join(lines)
