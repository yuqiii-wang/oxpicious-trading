# Skill: kg-db-semantics
# kg-db-semantics — the static knowledge graphs (ns 0 DB semantics, ns 1 prompts)

How to author the knowledge graphs where the database and the prompt
catalog describe themselves
(`llm.kg_entities` / `llm.kg_edges`, DDL `database/sql/llm/13_kg_hypergraph.sql`,
seeds `database/sql/llm/static/knowledge_graphs/`). Use for ANY
addition/extension of KG entities or edges — table semantics, table
relationships, prompt-catalog skills, the `kg_namespace` vocabulary —
even when the user just says "add these tables to the KG" or "add an
edge".

## Purpose

Namespace 0 is the built-in **DB-semantics subgraph**: one entity per
table, edges for how tables relate. Namespace 1 (2026-11-05) is the
**prompt-catalog subgraph**: one entity per cataloged `llm.ai_prompts`
row (prompt_type 1 skills + 2 plan — the prompt-loading ladder's
catalog universe), edges for how prompts relate. Both ground the
deep-agent KG tools (`llm_agents/kg/` — the independent KG module
behind `kg_search`/`kg_expand` with
a `namespace` argument): a walker answers "which tables hold OHLC",
"what feeds closes into the RSI study", "which skill teaches 估值"
without reading DDL or fetching prompts wholesale. `kg_namespace` is
deliberately NOT registered in `label_dim` until the first KG writer
ships (registering = three mirrors: `static/label_dim/07_text_llm.sql`
+ `_common/labels.py` + `data_viz/api/services/label-codes.ts`); the
shipped codes (0 DB semantics | 1 prompts) live in the DDL header and
the namespace column comments. Edges NEVER span namespaces (the DDL
contract) — "which tables a skill teaches" is answered by searching ns
0, not by a cross-namespace edge.

## Entity contract (`llm.kg_entities`)

- ONE ENTITY = ONE TABLE (or view). `entity_type` `'table'` / `'view'`;
  `name` = schema-qualified name (`stats.index_basic_stats`) — unique per
  `(namespace, entity_type, name)`.
- `description` = ONE sentence of BUSINESS meaning. Never ops (upserts,
  `--force`, rebuilds), never implementation (keys, FKs, partitions,
  indexes), never UI.
- `tags TEXT[]` = business-domain terms + how queries use the table, as a
  trader names them — in BOTH English and the natural Chinese term,
  interleaved (`{index, 指数, ohlcv, K线, trading amount, 成交额}`);
  `identity` / `标识`, `reference` / `参考` for lookup-anchor tables.
  Skip the Chinese duplicate when the term is the same acronym in Chinese
  usage (ETF, RSI, ema). No UI, no ops, no storage mechanics. GIN-indexed
  (`ix_kg_entities_tags_gin`) for `tags @>` containment — so both
  `ARRAY['K线']` and `ARRAY['ohlcv']` hit.

## Edge contract (`llm.kg_edges`)

Head-first (`entity_ids[0]` = subject). `relation` keeps the plain VERB;
`sub_relation` carries the narrowed semantics as a PREPOSITIONAL PHRASE
(`for_…` / `about_…`), NULL = bare verb. Both are open TEXT vocabulary —
coin new values freely, but only after verifying the fact against DDL /
pipeline docs.

| relation | sub_relation | reads as |
| --- | --- | --- |
| `computed_from` | `for_close` / `for_ohlcv` / `for_pe` / `for_moving_average` / `for_identity` / `for_adjusted_close` / `for_regime` | derived table → source table, and WHICH of the source's columns/facets it takes |
| `anchored_by` | `for_code_date` | fact/reference table → its identity table, keyed (code, date) |
| `similar_to` | `about_<family>` | analysis tables that both indicate the same concept (`about_momentum`); SYMMETRIC — seed one direction, walkers traverse either way |
| `joins_on` | `for_code_date` / `for_code` | two PEER tables commonly JOINed in practice, and the KEY: `for_code_date` = ON a.code = b.code AND a.date = b.date, `for_code` = ON cls.code = h.code (classification routing). SYMMETRIC. ONLY joins the repo's own SQL actually writes — every edge cites its file (see 17_sql_join_paths.sql's header); identity-grain joins are `anchored_by`'s own contract, never duplicated here |

`similar_to` needs both endpoints to be analysis tables of the SAME
family; the family must be checkable in the tables' biz docs.

## Namespace 1 — the prompt catalog (`prompts/01_prompt_catalog.sql`)

- ONE ENTITY = ONE CATALOGED PROMPT ROW of `llm.ai_prompts`
  (prompt_type 1 skills + 2 plan ONLY — tool/system/internal rows are
  not catalog entities: tool descriptions already ride their tool
  schemas). `name` = `prompt_key`; `entity_type` `'skill'` (type 1) |
  `'plan'` (type 2).
- `description` = the row's `preview` VERBATIM (the authored one-line
  face — never re-authored in the seed). `tags` = the row's LABELS
  verbatim (bound ∪ optional — the authored bilingual findability
  vocabulary) + its `prompt_sub_type` +, for unlabeled rows, a few
  terms taken from the preview/title text itself.
- Edge vocabulary (same verb + prepositional-phrase shape):
  `similar_to / about_<domain>` = two prompts teaching the SAME
  domain, grounded ONLY in authored structure — the same
  `static/ai_prompts/` seed-file family, parallel "how to read"
  previews, or labels naming the same concept; SYMMETRIC.
  `alternates_with / about_study_scope` = rows the picker offers as
  ONE mutually exclusive choice (the four goal rows — the
  09_plan_prompts.sql header's own contract).
- Population lives with the catalog: a row re-keyed or deactivated in
  `ai_prompts` needs this seed edited in the same change (static seeds
  are the source of record; entity DELETE stays a temp_scripts
  one-time job).

## Seed files

Numbered per scope under `database/sql/llm/static/knowledge_graphs/`
(ns 0 in `db_semantics/`, ns 1 in `prompts/`; e.g. `01_…` index daily
chain, `02_…` ETF/stock chains). Each file is re-appliable and
idempotent:

1. Header comment: what the namespace's conventions are (point at this
   skill), what THIS file's scope is.
2. Entities: one `INSERT … ON CONFLICT (namespace, entity_type, name) DO
   UPDATE SET description = EXCLUDED.description, tags = EXCLUDED.tags`.
   Never touch `entity_id` / `created_at` — stable ids are what edges
   reference.
3. Edges: `INSERT … SELECT` resolving member ids BY NAME (join
   `llm.kg_entities` on `namespace = N AND name = e.subject/object` — no
   entity_type filter, views must match too), guarded by `NOT EXISTS` on
   `(relation, sub_relation IS NOT DISTINCT FROM, entity_ids)` — the
   IS NOT DISTINCT FROM is what keeps NULL sub_relations re-run-safe.
4. Superseded shapes: the canonical seed never carries one-time DELETEs —
   old-shape rows already applied to a live DB are removed ONCE by a
   `temp_scripts/` apply script (see below), never parked in the seed.

## Authoring flow

1. Read the table's `COMMENT ON TABLE` / DDL header (and the producing
   pipeline's docstring for derivations) BEFORE writing its sentence and
   tags — never invent biz semantics. For ns 1, read the prompt row's
   preview/labels/seed-file family BEFORE adding edges — the grounding
   list for every ns-1 edge lives in the seed file's header.
2. Write the seed file.
3. Apply + verify from a `temp_scripts/` script (WSL conda per
   `oxpicious-dev-conventions`): apply ALL `knowledge_graphs/**/*.sql` files
   in order, then print every namespace's entities and edges (decoded by
   joining `entity_ids[1]` / `entity_ids[2]` to names). Re-run must be a
   no-op: same rows, no new edges. The standing script is
   `temp_scripts/apply_kg.py` (DDL + seeds + verify in one pass) — reuse
   it, recreating it if it has been cleaned.
4. Add the file's scope to the seed-dir coverage; done. The schema files
   (`13_kg_hypergraph.sql`) only change when the CONTRACT changes
   (columns, indexes, comments, the namespace vocabulary) — never for
   data.

Persistent Python around the KG (`llm_agents/kg/` — engine, loaders,
chain-cache service) is
class-first pydantic per `oxpicious-dev-conventions`; the seed SQL files
and the one-time `temp_scripts/` apply scripts keep their script shape.
