"""OmaAnnouncementItem — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.oma.__main__` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


OMA_CATEGORY = "oma"


OMA_FILE_PREFIX = "pboc_oma"


@dataclass
class OmaAnnouncementItem:
    title: str
    detail_url: str
    list_page: int = 0
    pub_date: Optional[str] = None
    detail_slug: Optional[str] = None
    serial_year: Optional[str] = None
    serial_no: Optional[str] = None
    type: str = "other"
    keywords: List[str] = field(default_factory=list)
    raw_body: str = ""
    parse_warnings: List[str] = field(default_factory=list)

    def md_filename(self) -> str:
        d = self.pub_date or "00000000"
        slug = self.detail_slug or "x"
        return f"{OMA_FILE_PREFIX}_{d}_{slug}.md"

    def to_markdown(self) -> str:
        lines = [
            "---",
            f"category: {OMA_CATEGORY}",
            f"title: {self.title}",
            f"detail_url: {self.detail_url}",
            f"pub_date: {self.pub_date or ''}",
            f"type: {self.type}",
            f"keywords: {'|'.join(self.keywords)}",
        ]
        if self.serial_year:
            lines.append(f"serial_year: {self.serial_year}")
        if self.serial_no:
            lines.append(f"serial_no: {self.serial_no}")
        if self.parse_warnings:
            lines.append(f"parse_warnings: {self.parse_warnings!r}")
        lines += [
            "---",
            "",
            f"# {self.title}",
            "",
            f"- Pub date: **{self.pub_date or 'n/a'}**",
            f"- Type: **{self.type}**",
            f"- Keywords: {', '.join(self.keywords) if self.keywords else '(none)'}",
            f"- Detail: {self.detail_url}",
            "",
            "## Raw body",
            "",
            "```",
            self.raw_body.strip(),
            "```",
            "",
        ]
        return "\n".join(lines)
