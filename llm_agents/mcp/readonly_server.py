"""readonly_server.py — DbReadonlyServer: the read-only DB MCP server
as a single class.

Owns the whole stack: connection params, the read-only SQL guard, the
query runner, the schema helpers, the MCPServer instance, and the
registered tools (as bound methods): the three DB tools, the text/llm-schema
full-text search (``text_search`` — segmented tsvector queries over the
GIN-indexed ``search_tsv`` columns), and the web
tools (``web_search`` / ``web_summarize`` — the
``llm_agents.online_search_summary`` provider stack exposed so MCP
clients such as the deep agent can search the open web next to the
DB). ``__main__.py`` stays a thin launcher (args, heartbeat, ``run()``).

The web tools reuse the ``llm_agents.online_search_summary``
providers (ZhiPu web search + LLM summary) and PERSIST NOTHING —
writing to the DB stays with the CLI's ``--store`` path, keeping this
server's no-DB-writes contract. That package is imported LAZILY (on
the first web-tool call, then cached in ``sys.modules``): importing
any of its submodules executes the facade ``__init__``, which pulls
the storage half and ``_common.build_commons`` (pandas/numpy) — dead
weight this daemon's startup never needs, and a dependency a web tool
can afford to fail soft (a per-call ``{"error": ...}``) where an
import-time failure would take the DB tools down with it.

Access model: connects as the ``readonly_analytics`` role (created by
``database/sql/00_readonly_role.sql``) — a standalone LOGIN with
SELECT only on the data schemas and a role-level
``default_transaction_read_only = on`` that a non-superuser cannot
override. Every tool call opens a fresh short-lived connection and
also guards the SQL text client-side (single statement, read-only
leading keyword) so mistakes fail fast without a round trip.

Connection params come from ``database/.env`` (SUPABASE_*) via the
shared ``_common.db_commons`` helpers; the role password is
``READONLY_ANALYTICS_PASSWORD`` from the environment / ``database/.env``,
falling back to the same fixed password ``apply_readonly_role.py``
provisions.

The package lives at ``llm_agents.mcp`` — inside the repo's
``llm_agents`` workspace — so the top-level ``mcp`` name belongs to
the installed MCP SDK and the ``MCPServer`` import below is a plain
SDK import. (At its former repo-root ``mcp/`` location this package
shadowed the SDK and needed a sys.path swap at import time; the move
under ``llm_agents/`` removed that.)

No cudf/pandas in this module graph: the server only passes SQL text
and JSON-able row results.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

import psycopg

from _common.db_commons._helpers import _load_env_vars
from llm_agents._core.errors import LlmProviderError
from mcp.server.mcpserver import MCPServer

if TYPE_CHECKING:
    from llm_agents.online_search_summary.core.models import SearchOptions
    from llm_agents.online_search_summary.providers.base import (
        BaseOnlineSearchProvider,
    )

logger = logging.getLogger(__name__)


class DbReadonlyServer:
    """MCP server exposing read-only DB queries plus live web search.

    The DB tools open a fresh connection as the ``readonly_analytics``
    role per call, validate the statement client-side, and cap the
    returned rows. The web tools run the online_search_summary
    providers (search + LLM summary) and persist nothing.
    """

    # Same fixed password apply_readonly_role.py substitutes into the
    # SQL placeholder. Overridable via READONLY_ANALYTICS_PASSWORD.
    _DEFAULT_READONLY_PASSWORD = "read0n!y"

    # Row cap per query: MCP tool results must stay small enough for an
    # LLM context; callers narrow the query instead of paging here.
    MAX_ROWS_HARD_CAP = 500

    # text_search: per-call result cap (hits carry 400-char snippets; a
    # tight cap keeps one tool call well inside an LLM context).
    TEXT_SEARCH_MAX_ROWS = 50

    # text_search: the engagement orderings beside rank/date — the tables
    # each supports. "votes" = the thumbs-up column (news article upvotes,
    # comment like counts); "comments" = the most-discussed ordering (the
    # text.news_comments count per article, riding back as n_comments).
    _TEXT_SEARCH_ENGAGEMENT_ORDERS: dict[str, set[str]] = {
        "votes": {"news", "news_comments"},
        "comments": {"news"},
    }

    # text_search: the searchable text/llm-schema tables — display columns
    # (snippets pre-truncated), the scalar filters each supports, and the
    # date column date_from/date_to bound to (timestamptz on the QA
    # tables, DATE on news). search_tsv is the weighted GIN-indexed
    # tsvector every table here carries (TSV_SPECS contract).
    _TEXT_SEARCH_TABLES: dict[str, dict[str, Any]] = {
        "news": {
            "table": "text.news",
            "select": ("news_id, title, source, date::text AS date, author,"
                       " industry_id, LEFT(content, 400) AS snippet"),
            "filters": {"industry_id", "sector_id", "source", "author"},
            "date_col": "date",
        },
        "news_comments": {
            "table": "text.news_comments",
            "select": ("comment_id, news_id, source, author,"
                       " date::text AS date, LEFT(content, 400) AS snippet"),
            "filters": {"news_id", "source"},
            "date_col": "date",
        },
        "news_digestions": {
            "table": "text.news_digestions",
            "select": ("news_id, date::text AS date, industry_id,"
                       " LEFT(summary, 400) AS snippet"),
            "filters": {"news_id", "industry_id"},
            "date_col": "date",
        },
        "llm_qa": {
            "table": "llm.llm_qa",
            "select": ("qa_id, LEFT(question, 200) AS question,"
                       " LEFT(answer, 400) AS snippet, industry_id,"
                       " (qa_date AT TIME ZONE 'Asia/Shanghai')::date::text"
                       " AS date"),
            "filters": {"industry_id", "sector_id"},
            "date_col": "qa_date",
            "active_only": True,
        },
        "llm_qa_by_ask": {
            "table": "llm.llm_qa_by_ask",
            "select": ("ask_id, LEFT(question, 200) AS question,"
                       " LEFT(COALESCE(answer, error_tail), 400) AS snippet,"
                       " code, status,"
                       " (ask_date AT TIME ZONE 'Asia/Shanghai')::date::text"
                       " AS date"),
            "filters": {"code"},
            "date_col": "ask_date",
            "active_only": True,
        },
        "llm_qa_by_research": {
            "table": "llm.llm_qa_by_research",
            "select": ("qa_id, LEFT(question, 200) AS question,"
                       " LEFT(answer, 400) AS snippet, industry_id,"
                       " (qa_date AT TIME ZONE 'Asia/Shanghai')::date::text"
                       " AS date"),
            "filters": {"industry_id", "sector_id"},
            "date_col": "qa_date",
            "active_only": True,
        },
    }

    # A statement must START with one of these keywords after stripping
    # comments/whitespace. `WITH` covers CTE SELECTs; EXPLAIN/SHOW/TABLE
    # are informational. Everything else (INSERT/UPDATE/DELETE/DDL/DO/
    # CALL/SET/COPY) is rejected before it reaches the server — the role
    # is the real guard, this is the fast-fail.
    _READONLY_LEAD_RE = re.compile(
        r"^\s*(SELECT|WITH|EXPLAIN|SHOW|TABLE|VALUES)\b", re.IGNORECASE,
    )
    # A query token that actually becomes a tsquery lexeme (has CJK or
    # alphanumeric content) — punctuation-only tokens are dropped by
    # websearch_to_tsquery and never reach text.search_lexicon_df.
    _LEXEME_RE = re.compile(r"[0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
    _COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

    def __init__(
        self,
        name: str = "oxpicious-db",
        title: str = "oxpicious-trading read-only DB",
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self._default_host = host
        self._default_port = port
        # Lazily-constructed search providers, keyed by registry name —
        # construction resolves the provider API key on first use.
        self._providers: dict[str, BaseOnlineSearchProvider] = {}
        self._server = MCPServer(
            name=name,
            title=title,
            instructions=(
                "Query the oxpicious-trading PostgreSQL database READ-ONLY "
                "as the readonly_analytics role. Schemas: stats, analysis, "
                "analysis_signals, analysis_composites, "
                "analysis_insight_metrics, strategy, static, live, text, llm. "
                "Call db_list_tables / db_describe_table first when unsure "
                "of column names, then db_run_query. For KEYWORD search "
                "over the text/llm schemas (news corpus, Q&A) call text_search — the "
                "large-text tables carry a GIN-indexed search_tsv that "
                "hand-written ILIKE cannot use on Chinese text. Results are "
                "capped at "
                f"{self.MAX_ROWS_HARD_CAP} rows — aggregate or filter in "
                "SQL instead of fetching wide sweeps. web_search / "
                "web_summarize run LIVE web searches (no DB): search "
                "returns normalized references; summarize answers the "
                "query with an LLM over them, inline [来源：ref_N] "
                "citations matching the references' refer tags. Neither "
                "web tool persists anything."
            ),
        )
        self._register_tools()

    # ------------------------------------------------------------------
    # Access layer: guard, connection params, query runner
    # ------------------------------------------------------------------

    @staticmethod
    def assert_readonly_sql(sql: str) -> str:
        """Validate the SQL text as a single read-only statement.

        Returns the statement unchanged when it passes; raises ValueError
        otherwise. Rejects multi-statement text (a trailing semicolon is
        allowed) and anything not led by a read-only keyword.
        """
        stripped = DbReadonlyServer._COMMENT_RE.sub(" ", sql).strip()
        if not stripped:
            raise ValueError("empty SQL statement")
        body = stripped
        if body.endswith(";"):
            body = body[:-1]
        if ";" in body:
            raise ValueError("only a single SQL statement is allowed")
        if not DbReadonlyServer._READONLY_LEAD_RE.match(body):
            raise ValueError(
                "only read-only statements (SELECT / WITH / EXPLAIN / "
                "SHOW / TABLE / VALUES) are allowed",
            )
        return body

    @staticmethod
    def _readonly_conn_params() -> dict[str, Any]:
        """Connection params for the readonly_analytics role."""
        _load_env_vars()
        return {
            "host": os.environ.get("SUPABASE_HOST", "127.0.0.1"),
            "port": int(os.environ.get("SUPABASE_PORT", "9876")),
            "database": os.environ.get("SUPABASE_DB", "oxpicious-stats"),
            "user": "readonly_analytics",
            "password": os.environ.get(
                "READONLY_ANALYTICS_PASSWORD",
                DbReadonlyServer._DEFAULT_READONLY_PASSWORD,
            ),
        }

    @staticmethod
    def _result_size_bytes(rows: list[list[Any]]) -> int:
        """JSON size of the returned rows — the payload an MCP client sees."""
        return len(json.dumps(rows, ensure_ascii=False, default=str))

    def run_query(self, sql: str, params: list[Any] | None = None,
                  max_rows: int = 100) -> dict[str, Any]:
        """Execute one read-only query as readonly_analytics.

        Args:
            sql: a single SELECT/WITH/EXPLAIN/SHOW/TABLE/VALUES statement.
            params: positional query parameters ($1, $2, ... style for
                psycopg), or None.
            max_rows: rows to return; capped at MAX_ROWS_HARD_CAP. When
                the result has more rows than the cap, ``truncated`` is
                True and the extra rows are dropped (the query itself
                still runs to completion server-side).

        Returns:
            {"columns": [...], "rows": [[...], ...], "metadata": {...}}
            where metadata carries the data size and the head/tail
            samples: {"row_count": int, "truncated": bool,
            "data_size_bytes": int, "head": first <=5 rows,
            "tail": last <=5 rows}
        """
        statement = self.assert_readonly_sql(sql)
        limit = min(int(max_rows), self.MAX_ROWS_HARD_CAP)
        if limit < 1:
            raise ValueError("max_rows must be >= 1")

        conn_params = self._readonly_conn_params()
        with psycopg.connect(
            host=conn_params["host"],
            port=conn_params["port"],
            dbname=conn_params["database"],
            user=conn_params["user"],
            password=conn_params["password"],
            connect_timeout=10,
            autocommit=True,
        ) as conn:
            # Belt and suspenders at session level too (the role default
            # already forces this; a superuser widening the role later
            # cannot silently turn this connection read-write).
            conn.execute("SET default_transaction_read_only = on")
            with conn.cursor() as cur:
                cur.execute(statement, params or None)
                columns = [d.name for d in cur.description or ()]
                if not columns:
                    return {"columns": [], "rows": [], "metadata": {
                        "row_count": 0, "truncated": False,
                        "data_size_bytes": 0, "head": [], "tail": [],
                    }}
                rows = [list(r) for r in cur.fetchmany(limit + 1)]
                truncated = len(rows) > limit
                rows = rows[:limit]
                return {
                    "columns": columns,
                    "rows": rows,
                    "metadata": {
                        "row_count": len(rows),
                        "truncated": truncated,
                        "data_size_bytes": self._result_size_bytes(rows),
                        "head": rows[:5],
                        "tail": rows[-5:],
                    },
                }

    def list_tables(self, schema: str) -> list[dict[str, Any]]:
        """List tables/views in one schema with approximate row counts."""
        result = self.run_query(
            """
            SELECT c.relname AS table_name,
                   c.relkind AS kind,
                   c.reltuples::bigint AS approx_rows
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relkind IN ('r', 'p', 'v', 'm')
            ORDER BY c.relname
            """,
            params=[schema],
            max_rows=self.MAX_ROWS_HARD_CAP,
        )
        return [
            {
                "table": r[0],
                "kind": {"r": "table", "p": "partitioned table",
                         "v": "view", "m": "materialized view"}[r[1]],
                "approx_rows": r[2],
            }
            for r in result["rows"]
        ]

    def describe_table(self, schema: str, table: str) -> list[dict[str, Any]]:
        """Describe one table's columns, types and key membership."""
        result = self.run_query(
            """
            SELECT a.attname AS column_name,
                   format_type(a.atttypid, a.atttypmod) AS data_type,
                   a.attnotnull AS not_null,
                   COALESCE(
                       (SELECT true FROM pg_index i
                        WHERE i.indrelid = a.attrelid
                          AND i.indisprimary AND a.attnum = ANY(i.indkey)),
                       false) AS primary_key
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            params=[schema, table],
            max_rows=self.MAX_ROWS_HARD_CAP,
        )
        return [
            {
                "column": r[0],
                "type": r[1],
                "not_null": r[2],
                "primary_key": r[3],
            }
            for r in result["rows"]
        ]

    # ------------------------------------------------------------------
    # Web search layer: llm_agents.online_search_summary providers
    # ------------------------------------------------------------------
    # The search package imports lazily (see the module docstring): the
    # facade executes its whole package __init__ (storage half +
    # _common.build_commons), so the cost is only paid on the first web
    # tool call and a missing search dependency degrades to a per-call
    # {"error": ...} instead of a daemon that never starts.

    def _provider(self, name: str) -> "BaseOnlineSearchProvider":
        """The cached search provider (constructed on first use — the
        ctor resolves the API key from the project .env)."""
        from llm_agents.online_search_summary.providers.registry import (
            get_provider,
        )

        provider = self._providers.get(name)
        if provider is None:
            provider = get_provider(name)()
            self._providers[name] = provider
        return provider

    @staticmethod
    def _search_opts(engine: str | None, count: int, recency: str,
                     domain: str | None,
                     content_size: str) -> "SearchOptions":
        """Build + validate the shared search knobs."""
        from llm_agents.online_search_summary.core.models import (
            SearchOptions,
        )

        opts = SearchOptions(
            engine=engine, count=count, recency=recency, domain=domain,
            content_size=content_size)
        opts.validate()
        return opts

    # ------------------------------------------------------------------
    # MCP server: tools and lifecycle
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        register = self._server.tool()
        register(self.db_run_query)
        register(self.db_list_tables)
        register(self.db_describe_table)
        register(self.text_search)
        register(self.web_search)
        register(self.web_summarize)

    def db_run_query(self, sql: str, params: list | None = None,
                     max_rows: int = 100) -> dict:
        """Execute ONE read-only SQL statement (SELECT / WITH / EXPLAIN /
        SHOW / TABLE / VALUES) and return {"columns", "rows", "metadata"}.
        metadata carries row_count, truncated, data_size_bytes, and the
        head/tail 5 rows. Params are positional, matched to $1/$2/... Use
        the schema-qualified table name (e.g. stats.sec_info)."""
        try:
            return self.run_query(sql, params=params, max_rows=max_rows)
        except ValueError as exc:
            return {"error": str(exc)}

    def db_list_tables(self, schema: str) -> str:
        """List tables / views in one schema with approximate row counts,
        so queries can target the right table before writing SQL. Returns
        a JSON array of {"table", "kind", "approx_rows"}."""
        return json.dumps(self.list_tables(schema))

    def db_describe_table(self, schema: str, table: str) -> str:
        """Describe one table: column names, data types, nullability and
        primary-key membership. Returns a JSON array of {"column", "type",
        "not_null", "primary_key"}."""
        return json.dumps(self.describe_table(schema, table))

    async def text_search(
        self, query: str, table: str = "news", max_rows: int = 20,
        industry_id: str | None = None, sector_id: str | None = None,
        source: str | None = None, author: str | None = None,
        code: str | None = None, news_id: int | None = None,
        date_from: str | None = None, date_to: str | None = None,
        order_by: str = "rank",
    ) -> dict:
        """Weighted FULL-TEXT search over the text/llm schemas' GIN-indexed
        ``search_tsv`` columns (Chinese is segmented automatically — raw
        Chinese text in a plain db_run_query ILIKE/tsquery will NOT match).
        Ranks by ts_rank, best first; when EVERY query word is overly
        common (text.search_lexicon_df >= 25%-DF lexemes) it orders by the
        article date instead (rank stays a returned column + tiebreak) —
        see the "ordered_by" field. *order_by* picks the ranking:
        "rank" (default — the relevance strategy above), "date" (newest
        first), "votes" (thumbs-up count DESC — news articles / news
        comments) or "comments" (most-discussed DESC, news only — the
        comment count rides back as n_comments). Returns {"query",
        "table", "search_tokens", "ordered_by", "row_count", "truncated",
        "hits": [{columns + rank + snippet}]}.

        *query*: free text; words are AND-ed (websearch syntax: "a b" both
        must hit). *table* — one of: news (articles; title A / content C),
        news_comments, news_digestions, llm_qa (weekly industry Q&A),
        llm_qa_by_ask (interactive asks), llm_qa_by_research (research-logic
        Q&A; the five dimensions are searched too). *max_rows* capped at
        {cap}. Optional scalar filters — only the ones the table supports
        are accepted: industry_id / sector_id / source / author (news,
        llm_qa, llm_qa_by_research per set), code (llm_qa_by_ask), news_id
        (news_comments, news_digestions), date_from / date_to (the table's
        article/qa date, inclusive; e.g. 2026-10-01). *order_by* —
        rank | date | votes | comments per the table's support (votes:
        news, news_comments; comments: news); an unsupported combination
        errors listing what the table supports."""
        spec = self._TEXT_SEARCH_TABLES.get(table)
        if spec is None:
            return {"error": f"unknown table {table!r} — one of: "
                             f"{', '.join(self._TEXT_SEARCH_TABLES)}"}
        query = (query or "").strip()
        if not query:
            return {"error": "query is required"}

        # Query-side segmentation (same StructBERT segmenter the document
        # vectors were built with) — lazy like the web tools: the segmenter
        # import is cheap, the model loads on the first call and offloads
        # when idle.
        try:
            from _common.tokenization import SearchIndexer
            tokens = SearchIndexer.get().tokenize_for_search(query)
        except Exception as exc:
            return {"error": f"query segmentation failed: {exc}"}
        if not tokens.strip():
            return {"error": "query produced no search tokens"}

        # Rank strategy: DF-aware (text.search_lexicon_df — the >= 25%-DF
        # lexemes per table, refreshed after each tsv write pass): when
        # EVERY query lexeme is overly common (市场 alone, or a query whose
        # words are all ubiquitous), ORDER BY rank must detoast + score
        # every match — the slow path, linear in the corpus (~0.2s at 28k
        # articles) — and ranking by the matched frequency of ubiquitous
        # words is noise anyway, so order by the article date with rank as
        # the tiebreak. Any query carrying at least one non-common lexeme
        # keeps the relevance order (the GIN bitmap intersection keeps
        # those in the ms range). Common lexemes are NEVER dropped from
        # the tsquery: ANDing one with a rarer lexeme only shrinks the
        # matched set (2026-11-01 study). A missing/stale stats table
        # degrades silently to the rank order. The engagement orderings
        # (date / votes / comments) skip the lookup — the caller asked for
        # an explicit order, which is the point of passing it.
        # ORDER BY keys are QUALIFIED with the table name: a bare `date`
        # resolves to the select list's `date::text AS date` OUTPUT ALIAS —
        # a text cast no date index serves, which forces the seq-scan+sort
        # slow path even under LIMIT (2026-11-01 probe: 市场 128ms vs 1.05ms
        # with the index walk + early stop).
        tbl_ref = spec["table"].rsplit(".", 1)[-1]
        order = (order_by or "rank").strip().lower()
        supported_orders = ({"rank", "date"}
                            | set(self._TEXT_SEARCH_ENGAGEMENT_ORDERS))
        if order not in supported_orders:
            return {"error": (f"unknown order_by {order_by!r} — one of: "
                              f"{', '.join(sorted(supported_orders))}")}
        if (order in self._TEXT_SEARCH_ENGAGEMENT_ORDERS
                and table not in self._TEXT_SEARCH_ENGAGEMENT_ORDERS[order]):
            return {"error": (
                f"table {table!r} does not support order_by={order!r} — "
                "supported tables: "
                + ", ".join(sorted(
                    self._TEXT_SEARCH_ENGAGEMENT_ORDERS[order])))}
        if order == "comments":
            order_clause = (f"n_comments DESC, {tbl_ref}.{spec['date_col']}"
                            " DESC, rank DESC")
            ordered_by = "comments"
        elif order == "votes":
            order_clause = (f"{tbl_ref}.votes DESC NULLS LAST, "
                            f"{tbl_ref}.{spec['date_col']} DESC, rank DESC")
            ordered_by = "votes"
        elif order == "date":
            order_clause = f"{tbl_ref}.{spec['date_col']} DESC, rank DESC"
            ordered_by = "date"
        else:
            order_clause = f"rank DESC, {tbl_ref}.{spec['date_col']} DESC"
            ordered_by = "rank"
            lexemes = [t for t in tokens.split() if self._LEXEME_RE.search(t)]
            if lexemes:
                try:
                    from _common.labels import SEARCH_TABLE_CODE
                    stats = self.run_query(
                        "SELECT lexeme FROM text.search_lexicon_df "
                        "WHERE search_table = %s AND lexeme = ANY(%s)",
                        params=[SEARCH_TABLE_CODE[table], lexemes],
                        max_rows=len(lexemes))
                    common = {row[0] for row in stats["rows"]}
                    if all(lex in common for lex in lexemes):
                        order_clause = (
                            f"{tbl_ref}.{spec['date_col']} DESC, rank DESC")
                        ordered_by = "date"
                except Exception as exc:
                    logger.debug("text_search: lexicon-DF lookup failed, "
                                 "ranking by relevance: %r", exc)

        # The engagement selects: the comments ordering joins the
        # pre-aggregated per-article comment count (one grouped scan of
        # news_comments, then a hash join — the count column rides back so
        # the caller sees the engagement value it ordered by); the votes
        # ordering adds the thumbs-up column.
        extra_select = ""
        join_sql = ""
        if order == "comments":
            extra_select = ", COALESCE(cc.n_comments, 0)::int AS n_comments"
            # The count subquery aliases its news_id out (cc_news_id) so
            # the table's own bare select columns stay unambiguous.
            join_sql = (
                "LEFT JOIN (SELECT news_id AS cc_news_id, "
                "count(*) AS n_comments "
                "FROM text.news_comments GROUP BY news_id) cc "
                f"ON cc.cc_news_id = {tbl_ref}.news_id")
        elif order == "votes":
            extra_select = f", {tbl_ref}.votes"

        where = ["search_tsv @@ tsq"]  # tsq = the CROSS JOINed query below
        params: list[Any] = [tokens]
        scalar_filters = {
            "industry_id": industry_id, "sector_id": sector_id,
            "source": source, "author": author, "code": code,
            "news_id": news_id,
        }
        supported: set[str] = spec["filters"]
        unsupported = sorted(
            name for name, val in scalar_filters.items()
            if val is not None and name not in supported
        )
        if unsupported:
            return {"error": (
                f"table {table!r} does not support filter(s) "
                f"{', '.join(unsupported)} — supported: "
                f"{', '.join(sorted(supported)) or '(none)'}")}
        for name in sorted(supported):
            val = scalar_filters[name]
            if val is None:
                continue
            params.append(val)
            where.append(f"{name} = %s")
        if date_from:
            params.append(date_from)
            where.append(f"{spec['date_col']} >= %s")
        if date_to:
            params.append(date_to)
            where.append(f"{spec['date_col']} <= %s")
        if spec.get("active_only"):
            where.append("is_active")

        limit = min(max(int(max_rows), 1), self.TEXT_SEARCH_MAX_ROWS)
        params.append(limit)
        # Placeholder order == param order: the tokens %s rides the CROSS
        # JOIN (first in the SQL text), then the sorted WHERE filters, the
        # date bounds, and the LIMIT.
        sql = (
            f"SELECT {spec['select']}{extra_select}, "
            f"ROUND(ts_rank(search_tsv, tsq)::numeric, 4)::float AS rank "
            f"FROM {spec['table']} "
            + (f"{join_sql} " if join_sql else "")
            + f"CROSS JOIN websearch_to_tsquery('simple', %s) tsq "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY {order_clause} LIMIT %s"
        )
        try:
            result = self.run_query(
                sql, params=params, max_rows=self.TEXT_SEARCH_MAX_ROWS)
        except Exception as exc:
            return {"error": str(exc)}
        hits = [dict(zip(result["columns"], row)) for row in result["rows"]]
        return {
            "query": query,
            "table": table,
            "search_tokens": tokens,
            "ordered_by": ordered_by,
            "row_count": len(hits),
            "truncated": result["metadata"]["truncated"],
            "hits": hits,
        }

    async def web_search(
        self, query: str, provider: str = "zhipu",
        engine: str | None = None, count: int = 10,
        recency: str = "noLimit", domain: str | None = None,
        content_size: str = "medium",
    ) -> dict:
        """Search the open web (LIVE network call, no DB) and return the
        normalized references WITHOUT summarizing:
        {"provider", "engine", "request_id", "created", "intents",
        "references": [{refer, title, content, link, media,
        publish_date}]}. Knobs: provider zhipu|zhihu; count 1-50
        (default 10); recency oneDay|oneWeek|oneMonth|oneYear|noLimit;
        content_size medium|high; domain restricts to one site (e.g.
        www.sohu.com); engine is provider-specific (zhipu: search_std /
        search_pro / search_pro_sogou / search_pro_quark, default
        search_pro). Nothing is persisted."""
        try:
            opts = self._search_opts(engine, count, recency, domain,
                                     content_size)
            resp = await self._provider(provider).search(query, opts)
            return resp.to_dict()
        except (ValueError, ImportError, LlmProviderError) as exc:
            return {"error": str(exc)}

    async def web_summarize(
        self, query: str, provider: str = "zhipu",
        mode: str = "compose", model: str | None = None, lang: str = "zh",
        engine: str | None = None, count: int = 10,
        recency: str = "noLimit", domain: str | None = None,
        content_size: str = "medium",
    ) -> dict:
        """Search the open web (LIVE network call, no DB), then answer
        the query with an LLM summary over the retrieved references.
        Returns {"question", "answer", "cited_refs", "references",
        "provider", "model", "mode", ...} — the answer carries inline
        [来源：ref_N] markers whose ref_N tags match the references.
        mode compose (default) = search, then a separate LLM pass over
        the references (refs cannot go missing); native = the
        provider's one-shot search-in-chat. lang sets the answer
        language (zh default, e.g. en); model overrides the provider's
        default chat model; the web_search knobs apply as there.
        Nothing is persisted — text.* storage stays with the CLI
        --store path."""
        try:
            if mode not in ("native", "compose"):
                raise ValueError(f"mode must be native|compose, got "
                                 f"{mode!r}")
            opts = self._search_opts(engine, count, recency, domain,
                                     content_size)
            summary = await self._provider(provider).summarize(
                query, model=model, opts=opts, mode=mode, lang=lang)
            return summary.to_dict()
        except (ValueError, ImportError, LlmProviderError) as exc:
            return {"error": str(exc)}

    def run(self, stdio: bool = False, host: str | None = None,
            port: int | None = None) -> None:
        """Run until stopped: stdio transport for one subprocess client,
        or (default) the always-on streamable-HTTP daemon mode."""
        if stdio:
            logger.info("starting oxpicious-db MCP server (stdio)")
            self._server.run(transport="stdio")
        else:
            host = host if host is not None else self._default_host
            port = port if port is not None else self._default_port
            logger.info(
                "starting oxpicious-db MCP server (streamable http on "
                "%s:%d/mcp)", host, port,
            )
            self._server.run(transport="streamable-http", host=host,
                             port=port)
