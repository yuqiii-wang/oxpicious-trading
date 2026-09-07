"""builds.text — Load downloaded news text into the `text` schema.

Reads the downloaded news artifacts written by the `downloads.macro.*`
pipelines (all under ``temps/``), normalizes them to the ``text.news`` row
shape, tags each article with an industry via the integrated SEC-classification
keyword map, and upserts everything into:

  * ``text.news``            — one row per (title, source, date)
  * ``text.news_keywords``   — per-article keyword frequencies + corpus stats
                               (doc_freq / idf maintained by this build)
  * ``text.news_keywords.today_industry_change`` — industry's move on the
                               article date, from stats.industry_basic_stats

Run as ``python -m builds.text``. See ``__main__.py`` for the CLI flags.

Downloaded source formats (as produced by the downloaders):

  * gov      — ``temps/gov_news/``: ``gov_news_titles.csv`` title list +
               ``articles_index.csv`` + per-article ``articles/*.md``
               (pseudo-YAML frontmatter + ``## Body`` markdown).
  * ndrc     — ``temps/ndrc_news/``: ``ndrc_news_titles.csv`` (titles only,
               no content crawled).
  * pboc_lpr — ``temps/pboc_lpr_news/``: per-article ``.md`` with
               frontmatter (category/title/detail_url/pub_date/lpr_1y/lpr_5y)
               + ``## Raw body``` fenced block.
  * pboc_omo — ``temps/pboc_repo_news/``: per-article ``.md``
               (omo_transaction announcements) + ``## Raw body``.
  * pboc_oma — ``temps/pboc_oma_news/``: per-article ``.md``
               (primary_dealer announcements) + ``## Raw body``.
  * zhihu    — ``temps/zhihu_news/``: one JSON per (keyword, target_date);
               each item is an answer/article with Title / ContentText /
               Url / EditTime (unix seconds, Asia/Shanghai).

Canonical DDL lives in ``database/sql/text/00_text_schema.sql`` +
``database/sql/text/01_news.sql`` — this build never alters the schema, it
only writes rows (and exits with a hint if the tables are missing).
"""
