# Skill: kg-db-semantics
# kg-db-semantics — the static knowledge graphs (ns 0 DB semantics, ns 1 prompts, ns 2 data & labels)

How to author the knowledge graphs where the database and the prompt
catalog describe themselves
(`llm.kg_entities` / `llm.kg_edges`, DDL `database/sql/llm/kg_entities.sql` +
`kg_edges.sql`,
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
catalog universe), edges for how prompts relate. Namespace 2
(2026-11-08) is the **data & labels subgraph**: one entity per
`static.label_dim` SMALLINT vocabulary plus the tables its typed
columns live in — the code-word lane ("macd", "buy/sell", "CALL/PUT")
that locates tables plain table vocabulary misses. All ground the
deep-agent KG tools (`llm_agents/kg/` — the independent KG module
behind `kg_search`/`kg_expand` with
a `namespace` argument; the DEFAULT kg_search lane searches ns 0 AND
ns 2 together): a walker answers "which tables hold OHLC",
"what feeds closes into the RSI study", "which skill teaches 估值",
"which tables carry the macd family code"
without reading DDL or fetching prompts wholesale. `kg_namespace` is
deliberately NOT registered in `label_dim` until the first KG writer
ships (registering = three mirrors: `static/label_dim/07_text_llm.sql`
+ `_common/labels.py` + `data_viz/api/services/label-codes.ts`); the
shipped codes (0 DB semantics | 1 prompts | 2 data & labels) live in
the DDL header and the namespace column comments. Edges NEVER span
namespaces (the DDL
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
| `joins_on` | `for_code_date` / `for_code` / `for_contract_code_date` | two PEER tables commonly JOINed in practice, and the KEY: `for_code_date` = ON a.code = b.code AND a.date = b.date, `for_code` = ON cls.code = h.code (classification routing), `for_contract_code_date` = ON a.contract_code = b.contract_code AND a.date = b.date (the options chain's hub↔satellite fact joins — the satellites→identity grain stays `anchored_by`). SYMMETRIC. ONLY joins the repo's own SQL actually writes — every edge cites its file (see 17_sql_join_paths.sql's header); identity-grain joins are `anchored_by`'s own contract, never duplicated here |

`similar_to` needs both endpoints to be analysis tables of the SAME
family; the family must be checkable in the tables' biz docs.

## The ns0 Python static rules (`llm_agents/kg/static_rules/` — 2026-11-09)

ns0's rows come from TWO corpus rules (plus the hand seeds for what
doesn't fit a rule), both synced by `python -m llm_agents.kg.static_rules`
(WSL conda; `--force` = purge+rebuild; idempotent, prune on corpus
diff, one transaction, all SQL from `database/sql_writes/llm_agents/
kg/static_rules/` action files) and both re-synced at read time by the
freshness gate below. The hand seeds (`db_semantics/*.sql`) keep ONLY
the edge INSERTs (`computed_from` / `anchored_by` / `similar_to` /
`joins_on`) and the DB `view` entities (`stats.v_*`) — every authored
TABLE payload moved out of the seeds on 2026-11-09.

### `db_semantics_tables` — the authored tables (the DDL files themselves)

- ONE TABLE = ONE FILE under `database/sql/` (filename = the table
  name; the entity name comes from the file's own qualified
  ``CREATE TABLE`` statement); payload = two lines in the file's
  LEADING ``--`` banner — `-- description: <one business sentence>` and
  `-- tags: <comma-split bilingual trader vocabulary>` (the seed
  contract's tags, migrated verbatim 2026-11-09 into the DDL 2026-11-10,
  the former `database/kg_tables/` parallel corpus deleted). Adding a
  table to the KG = adding the payload lines to its own DDL file's
  banner; the freshness gate picks it up. A scanned file with no
  ``CREATE TABLE`` is not a table file; a file declaring several tables
  breaks the invariant and is reported (never seeded); a table file
  WITHOUT a `-- description:` line is unauthored — no entity, not
  owned (the mechanical fallback's business).
- The rule (`TableSemanticsRule`, `db_semantics_tables.py`) resolves
  every name against pg_class (a header for a dropped relation is
  skipped and counted, never seeded blind) and UPSERTS the rows as
  OWNED: the ownership ledger `llm.kg_rule_entities (rule_name,
  entity_name)` (DDL `kg_rule_syncs.sql`) records the names each
  sync seeded; a payload deleted (or its table dropped) → the prune
  deletes the entity AND every edge touching it (no ghost endpoints).
  The DDL banner is the source of record — edit the file, not the DB.

### `sql_view_semantics` — the read-side query corpus (`database/sql_views/`)

- ONE ENTITY = ONE TEMPLATE: `entity_type` `'view'`, `name` = the
  template's loader slash path (`analysis/margins/fetch/
  index_margin_series` — the render name, NOT a schema object).
  Schema-qualified names stay the hand seeds' (the `stats.v_*` DB
  views).
- `description` = harvested from the template's own leading ``--`` header
  (first content line, the `<stem> —` headline prefix stripped) — the
  author's business sentence; empty headers fall back to a factual
  identity line. Never invented.
- `tags` = mechanical: the resolved relations' bare names (so searching
  `index_basic_stats` lands every template that reads it) + the
  template's path segments. No hand-authoring.
- THE EDGE: `reads_from` (constant `llm_agents/kg/graph/vocabulary.
  READS_FROM`), head-first `[view, table]`, NULL `sub_relation`. Built
  by PARSING: every `schema.relation` reference in the template text,
  resolved against pg_class (relkind r/p/v/m) — alias refs (`t.date`)
  and system schemas drop out; unresolvable refs are skipped and
  COUNTED in the run report. A referenced relation with no ns0 entity
  yet gets a MECHANICAL `table` entity (description from
  `obj_description`, empty tags, refreshed only while still
  mechanical — an authored DDL-header row always wins).
- The common dir scan-and-read face is `static_rules/sources.py`
  (`SqlDirSource` → `SourceFile`: loader name, text, leading comment,
  mtime; `stats()` = the stat-only fingerprint scan) — reuse it for
  any future corpus rule; register new rules in
  `static_rules/__init__.py::RULES` (the read-time gate derives its
  per-namespace guards from the same registry).
- THE READ-TIME FRESHNESS GATE (`static_rules/freshness.py` +
  `KnowledgeGraphService._sync_stale_rules`, 2026-11-09): on every
  graph (re)load, the namespace's rules stat-scan their corpus
  (file count + MAX mtime — no content read) against the
  `llm.kg_rule_syncs` row (DDL `database/sql/llm/
  kg_rule_syncs.sql`, stamped by the writer in the sync's own
  transaction). Stale ⟺ row missing (bootstrap) OR count diff
  (templates added/deleted) OR a file newer than `max_mtime` — then
  the rule's idempotent sync runs INLINE BEFORE the read (prune +
  upsert + re-derive edges + restamp, one transaction). Fail-soft:
  a gate error logs and serves the stored graph. The offline runner
  stamps the same fingerprint, so a manual sync quiets the gate until
  the corpus moves again.
- Search integration: `reads_from` walks both directions
  (`vocabulary._EDGE_FACTORS`, 0.75/0.85 like `computed_from`), and a
  live `kg_search` attaches a bounded `sql views reading this:` block
  under its top-3 located tables (`KnowledgeGraphService.
  _sql_view_blocks` via `KnowledgeGraph.related_views`) — a hub
  table's 100+ reading templates would drown in walk fan-out. The
  templates also seed DAMPED (`vocabulary.seed_type_damp`: `view`
  entities with a slash-path name match at 0.5 — the schema-qualified
  DB views stay full weight), so 400+ template name tokens never crowd
  the study tables out of a ranking.

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

## Namespace 2 — data & labels (GENERATED, never hand-edited)

The `static.label_dim` vocabularies describe themselves
(`data_and_labels/01_label_domains.sql`). The seed is GENERATED by
`temp_scripts/generate_kg_data_labels.py` — regenerating after any
label_dim domain change or typed-table change IS the authoring move;
hand edits are overwritten by the next run.

- ONE ENTITY per label DOMAIN: `name` = the domain (`signal_type`),
  `entity_type` 'label_domain'; `tags` = the domain name + its code
  LABELS verbatim (the code words ARE the findability vocabulary —
  macd, buy/sell, top/bottom, CALL/PUT); `description` = the label_dim
  seed file's own `-- <domain>:` intro clause (mechanical fallback
  names the axis and code head).
- ONE ENTITY per table a typed column lives in: same surface name as
  ns0 (description reused from ns0 / the table comment), `tags` EMPTY
  by design — ns2 tables are WALK targets, the lexical seeds are the
  domains (seeding table vocabulary here would duplicate ns0's job and
  skew ns2's IDF).
- Edge vocabulary: `labeled_by / for_<column>` (head = the typed
  table, the sub_relation names the carrying column — differs from the
  domain when they are not homonyms, e.g. `method` ↔
  roof_floor_method).
- Usage discovery (the "what DB table uses it" check, both signals
  anchored on label_dim as the authority): column comments referencing
  `label_dim '<domain>'` ∪ SMALLINT columns NAMED after their domain
  (the `db-smallint-type-codes` house convention), validated live
  (DISTINCT ⊆ codes, 1.5s timeout; out-of-code values keep the edge —
  legacy drift worth surfacing, strays decode to NULL). Partition
  children root to their parent (pg_inherits ∪ the legacy `_pNN`
  manual-shard naming).

## Seed files

Numbered per scope under `database/sql/llm/static/knowledge_graphs/`
(ns 0 in `db_semantics/`, ns 1 in `prompts/`, ns 2 GENERATED in
`data_and_labels/` — see its section above; e.g. `01_…` index daily
chain, `02_…` ETF/stock chains). Since 2026-11-09 the ns0 seeds carry
ONLY the edge INSERTs + the DB view entities — the authored table
payloads live in the tables' own DDL files under `database/sql/` (ONE
TABLE = ONE FILE, the `db_semantics_tables` corpus rule reads the
leading banner; `apply_kg.py` runs the rules' sync between DDL and
seeds so a fresh install's edges resolve). Each file is re-appliable
and idempotent:

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

1. A NEW TABLE in ns0 = its own DDL file `database/sql/<schema>/
   <table>.sql` whose leading banner carries `-- description:` +
   `-- tags:` (read the table's `COMMENT ON TABLE` / sibling banners
   and the producing pipeline's docstring BEFORE writing its sentence
   and tags — never invent biz semantics). A NEW RELATIONSHIP = the
   seeds' edge INSERT (read the grounding docs first). For ns 1, read
   the prompt row's preview/labels/seed-file family BEFORE adding
   edges — the grounding list for every ns-1 edge lives in the seed
   file's header.
2. Write the DDL banner payload or seed file.
3. Apply + verify from a `temp_scripts/` script (WSL conda per
   `oxpicious-dev-conventions`): apply ALL `knowledge_graphs/**/*.sql` files
   in order, then print every namespace's entities and edges (decoded by
   joining `entity_ids[1]` / `entity_ids[2]` to names). Re-run must be a
   no-op: same rows, no new edges. The standing script is
   `temp_scripts/apply_kg.py` (DDL + seeds + verify in one pass) — reuse
   it, recreating it if it has been cleaned.
4. Add the file's scope to the seed-dir coverage; done. The KG schema
   files (`kg_entities.sql` / `kg_edges.sql`) only change when the
   CONTRACT changes (columns, indexes, comments, the namespace
   vocabulary) — never for data.

Persistent Python around the KG (`llm_agents/kg/` — engine, loaders,
chain-cache service) is
class-first pydantic per `oxpicious-dev-conventions`; the seed SQL files
and the one-time `temp_scripts/` apply scripts keep their script shape.
