"""sql_writes — load the write action templates under ``database/sql_writes/``.

The write-side twin of :mod:`_common.sql_views`: custom parameterized
writes (INSERT / UPDATE / DELETE the pipelines and services issue
directly, as opposed to the row-dict writes that go through the
``_common.db_commons`` bulk helpers) live as action files in that tree,
never inline in logic — see ``database/sql_writes/README.md`` for the
layout. Actions carry plain ``$n`` positional placeholders, so ONE file
serves every executor that speaks the Postgres wire protocol: the
data_viz API's node-postgres (``loadSqlWrite`` in
``data_viz/api/services/sql-writes.service.ts``) and Python asyncpg
(here). The header comment documents every parameter; the SQL body is
executed verbatim (comments and all — Postgres ignores them).

Two loaders, matched to the call shape:

- :func:`render_sql_write` — the checked path for one-statement calls
  (``execute`` / ``fetchval`` / ``fetchrow``): binds *params
  positionally and verifies the action's highest ``$n`` equals the arg
  count (placeholder gaps raise too), so a wiring bug fails here with
  the action's name instead of as a confusing asyncpg arity error
  mid-transaction. Feed the returned ``(sql, params)`` pair to the
  existing db_commons write path.
- :func:`load_sql_write` — the raw text for ``executemany`` batches
  (the per-row args vary, asyncpg validates each row itself).

Files re-read on mtime change (the sql_views contract): an edited
action applies without a process restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from _common.data_definitions import (
    RenderedSqlWrite,
)
SQL_WRITES_DIR = Path(__file__).resolve().parents[1] / "database" / "sql_writes"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
_PARAM_RE = re.compile(r"\$(\d+)")

_cache: dict[Path, tuple[float, str]] = {}


def _action_text(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid sql write action name: {name!r}")
    path = SQL_WRITES_DIR / f"{name}.sql"
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    text = path.read_text(encoding="utf-8")
    _cache[path] = (mtime, text)
    return text


def load_sql_write(name: str) -> str:
    """One action's SQL text verbatim — the executemany form (asyncpg
    validates each row's arity itself)."""
    return _action_text(name)




def render_sql_write(name: str, /, *params: Any) -> RenderedSqlWrite:
    """Load the named write action and bind *params* positionally.

    Strict like the sql_views renderer: the action's highest ``$n`` must
    equal ``len(params)`` and every ``$1..$n`` must appear (in the SQL
    body — the ``--`` header comment's parameter doc does not count), so
    a file/call-site drift fails here, named, before any DB round trip.
    """
    text = _action_text(name)
    body = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("--")
    )
    indexes = {int(m.group(1)) for m in _PARAM_RE.finditer(body)}
    expected = max(indexes, default=0)
    gaps = [i for i in range(1, expected + 1) if i not in indexes]
    if gaps:
        raise ValueError(
            f"sql write {name!r}: placeholder gap(s) "
            f"{', '.join(f'${i}' for i in gaps)} (highest ${expected})")
    if expected != len(params):
        raise ValueError(
            f"sql write {name!r}: {len(params)} arg(s) passed, "
            f"action binds ${expected}")
    return RenderedSqlWrite(sql=text, params=params)
