"""``python -m llm_agents.kg.static_rules`` — sync the static rules'
KG corpora into ``llm.kg_entities`` / ``llm.kg_edges``.

Default run: incremental sync (stale rule-owned rows pruned, drafts
upserted, derived edges replaced) — idempotent, re-run any time a
corpus changes. ``--force``: the standing purge-then-rebuild convention
(a fully derived rule converges to the same state either way; the flag
makes the purge explicit). No cudf activation: this module graph has
no pandas anywhere (asyncpg + regex only)."""
from __future__ import annotations

import argparse
import asyncio
from typing import List, Optional

from llm_agents.kg.static_rules.data_definitions import StaticRulesReport


async def _sync(force: bool) -> List[StaticRulesReport]:
    # Lazy imports — the activation-order rule for _common, and the
    # runner stays the only entry that needs the DB layer.
    from _common.db_commons import (
        close_pools_async, get_db_connection_async,
    )
    from llm_agents.kg.static_rules import RULES, StaticRulesWriter

    conn = await get_db_connection_async()
    try:
        writer = StaticRulesWriter()
        return [await rule.run(conn, writer, force=force) for rule in RULES]
    finally:
        await conn.close()
        try:
            await close_pools_async()
        except Exception:  # noqa: BLE001 — pool teardown is best-effort
            pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync the KG static rules' corpora (ns0 sql_views "
                    "view entities + reads_from edges) into "
                    "llm.kg_entities / llm.kg_edges.")
    parser.add_argument(
        "--force", action="store_true",
        help="purge the rules' own rows first, then rebuild (the sync "
             "is already fully derived and idempotent)")
    for report in asyncio.run(_sync(parser.parse_args(argv).force)):
        print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
