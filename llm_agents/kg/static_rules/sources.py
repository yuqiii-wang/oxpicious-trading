"""llm_agents.kg.static_rules.sources — the COMMON local-directory
scan-and-read face of the static rules: one module any rule uses to
turn a corpus directory of files into typed :class:`SourceFile` records,
so every rule's corpus access looks the same and none of them re-rolls
path walking / encoding / leading-comment harvesting.

A "source" is a plain local directory scanned recursively for one file
pattern (today: the ``database/sql_views/`` corpus — the read-side
SELECT templates the sql_views loaders render by name). Each scanned
file becomes a :class:`SourceFile` whose ``name`` is the slash path
relative to the scan root WITHOUT the extension — exactly the loader
name the corpus's own consumers use (``render_sql_view(name)`` /
``renderSqlView(name)``) and, for the sql_views corpus, the KG entity
name the rule seeds. ``leading_comment`` keeps the file's leading
``--`` block with the markers stripped — the grounded prose a rule
harvests descriptions from (the ns2 generator's ``harvest_prose``
principle: reuse the corpus's own words, never invent).

Future static rules (e.g. a rules pass over another seed-adjacent
directory) reuse this module unchanged — scan a different root, parse
their own facts; only the corpus-specific interpretation lives in the
rule."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class SourceFile(BaseModel):
    """One scanned corpus file — its loader name, where it lives, its
    text, its leading ``--`` comment block (markers stripped), and its
    mtime (the freshness fingerprint's input)."""

    name: str
    """Slash path relative to the scan root without the extension —
    the corpus's loader name (and, for sql_views, the KG entity name)."""

    path: Path
    """The file's absolute path (kept for warnings, never seeded)."""

    text: str
    """The file's full UTF-8 text (the parse input)."""

    leading_comment: str = ""
    """The leading ``--`` block, markers stripped, lines joined with
    ``\\n`` — empty when the file opens with code."""

    mtime: float = 0.0
    """The file's ``st_mtime`` (epoch seconds), captured by the same
    walk that read it — a scan is its own fingerprint source."""

    @property
    def stem(self) -> str:
        """The name's last segment (``index_margin_series`` for
        ``analysis/margins/fetch/index_margin_series``)."""
        return self.name.rsplit("/", 1)[-1]

    @property
    def path_segments(self) -> tuple[str, ...]:
        """The name's path segments (the corpus's own location
        vocabulary — a mechanical tag source for rules)."""
        return tuple(self.name.split("/"))


class SqlSourceScan(BaseModel):
    """One directory scan — the root and every matching file, sorted by
    name so a rule's drafts (and the DB rows they upsert) are produced
    in a deterministic order."""

    root: Path
    files: tuple[SourceFile, ...] = Field(default=())

    def __len__(self) -> int:
        return len(self.files)

    @property
    def file_count(self) -> int:
        """The corpus's file count — half of the sync fingerprint."""
        return len(self.files)

    @property
    def max_mtime(self) -> float:
        """The corpus's MAX file mtime (0.0 on an empty scan) — the
        other half; a file edited after the last sync moves this."""
        return max((f.mtime for f in self.files), default=0.0)


class SourceStat(BaseModel):
    """A corpus's bare fingerprint — the count and newest mtime its
    ``.sql`` (or pattern) files stat at, with NO content read. The
    freshness gate's cheap per-load check (a few hundred ``stat()``
    calls); the writer stamps the full-scan values as
    ``llm.kg_rule_syncs``."""

    root: Path
    file_count: int
    max_mtime: float


class SqlDirSource:
    """The recursive scanner behind every static rule's corpus access:
    ``scan`` walks *root* for ``*.sql`` (or any pattern), reads each
    file UTF-8, and harvests its leading ``--`` block (plus each
    file's mtime — a scan doubles as its fingerprint source);
    ``stats`` walks the same tree WITHOUT reading content — the
    read-time freshness gate's ms-cheap check. Deterministic — sorted
    by loader name; a file that vanished between the walk and the read
    is skipped, not fatal (the corpus is a moving tree)."""

    def scan(self, root: Path, *, pattern: str = "*.sql") -> SqlSourceScan:
        if not root.exists():
            return SqlSourceScan(root=root)  # corpus not populated yet
        paths = sorted(p for p in root.rglob(pattern) if p.is_file())
        files: list[SourceFile] = []
        for path in paths:
            try:
                mtime = path.stat().st_mtime
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            files.append(SourceFile(
                name=path.relative_to(root).with_suffix("").as_posix(),
                path=path,
                text=text,
                leading_comment=self._leading_comment(text),
                mtime=mtime,
            ))
        return SqlSourceScan(root=root, files=tuple(files))

    def stats(self, root: Path, *, pattern: str = "*.sql") -> SourceStat:
        """The corpus's bare fingerprint — file count + MAX mtime, stat
        only (never a content read; this runs on every graph reload).
        An absent corpus is an empty fingerprint (count 0)."""
        max_mtime = 0.0
        count = 0
        if root.exists():
            for path in root.rglob(pattern):
                if not path.is_file():
                    continue
                try:
                    max_mtime = max(max_mtime, path.stat().st_mtime)
                    count += 1
                except OSError:
                    continue
        return SourceStat(root=root, file_count=count, max_mtime=max_mtime)

    @staticmethod
    def _leading_comment(text: str) -> str:
        """The file's leading ``--`` block with the markers stripped —
        stops at the first line that is neither ``--``-prefixed nor
        blank (a blank line only continues the block when more comment
        lines follow, so ``-- header\\n\\nSELECT …`` still harvests)."""
        lines: list[str] = []
        pending_blank = False
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped.startswith("--"):
                if pending_blank and lines:
                    lines.append("")
                pending_blank = False
                lines.append(stripped.removeprefix("--").strip())
                continue
            if not stripped and not lines:
                continue  # leading blank lines before the header
            if not stripped:
                pending_blank = True
                continue
            break  # first code line — the block is over
        return "\n".join(lines).strip()
