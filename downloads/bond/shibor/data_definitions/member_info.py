"""MemberInfo — data definition.

Migrated verbatim from :mod:`downloads.bond.shibor.__main__` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemberInfo:
    mem_code: str
    instn_cn_nm: str
    instn_en_nm: str
