"""llm_agents.deep_agents.core.authorization.models — the authorization
module's typed payloads (2026-11-13).

ONE permission object, ONE context object — the whole module's data
vocabulary:

- :class:`TablePermission` — the effective read scope as one JSON-able
  value (the ``llm.deep_agent_authorizations.permission`` JSONB's typed
  face): a schema-qualified table allowlist UNION a schema allowlist,
  plus the node-extension provenance (the KG-located delta and the study
  focus that earned it). ``allowed(table) <=> table IN tables OR
  schema(table) IN schemas``.
- :class:`AuthorizationContext` — what a running agent conversation
  carries: the run's chain id, the node's state id (None = the thread
  temp-user scope) and the RESOLVED permission. Inheritance is this
  object flowing down the spawn tree (a child without an extension row
  gets its parent's context verbatim).

pydantic frozen models per the repo's class-first convention; stdlib +
pydantic only.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, field_validator

# The standing schema allowlist — the SAME set the AI-summary bridge
# sends as forced_schemas (KG_SUMMARY_SCHEMAS) and the default scope for
# runs that carry no UI table picks (daemon/CLI/test runs): the study
# stores, never the operational ones (llm / monitor / static / live —
# which the shared readonly_analytics role CAN read; the gate is what
# keeps agent SQL out of them). analysis_signals joined 2026-11-18 —
# the SIGNAL AMPLIFICATION tier (signal_amplification_rules/_history)
# joined the study corpus beside analysis (the replayed-history tables
# were already reachable through the strategy tier's own reads; the
# agent's kg_search surfaces the new tables and the gate must follow).
DEFAULT_STANDING_SCHEMAS: Tuple[str, ...] = (
    "analysis", "analysis_signals", "strategy", "stats", "text",
    "multi_media",
)

SOURCE_UI_INIT = "ui_init"
SOURCE_KG_EXTENSION = "kg_extension"


def _normalize_names(names: Tuple[str, ...]) -> Tuple[str, ...]:
    """Lowercase, strip, dedupe — keep first-seen order (stable
    rendering for the stored JSON and the tests)."""
    seen: Dict[str, None] = {}
    for name in names:
        key = str(name).strip().lower()
        if key:
            seen.setdefault(key, None)
    return tuple(seen)


class TablePermission(BaseModel):
    """One effective read scope — the permission JSONB's typed face.

    ``tables`` schema-qualified allowlist; ``schemas`` schema allowlist;
    ``added_tables`` / ``focus`` — the node-extension provenance (empty /
    None on the thread temp-user row); ``source`` ``ui_init`` (the run's
    init scope) or ``kg_extension`` (a study-focused node's widened
    scope — the parent UNION the KG-located delta, materialized at
    write time so resolution is one row read).
    """

    model_config = ConfigDict(frozen=True)

    tables: Tuple[str, ...] = ()
    schemas: Tuple[str, ...] = ()
    added_tables: Tuple[str, ...] = ()
    focus: Optional[str] = None
    source: str = SOURCE_UI_INIT

    @field_validator("tables", "schemas", "added_tables")
    @classmethod
    def _norm(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalize_names(tuple(value))

    # -- the one predicate ---------------------------------------------

    def allows(self, name: str) -> bool:
        """Is this (schema-qualified) relation inside the scope? A BARE
        name matches only the tables list (schemas cannot vouch for an
        unqualified reference); callers pass qualified names."""
        key = str(name).strip().lower()
        if not key:
            return False
        if key in self.tables:
            return True
        schema = key.split(".", 1)[0] if "." in key else ""
        return bool(schema) and schema in self.schemas

    # -- the one derivation --------------------------------------------

    def extend(self, tables: Tuple[str, ...],
               focus: str) -> "TablePermission":
        """The EXTENSION derivation (extend, never narrow): this scope
        UNION the new tables, provenance carried in added_tables /
        focus. Deterministic: the union is re-normalized, the delta is
        what the extension actually ADDED TO THE SCOPE (a table already
        allowed via schemas adds nothing)."""
        new = _normalize_names(tuple(tables))
        merged = _normalize_names(self.tables + new)
        delta = tuple(t for t in merged if not self.allows(t))
        return TablePermission(
            tables=merged,
            schemas=self.schemas,
            added_tables=delta,
            focus=(focus or "").strip() or None,
            source=SOURCE_KG_EXTENSION,
        )

    # -- JSON round-trip -------------------------------------------------

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_doc(cls, doc: Any) -> "TablePermission":
        """Tolerant reader for a stored permission JSONB (asyncpg hands
        JSONB back as text): a str parses, a dict validates directly,
        anything else degrades to the empty scope — callers treat that
        as deny-by-default."""
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except ValueError:
                doc = {}
        if not isinstance(doc, dict):
            doc = {}
        return cls.model_validate({
            "tables": doc.get("tables") or (),
            "schemas": doc.get("schemas") or (),
            "added_tables": doc.get("added_tables") or (),
            "focus": doc.get("focus"),
            "source": doc.get("source") or SOURCE_UI_INIT,
        })


class AuthorizationContext(BaseModel):
    """What one agent conversation carries: the run's chain id, the
    node's state id (None = the thread temp-user scope — the fallback
    every un-rowed node shares) and the RESOLVED permission. This object
    IS the inheritance mechanism: a spawn without an extension row
    receives its parent's context verbatim."""

    model_config = ConfigDict(frozen=True)

    super_thread_id: str
    node_id: Optional[int] = None
    permission: TablePermission

    @classmethod
    def for_thread(cls, super_thread_id: str,
                   permission: TablePermission) -> "AuthorizationContext":
        return cls(super_thread_id=super_thread_id, node_id=None,
                   permission=permission)

    @classmethod
    def for_node(cls, super_thread_id: str, node_id: int,
                 permission: TablePermission) -> "AuthorizationContext":
        return cls(super_thread_id=super_thread_id, node_id=int(node_id),
                   permission=permission)

    @property
    def key(self) -> str:
        """The (chain, node) identity as one comparable token."""
        return f"{self.super_thread_id}:{self.node_id if self.node_id is not None else '-'}"

    def describe(self) -> str:
        """One human line for prompts/logs (bounded: table counts, not
        the lists)."""
        bits = [f"{len(self.permission.tables)} tables",
                f"schemas {'+'.join(self.permission.schemas) or '-'}"]
        if self.node_id is not None:
            bits.append(f"node {self.node_id}")
        if self.permission.focus:
            bits.append(f"focus {self.permission.focus[:40]}")
        return f"[scope] {' · '.join(bits)}"
