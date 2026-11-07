"""sql_views — render the SELECT view templates under ``database/sql_views/``.

See ``database/sql_views/README.md`` for the convention: templates carry
``{{slot}}`` placeholders; value args bind to ``$n`` positional params
(the same slot reused maps to the same ``$n``), :class:`SqlFragment` args
substitute verbatim. Rendering is strict — every slot needs an arg and
every arg must appear as a slot, so typos fail fast at render instead of
silently changing the query. Templates re-read on mtime change.

Callers feed the returned ``(sql, params)`` pair to their existing
``_common.db_commons`` read path. ``param_offset`` shifts the ``$n``
numbering when the rendered text is composed into a wrapper statement
that already binds earlier params.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from _common.data_definitions import (
    RenderedSqlView,
    SqlFragment,
)
SQL_VIEWS_DIR = Path(__file__).resolve().parents[1] / "database" / "sql_views"

_SLOT_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")




def frag(text: str) -> SqlFragment:
    """Wrap raw SQL text to pass it as a fragment arg."""
    return SqlFragment(text)




_cache: dict[Path, tuple[float, str]] = {}


def _template_text(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid sql view name: {name!r}")
    path = SQL_VIEWS_DIR / f"{name}.sql"
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    text = path.read_text(encoding="utf-8")
    _cache[path] = (mtime, text)
    return text


def render_sql_view(
    name: str,
    /,
    *,
    param_offset: int = 0,
    **args: Any,
) -> RenderedSqlView:
    """Render the named view template with ``args`` filling its slots."""
    template = _template_text(name)
    params: list[Any] = []
    index_by_arg: dict[str, int] = {}
    used: set[str] = set()

    def _sub(match: re.Match[str]) -> str:
        arg_name = match.group(1)
        if arg_name not in args:
            raise ValueError(
                f"sql view {name!r}: missing arg {arg_name!r} "
                f"(got: {', '.join(args) or 'none'})"
            )
        value = args[arg_name]
        used.add(arg_name)
        if isinstance(value, SqlFragment):
            return value.text
        idx = index_by_arg.get(arg_name)
        if idx is None:
            params.append(value)
            idx = param_offset + len(params)
            index_by_arg[arg_name] = idx
        return f"${idx}"

    sql = _SLOT_RE.sub(_sub, template)
    unknown = [k for k in args if k not in used]
    if unknown:
        raise ValueError(
            f"sql view {name!r}: args not in template: {', '.join(unknown)}"
        )
    return RenderedSqlView(sql=sql, params=tuple(params))
