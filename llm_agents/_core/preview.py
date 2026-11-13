"""llm_agents._core.preview — the METADATA + PREVIEW ENVELOPE every
large piece of data ships as when it enters an LLM conversation, and
the builder that produces it from anything a tool seam or message
builder hands it.

The STANDARD TEMPLATE (:class:`PreviewEnvelope`) replaces the former
``_guarded`` size-only stubs: an envelope is not just "this was big, a
taste follows" — it tells the reading model WHAT the thing is (text or
tabular data), HOW BIG (chars / rows×cols structure), WHAT IT HOLDS
(per-column dtype class + mean/std/min/max for numeric columns),
A TASTE OF THE CONTENT (head / mid / tail batches — three slices, not
just the head), and HOW TO READ THE REAL THING (``how_to_read`` names
the tool/path that fetches the substance — the harness's
"artifacts in files, references in context" principle at the request
seam: the LLM reads the envelope, then DECIDES WHAT TO FETCH).

Two content branches, chosen by sniffing (never trusted blindly; a
sniff failure degrades to the text branch):

* TEXT — char count + head/mid/tail char slices. (The former optional
  jev ``classification`` rider was REMOVED 2026-11-13: jev decision
  outputs stay out of every LLM context — the content reads record
  themselves as decision steps in ``llm.deep_agent_aux_steps``.)
* DATA — ``array[rows=N]×array[cols=M]`` structure, per-column dtype
  class + numeric stats (mean / std / min / max, population std over a
  documented sample cap — pure Python, no pandas in _core), and
  head / mid / tail ROW BATCHES.

Accepted data shapes: ``{columns: [...], rows: [[...], ...]}`` (the
readonly DB daemon's result shape), list-of-lists, list-of-dicts, and
a JSON-encoded string of any of those (sandbox stdout that printed a
table). Everything else is text.

Fail-soft everywhere: a builder problem returns a text envelope of the
``str()`` form, never raises. Layering: stdlib + pydantic only at
import; the same every-layer-may-import discipline as
``text_caps`` / ``errors``.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

__all__ = [
    "PREVIEW_KEY",
    "ColumnStats",
    "PreviewEnvelope",
    "PreviewBuilder",
]

# The in-place marker an envelope carries instead of the full payload:
# {"_preview": {...}} — replaces the former "_guarded" key. Distinct
# enough that the model reads it as a reference, not as data.
PREVIEW_KEY = "_preview"

# A sniffed JSON string is only trusted as data when it is at least
# this large — tiny strings stay text (a 40-char JSON scalar is prose).
_DATA_SNIFF_MIN_CHARS = 200

_DEFAULT_PREVIEW_ROWS = 5
_DEFAULT_PREVIEW_CHARS = 800
_DEFAULT_STATS_SAMPLE_ROWS = 1000


class ColumnStats(BaseModel):
    """One column of a data envelope: its dtype class and, for numeric
    columns, the population stats over the sampled rows."""

    name: str
    dtype_class: str = Field(
        description="'number' | 'text' | 'other'.")
    mean: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None


class PreviewEnvelope(BaseModel):
    """THE STANDARD TEMPLATE — what the LLM reads instead of a large
    payload (see module docstring). Render with :meth:`render`."""

    source: str = Field(
        description="Where the content came from: 'db' | 'sandbox' | "
                    "'context' | 'tool'.")
    kind: str = Field(
        description="'text' | 'data'.")
    total_chars: int = Field(
        description="The ORIGINAL payload's serialized size in chars.")
    elided_chars: int = Field(
        default=0,
        description="How many chars the envelope does NOT carry (total "
                    "minus what the preview batches hold).")
    how_to_read: str = Field(
        default="",
        description="The instruction naming the tool/path that fetches "
                    "the substance — the LLM decides what to read from "
                    "this.")
    # ---- the data branch ----
    structure: str = Field(
        default="", description="The data shape, e.g. "
        "'array[rows=500]×array[cols=7]' — '' for text envelopes.")
    columns: List[ColumnStats] = Field(
        default_factory=list,
        description="Per-column dtype class + numeric stats (data "
                    "branch only).")
    row_batches: Dict[str, List[List[Any]]] = Field(
        default_factory=dict,
        description="The preview batches keyed 'head' | 'mid' | 'tail' "
                    "— each up to preview_batch_rows rows (data branch "
                    "only).")
    # ---- the text branch ----
    char_count: int = Field(
        default=0,
        description="The text's character count (text branch only).")
    text_batches: Dict[str, str] = Field(
        default_factory=dict,
        description="The preview char slices keyed 'head' | 'mid' | "
                    "'tail' (text branch only).")
    # ---- provenance ----
    note: str = Field(
        default="", description="One line describing what was elided "
        "and why (also the log face).")

    def render(self) -> Dict[str, Any]:
        """The plain dict that ships inside a message: one ``_preview``
        key holding the envelope's fields (None/default empties
        dropped so a lean payload stays lean)."""
        body: Dict[str, Any] = {
            "source": self.source,
            "kind": self.kind,
            "total_chars": self.total_chars,
            "elided_chars": self.elided_chars,
        }
        if self.how_to_read:
            body["how_to_read"] = self.how_to_read
        if self.kind == "data":
            body["structure"] = self.structure
            body["columns"] = [c.model_dump() for c in self.columns]
            body["preview"] = self.row_batches
            body["omitted"] = (
                f"{self.elided_chars:,} of {self.total_chars:,} chars "
                "not shown — never read the full data into the "
                "conversation: compute over it in a sandbox script and "
                "read only the small computed result")
        else:
            body["char_count"] = self.char_count
            body["preview"] = self.text_batches
            body["omitted"] = (
                f"{self.elided_chars:,} of {self.total_chars:,} chars "
                "not shown — fetch the real text through your tools "
                "when needed")
        if self.note:
            body["note"] = self.note
        return {PREVIEW_KEY: body}

    def render_note(self) -> str:
        """The one-line note for the guard-notes block / the log."""
        if self.kind == "data":
            return (f"{self.source} — data {self.structure}, "
                    f"~{self.total_chars:,} chars — metadata + "
                    "head/mid/tail row batches kept")
        return (f"{self.source} — text ~{self.total_chars:,} chars — "
                "head/mid/tail char slices kept")

    def as_string(self) -> str:
        """The rendered envelope as the JSON text a tool-result seam
        ships (the loop's output is always a string)."""
        return json.dumps(self.render(), ensure_ascii=False)


def _is_number(value: Any) -> bool:
    """A numeric cell: int/float, but NOT bool (json round-trips keep
    bools bools) and NOT NaN/inf (stats stay finite)."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


def _column_cells(rows: List[List[Any]], idx: int) -> List[Any]:
    return [r[idx] for r in rows if isinstance(r, list) and len(r) > idx]


def _numeric_stats(cells: List[Any]) -> Tuple[Optional[float], ...]:
    """(mean, std, min, max) over the numeric cells — population std
    (ddof=0), pure Python. Empty/none-numeric column → all None."""
    values = [float(c) for c in cells if _is_number(c)]
    if not values:
        return None, None, None, None
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values)
    return (round(mean, 6), round(math.sqrt(var), 6),
            min(values), max(values))


def _dtype_class(cells: List[Any]) -> str:
    if cells and all(_is_number(c) for c in cells):
        return "number"
    if cells and all(isinstance(c, str) for c in cells):
        return "text"
    return "other"


def _rows_from_obj(obj: Any) -> Optional[Tuple[List[str],
                                              List[List[Any]]]]:
    """The (columns, rows) of a recognized TABULAR shape, else None:
    the readonly-daemon result dict ({columns, rows}), a list of
    equal-length lists (columns synthesized col0..colN), or a list of
    dicts (columns = the first row's keys, unioned from the head)."""
    if isinstance(obj, dict):
        cols = obj.get("columns")
        rows = obj.get("rows")
        if (isinstance(cols, list) and cols
                and isinstance(rows, list)
                and all(isinstance(r, (list, tuple)) for r in rows)):
            return [str(c) for c in cols], [list(r) for r in rows]
        return None
    if isinstance(obj, list) and obj:
        first = obj[0]
        if isinstance(first, (list, tuple)) and first:
            width = len(first)
            if all(isinstance(r, (list, tuple)) and len(r) == width
                    for r in obj):
                return ([f"col{i}" for i in range(width)],
                        [list(r) for r in obj])
            return None
        if isinstance(first, dict):
            cols: List[str] = []
            for r in obj[:20]:
                for k in r.keys():
                    if k not in cols:
                        cols.append(k)
            rows = [[r.get(k) for k in cols] for r in obj]
            return cols, rows
    return None


def _middle_slices(text: str, keep: int) -> Tuple[int, int, int]:
    """The (head, mid, tail) slice lengths for a char budget `keep`
    spent across three equal slices."""
    each = max(1, keep // 3)
    return each, each, each


def _shrink_row(row: List[Any], max_chars: int) -> List[Any]:
    """One previewed row kept navigable: each oversized CELL collapses
    to a truncated JSON string (a fat dict row must not bloat the
    envelope it previews)."""
    out: List[Any] = []
    for cell in row:
        if isinstance(cell, (str, int, float, bool)) or cell is None:
            if isinstance(cell, str) and len(cell) > max_chars:
                out.append(cell[:max_chars].rstrip() + " …")
            else:
                out.append(cell)
        else:
            text = json.dumps(cell, ensure_ascii=False, default=str)
            out.append(cell if len(text) <= max_chars
                       else text[:max_chars].rstrip() + " …")
    return out


class PreviewBuilder:
    """Sniffs content, builds the envelope (see module docstring). One
    instance per conversation/seam is typical — the knobs come from the
    caller's run limits so a builder is just a frozen config."""

    def __init__(
        self,
        *,
        preview_batch_rows: int = _DEFAULT_PREVIEW_ROWS,
        preview_chars: int = _DEFAULT_PREVIEW_CHARS,
        stats_sample_rows: int = _DEFAULT_STATS_SAMPLE_ROWS,
    ) -> None:
        self.preview_batch_rows = max(1, int(preview_batch_rows))
        self.preview_chars = max(50, int(preview_chars))
        self.stats_sample_rows = max(10, int(stats_sample_rows))

    # ------------------------------------------------------------ build
    def build(self, source: str, content: Any,
              how_to_read: str = "") -> PreviewEnvelope:
        """Anything → envelope. A JSON-encoded string of a tabular
        shape is sniffed as data (the sandbox stdout channel is
        text-only); a sniff/parse failure degrades to the text branch.
        Never raises."""
        try:
            rows_shape = None
            if isinstance(content, str):
                if len(content) >= _DATA_SNIFF_MIN_CHARS:
                    rows_shape = self._sniff_json(content)
            else:
                rows_shape = _rows_from_obj(content)
            if rows_shape is not None:
                return self.build_data(source, *rows_shape,
                                       total_chars=self._chars(content),
                                       how_to_read=how_to_read)
            text = (content if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False,
                                    default=str))
            return self.build_text(source, text, how_to_read=how_to_read)
        except Exception:  # noqa: BLE001 — the fail-soft contract
            text = content if isinstance(content, str) else str(content)
            return self.build_text(source, text, how_to_read=how_to_read)

    def build_data(
        self, source: str, columns: List[str], rows: List[List[Any]],
        *, total_chars: int = 0, how_to_read: str = "",
    ) -> PreviewEnvelope:
        """The DATA envelope: structure, per-column stats over the
        sampled rows, head/mid/tail row batches."""
        if total_chars <= 0:
            total_chars = self._chars({"columns": columns, "rows": rows})
        sample = rows[: self.stats_sample_rows]
        cols: List[ColumnStats] = []
        for i, name in enumerate(columns):
            cells = _column_cells(sample, i)
            klass = _dtype_class(cells)
            stats: Tuple[Optional[float], ...] = (None,) * 4
            if klass == "number":
                stats = _numeric_stats(cells)
            cols.append(ColumnStats(
                name=str(name), dtype_class=klass,
                mean=stats[0], std=stats[1],
                min=None if stats[2] is None else round(stats[2], 6),
                max=None if stats[3] is None else round(stats[3], 6)))
        n = len(rows)
        k = self.preview_batch_rows
        cell_budget = max(50, self.preview_chars // max(1, len(columns)))
        batches: Dict[str, List[List[Any]]] = {}
        if n > 0:
            mid_start = max(k, (n - k) // 2)
            batches = {
                "head": [_shrink_row(r, cell_budget) for r in rows[:k]],
                "mid": ([_shrink_row(r, cell_budget)
                         for r in rows[mid_start: mid_start + k]]
                        if n > 2 * k else []),
                "tail": ([_shrink_row(r, cell_budget)
                          for r in rows[-k:]]
                         if n > k else []),
            }
            # tail overlapping head for tiny tables → keep only head
            if n <= k:
                batches = {"head": [_shrink_row(r, cell_budget)
                                    for r in rows[:k]],
                           "mid": [], "tail": []}
        envelope = PreviewEnvelope(
            source=source, kind="data", total_chars=total_chars,
            elided_chars=max(0, total_chars - self._chars(batches)),
            how_to_read=how_to_read,
            structure=f"array[rows={n}]×array[cols={len(columns)}]",
            columns=cols, row_batches=batches)
        envelope.note = envelope.render_note()
        return envelope

    def build_text(
        self, source: str, text: str, how_to_read: str = "",
    ) -> PreviewEnvelope:
        """The TEXT envelope: char count, head/mid/tail char slices."""
        total = len(text)
        head_n, mid_n, tail_n = _middle_slices(text, self.preview_chars)
        batches: Dict[str, str] = {
            "head": text[:head_n],
            "mid": text[(total - mid_n - tail_n + tail_n) // 2:
                        (total - mid_n - tail_n + tail_n) // 2 + mid_n]
            if total > head_n + mid_n + tail_n else "",
            "tail": text[-tail_n:] if total > tail_n else "",
        }
        envelope = PreviewEnvelope(
            source=source, kind="text", total_chars=total,
            elided_chars=max(0, total - len("".join(batches.values()))),
            how_to_read=how_to_read, char_count=total,
            text_batches=batches)
        envelope.note = envelope.render_note()
        return envelope

    # ------------------------------------------------------------ utils
    @staticmethod
    def _chars(obj: Any) -> int:
        try:
            return len(json.dumps(obj, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return len(str(obj))

    def _sniff_json(self, text: str) -> Optional[Tuple[List[str],
                                                       List[List[Any]]]]:
        """A JSON-encoded tabular shape, else None (any parse failure
        is just "this is text" — the sandbox stdout channel is text,
        and a traceback that happens to start with '{' must not become
        a table)."""
        stripped = text.lstrip()
        if not stripped[:1] in ("{", "["):
            return None
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return None
        return _rows_from_obj(obj)
