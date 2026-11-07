"""AnnouncementItem — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.repo_news.__main__` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from downloads._common import service_urls


CATEGORY_OMO_TRANSACTION = "omo_transaction"


CATEGORY_OUTRIGHT_REPO = "outright_repo"


SERVICE = "downloads.macro.pboc.repo_news"


def _instruments_data_to_str(data: List[Dict]) -> str:
    """Serialize instruments_data to pipe-delimited string for YAML front-matter."""
    if not data:
        return ""
    entries = []
    for d in data:
        rate_str = f"{d['rate']:g}" if d.get("rate") is not None else ""
        entries.append("|".join([
            str(d.get("instrument", "")),
            str(d.get("tenor", "")),
            str(d.get("start_date", "")),
            f"{d.get('quantity', 0):g}" if d.get("quantity") is not None else "0",
            rate_str,
            str(d.get("end_date", "")),
        ]))
    return ";;".join(entries)


URLS = service_urls(SERVICE)


CATEGORY_CONFIGS: Dict[str, Dict[str, str]] = {
    CATEGORY_OMO_TRANSACTION: {
        "label": "公开市场业务交易公告",
        "list_base": URLS["omo_transaction_list_base"],
        "file_prefix": "pboc_omo_trans",
    },
    CATEGORY_OUTRIGHT_REPO: {
        "label": "公开市场买断式逆回购业务公告",
        "list_base": URLS["outright_repo_list_base"],
        "file_prefix": "pboc_outright_repo",
    },
}


@dataclass
class AnnouncementItem:
    category: str
    title: str
    detail_url: str
    list_page: int = 0
    pub_date: Optional[str] = None
    serial_year: Optional[str] = None
    serial_no: Optional[str] = None
    detail_slug: Optional[str] = None

    instruments_data: List[Dict] = field(default_factory=list)

    raw_body: str = ""
    parse_warnings: List[str] = field(default_factory=list)

    def md_filename(self) -> str:
        prefix = CATEGORY_CONFIGS[self.category]["file_prefix"]
        d = self.pub_date or "00000000"
        serial = self.serial_no or "0"
        y = self.serial_year or d[:4]
        slug = self.detail_slug or "x"
        return f"{prefix}_{d}_{y}_{serial}_{slug}.md"

    def to_markdown(self) -> str:
        lines = []
        lines.append("---")
        lines.append(f"category: {self.category}")
        lines.append(f"title: {self.title}")
        lines.append(f"detail_url: {self.detail_url}")
        lines.append(f"pub_date: {self.pub_date or ''}")
        if self.serial_year:
            lines.append(f"serial_year: {self.serial_year}")
        if self.serial_no:
            lines.append(f"serial_no: {self.serial_no}")
        lines.append(f"instruments_data: {_instruments_data_to_str(self.instruments_data)}")
        if self.parse_warnings:
            lines.append(f"parse_warnings: {self.parse_warnings!r}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {self.title}")
        lines.append("")
        lines.append(f"- Pub date: **{self.pub_date or 'n/a'}**")
        lines.append(f"- Detail: {self.detail_url}")
        lines.append("")
        lines.append("## Parsed instruments")
        lines.append("")
        if self.instruments_data:
            lines.append("| Instrument | Tenor | Start date | Quantity (亿元) | Rate (%) | End date |")
            lines.append("|------------|-------|------------|-----------------|----------|----------|")
            for d in self.instruments_data:
                rate_str = f"{d['rate']:g}" if d.get("rate") is not None else "-"
                lines.append(
                    f"| {d['instrument']} | {d.get('tenor', '')} | {d.get('start_date', '')} "
                    f"| {d.get('quantity', 0):g} | {rate_str} | {d.get('end_date', '')} |"
                )
        else:
            lines.append("(no instruments parsed)")
        if self.parse_warnings:
            lines.append("")
            lines.append("### Parse warnings")
            for w in self.parse_warnings:
                lines.append(f"- {w}")
        lines.append("")
        lines.append("## Raw body")
        lines.append("")
        lines.append("```")
        lines.append(self.raw_body.strip())
        lines.append("```")
        return "\n".join(lines) + "\n"
