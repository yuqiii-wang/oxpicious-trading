---
name: news-audit
description: >
  The news provider/content audit feature (2026-11-17): the origin
  registry text.news_providers (one row per (source, author); author ''
  is the provider-level sentinel; the manual is_blocked blocklist flag)
  and the two 5-day-window audit tables — text.news_provider_audit (per
  window per origin: num_items / num_digested / num_fake / fake_pct /
  avg_sentiment / avg_word_count / num_industries) and
  text.news_content_audit (per window per industry, '' = untagged: the
  same metrics plus the keyword rollup distinct_keywords /
  keyword_mentions / articles_with_keywords), both built by
  `python -m analysis.news_audit` and read by the Composites → News
  Audit (新闻审计) page with its Provider ↔ Content toggle. Denominator
  semantics: num_items counts EVERY article; fake_pct and avg_sentiment
  are over DIGESTED rows only (NULL when zero verdicts) — sentiment is
  the text.news_digestions score in [-5, 5], fake is the disingenuous
  tomorrow-question verdict. Window contract: 5 CALENDAR-day
  non-overlapping tiles (stride 5d) anchored at DATE '2000-01-03';
  complete windows compute once, the trailing partial window and recent
  windows within --grace-days 15 recompute, and news-EMPTY windows never
  materialize rows so incremental targeting excludes them.
  Use for ANY work touching these three tables, the analysis.news_audit
  pipeline, the provider/author origin grain, the audit window grid,
  is_blocked, or the News Audit page — even when the user does not say
  "audit".
---

# News audit — providers registry + 5-day provider/content windows

## The three tables (text schema; DDL under `database/sql/text/`)

| Table | Grain (PK) | Carries |
| --- | --- | --- |
| `text.news_providers` | `(source, author)` | `is_blocked`, `first_seen_date` / `last_seen_date`, `updated_at` |
| `text.news_provider_audit` | `(window_start, source, author)` | `num_items`, `num_digested`, `num_fake`, `fake_pct`, `avg_sentiment`, `avg_word_count`, `num_industries`, `is_complete`, `computed_at` |
| `text.news_content_audit` | `(window_start, industry_id)` | provider-audit metrics (minus word_count/industries) + `distinct_keywords`, `keyword_mentions`, `articles_with_keywords` |

- **ORIGIN** = `(source, author)`; `source` is `text.news.source` (gov,
  ndrc, pboc_lpr, pboc_omo, pboc_oma, zhihu, ai_daily — deliberately
  named `source`, not `provider`, to join text.news directly). `author`
  is the captured author (zhihu AuthorName, gov 来源) and `''` means the
  provider-level row — a NOT-NULL sentinel (PG PKs hold no NULL), the
  same origin concept as the fake-news detector (`author` when captured,
  else `source`).
- **industry `''`** = untagged articles (broad-market recaps, gov items)
  — a real bucket in the content audit, not a data hole. The UI labels
  it `· untagged ·` / `(untagged)`.
- **Denominators**: sentiment/fake verdicts exist only on digested rows
  (`text.news_digestions`), so `fake_pct = num_fake / num_digested` and
  `avg_sentiment = AVG(sentiment_level)` are NULL when `num_digested = 0`,
  while `num_items` counts ALL of the grain's articles in the window.
  The content audit's keyword columns are 0 (not NULL) when a window has
  no keyword rows. `distinct_keywords` counts DIFFERENT keywords per
  window (not per-article sum); `keyword_mentions` sums the affirmative
  hits (negated readings are never written by the extraction).

## The window contract

- 5 CALENDAR-day non-overlapping tiles, stride 5, anchored at
  `DATE '2000-01-03'` (`analysis/news_audit/config.py`: `WINDOW_DAYS`,
  `ANCHOR_DATE`) — window starts land on `anchor + 5k` days forever, so
  any publish date maps to exactly one window.
- Complete windows (`window_end <= MAX(news.date)` at compute time) set
  `is_complete = true` and compute once. The trailing partial window and
  windows within `--grace-days` (default 15) of the latest news date
  recompute every run (late LLM digestions / keyword rewrites).
- **News-empty windows never materialize rows** — a groupby emits nothing
  for them — so incremental targeting treats grid windows with no news as
  covered (`analysis/news_audit/steps.news_bearing_window_starts` over the
  `analysis/news_audit/steps/nonempty_windows` view). The corpus has ~640
  such sparse-coverage windows; without the exclusion every run would
  re-fetch full history chasing rows that can never exist.
- `computed_at` refreshes on every recompute of the row's window.

## The pipeline (`python -m analysis.news_audit`)

- `analysis/news_audit/__main__.py` — `NewsAuditAnalysis(DataAnalysis)`
  with `--force` (truncate both audit tables + full rebuild) and
  `--grace-days`. The registry step ALWAYS runs (cheap full-history
  aggregate); `--force` does NOT delete registry rows either.
- `analysis/news_audit/steps.py` — `ProviderRegistryStep`
  (upsert origins via the `database/sql_writes` action
  `analysis/news_audit/steps/news_providers/upsert_origin.sql` — its
  conflict arm updates ONLY the seen-date span + `updated_at`, never
  `is_blocked`), `ProviderAuditStep`, `ContentAuditStep` (both grains
  compute from ONE shared article frame + the keyword frame; per-window
  DELETE via `.../purge_window.sql` then `bulk_upsert_async`).
- Reads render `database/sql_views/analysis/news_audit/steps/*.sql`
  (`provider_origins`, `audit_articles` — news LEFT JOIN digestions with
  epoch-float dates, `audit_keywords`, `nonempty_windows`); no inline SQL.
- Rows are keyed day-number math on the grid (`analysis/news_audit/
  windows.py` `AuditWindowGrid`); dates enter frames as epoch float8 and
  materialize at the write boundary (`epoch_col_to_dt64` with `index=` —
  the gappy-index misalignment class).

## KG search coverage (ns0)

The three tables are namespace-0 entities sourced from their own DDL
banners (`-- description:` / `-- tags:` in `database/sql/text/
news_providers.sql`, `news_provider_audit.sql`, `news_content_audit.sql`),
synced by the `db_semantics_tables` corpus rule and owned in
`llm.kg_rule_entities`. Seed edges live in
`database/sql/llm/static/knowledge_graphs/db_semantics/15_text_corpus_static.sql`:
each audit `computed_from` news (+ digestions for sentiment/fake;
keywords for the content rollup), and the audits/registry anchor on each
other (`anchored_by for_source_author`). `kg_search` hits them via e.g.
"provider audit", "fake news", "审计", "新闻审计", "黑名单", "keyword
mentions" (verified: `temp_scripts/check_news_audit_kg.py` asserts
entities/edges/ledger and probes search). Editing semantics = edit the
DDL banner, never a skill-side list (`kg-db-semantics` is the authoring
contract).

## The UI (Composites → News Audit)

- Page: `data_viz/src/analysis/pages/Composites/NewsAudit/`
  (`NewsAuditPage` + `ProviderAuditTable` / `ContentAuditTable` on the
  shared `ExpandedTable`; shared formatters in `format.ts`). Registered
  in `CompositesPage.tsx` (`COMPOSITE_ANALYSES`, slug `news-audit`) and
  `App.tsx`; docs seed row `ui.composites.news-audit.page` in
  `database/sql/static/docs/static/20_ui_analysis_pages.sql`.
- Header TOGGLE (Provider audit ↔ Content audit) is a fetch dep (the
  DbAudit-page pattern: `useChartData` deps + spinner, stale rows stay
  visible); the 8w/26w/52w selector picks how many recent windows load.
- API: `GET /api/analysis/news-audit/providers?limit=&source=` and
  `GET .../content?limit=&industry_id=` (routes
  `data_viz/api/routes/analysis/news-audit.ts`, service
  `data_viz/api/services/analysis/news-audit.ts`, SQL templates
  `database/sql_views/data_viz/api/services/analysis/news-audit/
  {provider,content}-audit.sql`); `POST .../run` spawns
  `python -m analysis.news_audit` through the py-runner (tag
  `analysis-run:news_audit`) and the page refetches on success. The
  provider rows join the registry so the table shows `is_blocked` +
  seen-date span per origin.

## Invariants (break none of them)

1. `author`/`industry_id` sentinel is `''`, never NULL (PK contract).
2. `fake_pct` / `avg_sentiment` denominate on `num_digested`; NULL when
   zero verdicts — never 0-fill them.
3. `window_start` values sit on the anchor grid; `window_end` =
   start + 4 days.
4. The registry sync never DELETEs origins and never writes `is_blocked`
   (manual curation only).
5. Incremental targeting must keep the news-empty-window exclusion (or
   accept full-history refetches every run).
6. Recomputed windows are purged per `window_start` before upsert — the
   upsert alone would leave stale rows for origins/industries that
   disappeared from the window's recompute.
7. Content-audit keyword columns stay int 0-filled end-to-end (NULL would
   push them through float at the write boundary).

## Where things live

```
database/sql/text/news_providers.sql          DDL (KG banner payload)
database/sql/text/news_provider_audit.sql     DDL (KG banner payload)
database/sql/text/news_content_audit.sql      DDL (KG banner payload)
analysis/news_audit/                          pipeline (config/windows/steps/__main__)
database/sql_views/analysis/news_audit/steps/ read templates
database/sql_writes/analysis/news_audit/steps/registry upsert + window purges
database/sql_views/data_viz/api/services/analysis/news-audit/  API SELECT templates
data_viz/api/routes/analysis/news-audit.ts    endpoints + NEWS_AUDIT_RUN_TAG
data_viz/api/services/analysis/news-audit.ts  service
data_viz/src/analysis/pages/Composites/NewsAudit/  page + tables + format.ts
temp_scripts/apply_news_audit_tables.py       one-time DDL/docs apply (2026-11-17)
temp_scripts/check_news_audit_kg.py           KG coverage check + search probe
```
