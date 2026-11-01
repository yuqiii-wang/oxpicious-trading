"""Search-index tokenization + weighted tsvector writes for the text schema.

The `text` schema's large-text tables (news articles, comments, digestions,
the three Q&A tables) carry a ``search_tsv tsvector`` column with a GIN
index (DDL: the ``CREATE TABLE`` files under ``database/sql/text/``).  Because
Postgres' ``simple`` text-search config does not segment Chinese (a raw
Chinese sentence becomes one giant lexeme), the tsvector is NOT a GENERATED
column: the writing pipeline pre-segmentates the text with the local
StructBERT segmenter (:class:`ChineseWordSegmenter`), space-joins the
words, and builds the weighted tsvector in SQL:

    strip(setweight(to_tsvector('simple', <question tokens>), 'A') ||
          setweight(to_tsvector('simple', <answer tokens>),   'B') || ...)

Positions are stripped (:func:`strip`): GIN indexes lexemes only and the
rank path's cost is dominated by detoasting the tsvector — position data
was the bulk of the datum (2026-11-01 study).  ``ts_rank`` on a
positionless vector still discriminates the A/B/C/D weights and the
matched-term count; only within-document term frequency is lost.

Weight map (best practice — A most influential): A = title/question,
B = answer/summary, C = body/content/dimensions, D = supporting context.
The map lives ONLY in :data:`TSV_SPECS` (and mirrors the DDL comments).

Skip-word post-process: every token stream here also drops the
static.keyword_maps skip vocabulary (category_type=skip — low-semantic
function words like 其实/也/就是说 (zh) and the/a/of (en), seeded in
05_seed_skip.sql): document vectors stop indexing them and query terms
segment to content tokens only.  English skips the segmenter (whitespace
passthrough, and the model-free bypass) but still goes through this
post-process; matching is whole-token equality, case-insensitive for
ASCII (The/THE hit 'the').  The set loads once per process
(:func:`_skip_words`); a failed load degrades to no filtering —
tokenization must never break on the DB being unreachable.

The query side's overly-common lexeme stats live in
``text.search_lexicon_df`` (DDL: ``database/sql/text/
14_search_lexicon_df.sql``): per searchable table, the lexemes whose
document frequency is >= :data:`LEXICON_DF_MIN_RATIO` of the table — the
set the MCP ``text_search`` tool checks to pick its ranking strategy
(ts_rank sorting over a fully-common-word match set is the slow path).

:class:`SearchIndexer` ties it together — one object owning tokenization
and the tsvector writes:

    indexer = SearchIndexer.get()                       # or SearchIndexer(seg)
    tokens = indexer.tokenize_for_search(user_query)    # query-side helper
    await indexer.set_search_tsv_async(conn, "text.news", rows)
    await indexer.refresh_missing_search_tsv_async(conn, "text.news")
    await indexer.refresh_lexicon_df_async(conn, "text.news")

The segmenter is injectable (tests); default is the process singleton.
torch/transformers stay lazy — importing this module is cheap.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from .chinese_word_segmenter import ChineseWordSegmenter, _CJK

logger = logging.getLogger(__name__)

TSCONFIG = "simple"

# Segmenter input packing: sentences split after CJK/latin enders, packed
# into chunks of <= CHUNK_CHARS so every model call stays far under the
# 512-wordpiece budget even with ASCII-heavy segments.
_SENT_END = re.compile(r"(?<=[。！？；!?;\n…])")
CHUNK_CHARS = 360
# Per-word cap: guards to_tsvector against pathological tokens (PG errors on
# lexemes > ~2046 bytes); real Chinese words are far below this.
WORD_CAP = 256
# executemany chunk for the tsvector UPDATE (pipelined by asyncpg).
UPDATE_BATCH = 1000
# Between-chunk cool-down for the backfill loop (matches the repo's
# bulk-write rule: one commit per chunk, then pause).
REFRESH_CHUNK_ROWS = 500
REFRESH_CHUNK_PAUSE_S = 0.1

# Query-side model bypass: a text whose every whitespace token is a pure
# CJK run of at most this many chars is already word-segmented by
# construction (an isolated 1-2-char run IS one word — the segmenter
# returns e.g. 茅台/白酒 unchanged), so tokenize_for_search passes it
# through WITHOUT loading the model. Longer runs may hide word boundaries
# (白酒板块 -> 白酒/板块) and keep the model path. 0 disables the bypass.
MODEL_BYPASS_MAX_CJK = 2

# High-DF lexeme stats (text.search_lexicon_df): per searchable table, the
# lexemes with document frequency >= this fraction of the table — the
# overly-common set the MCP text_search ranking strategy reads. A floor,
# not a ranking weight: membership is the only signal.
LEXICON_DF_TABLE = "text.search_lexicon_df"
LEXICON_DF_MIN_RATIO = 0.25


# --------------------------------------------------------------------------- #
#  Skip-word post-process vocabulary (static.keyword_maps category_type=skip)
# --------------------------------------------------------------------------- #
_SKIP_WORDS: "frozenset[str] | None" = None


def _skip_words() -> frozenset:
    """The low-semantic words every token stream here drops (cached once).

    Reads the static.keyword_maps skip slice via
    :func:`_common.keyword_maps.load_skip_words` on first use.  A failed
    load logs once and returns an EMPTY set — unfiltered tokens are always
    better than a broken indexer; the next process start retries.
    """
    global _SKIP_WORDS
    if _SKIP_WORDS is None:
        try:
            from _common.keyword_maps import load_skip_words

            _SKIP_WORDS = frozenset(load_skip_words())
            logger.debug("search indexing: %d skip words loaded",
                         len(_SKIP_WORDS))
        except Exception as exc:  # noqa: BLE001 — degrade, never break
            logger.warning(
                "search indexing: skip-word load failed, tokens unfiltered "
                "(%r)", exc)
            _SKIP_WORDS = frozenset()
    return _SKIP_WORDS


# --------------------------------------------------------------------------- #
#  Per-table tsvector specs (mirror of the DDL comments in the text schema's
#  CREATE TABLE files — keep the two in sync)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TsvSpec:
    """One table's search_tsv contract: PK columns + (column, weight) map."""

    table: str
    pk: tuple[str, ...]
    weighted: tuple[tuple[str, str], ...]

    @property
    def select_cols(self) -> tuple[str, ...]:
        cols = list(self.pk)
        cols += [c for c, _ in self.weighted if c not in cols]
        return tuple(cols)


TSV_SPECS: dict[str, TsvSpec] = {
    "text.news": TsvSpec(
        "text.news", ("title", "source", "date"),
        (("title", "A"), ("content", "C")),
    ),
    "text.news_comments": TsvSpec(
        "text.news_comments", ("source", "comment_id"),
        (("content", "C"),),
    ),
    "text.news_digestions": TsvSpec(
        "text.news_digestions", ("news_id",),
        (("summary", "B"),),
    ),
    "text.llm_qa": TsvSpec(
        "text.llm_qa", ("qa_id",),
        (("question", "A"), ("answer", "B"), ("context", "D")),
    ),
    "text.llm_qa_by_ask": TsvSpec(
        "text.llm_qa_by_ask", ("ask_id",),
        (("question", "A"), ("answer", "B")),
    ),
    "text.llm_qa_by_research": TsvSpec(
        "text.llm_qa_by_research", ("qa_id",),
        (("question", "A"), ("answer", "B"),
         ("intro", "C"), ("optimistic_forecast", "C"),
         ("pessimistic_forecast", "C"), ("risks", "C"),
         ("capitalization", "C"), ("context", "D")),
    ),
}


class SearchIndexer:
    """Tokenize-for-search + weighted search_tsv writes for the text schema.

    Owns the segmenter (injectable; default the process singleton) and the
    per-table :class:`TsvSpec` map (default :data:`TSV_SPECS`).  Obtain via
    :meth:`get`; first parameters win.
    """

    _instance: ClassVar["SearchIndexer | None"] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def get(
        cls,
        segmenter: ChineseWordSegmenter | None = None,
        specs: Mapping[str, TsvSpec] | None = None,
    ) -> "SearchIndexer":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(segmenter=segmenter, specs=specs)
            return cls._instance

    def __init__(
        self,
        segmenter: ChineseWordSegmenter | None = None,
        specs: Mapping[str, TsvSpec] | None = None,
    ) -> None:
        self._segmenter = segmenter
        self._specs: dict[str, TsvSpec] = dict(
            TSV_SPECS if specs is None else specs)

    @property
    def segmenter(self) -> ChineseWordSegmenter:
        """The underlying segmenter (resolved to the singleton on first use)."""
        if self._segmenter is None:
            self._segmenter = ChineseWordSegmenter.get()
        return self._segmenter

    # ------------------------------------------------------- tokenization
    def tokenize_for_search(self, text: str | None) -> str:
        """One text -> space-joined search-token string for
        to_tsvector('simple').  The query-side counterpart of the document
        vectors: raw Chinese in a tsquery matches nothing."""
        return self.tokenize_for_search_batch([text])[0]

    def tokenize_for_search_batch(
        self, texts: Sequence[str | None]
    ) -> list[str]:
        """Texts -> space-joined search-token strings for to_tsvector('simple').

        Chinese (CJK-bearing) texts are chunked, segmented by the word
        segmenter (conservative exception forcing: digit/ASCII runs like
        ``1830.5`` merge, number+unit stays split for recall), filtered
        through the skip vocabulary (low-semantic function words —
        :func:`_skip_words`; whole-token match, case-insensitive for
        ASCII), and word-joined.  Texts whose every token is
        a pure CJK run of <= :data:`MODEL_BYPASS_MAX_CJK` chars bypass the
        model entirely (one word by construction — the 7s first-call model
        load never happens for short-keyword queries).  Non-Chinese texts
        skip the segmenter but still go through the same skip filter and
        per-token capping — the ``simple`` config already handles latin
        text.  None/blank ->
        ``''`` (to_tsvector of '' is an empty tsvector, so the setweight
        chain stays type-safe).

        One segment_batch call for all CJK texts of the batch: large-corpus
        callers get GPU batching and a single summary log line per call.
        """
        skip = _skip_words()
        out: list[str] = [""] * len(texts)
        flat_chunks: list[str] = []
        chunk_counts: list[int] = []
        for i, text in enumerate(texts):
            if not text:
                chunk_counts.append(0)
                continue
            if _CJK.search(text) is None:
                out[i] = " ".join(
                    self._cap_word(tok) for tok in text.split()
                    if tok and tok.lower() not in skip
                )
                chunk_counts.append(0)
                continue
            if MODEL_BYPASS_MAX_CJK and self._model_free_tokens(text):
                out[i] = " ".join(
                    self._cap_word(tok) for tok in text.split()
                    if tok and tok.lower() not in skip
                )
                chunk_counts.append(0)
                continue
            chunks = self._pack_chunks(text)
            flat_chunks.extend(chunks)
            chunk_counts.append(len(chunks))

        if flat_chunks:
            words_per_chunk = self.segmenter.segment_batch(flat_chunks)
            pos = 0
            for i, n in enumerate(chunk_counts):
                if n == 0:
                    continue
                words = [
                    w for chunk in words_per_chunk[pos : pos + n] for w in chunk
                ]
                pos += n
                out[i] = " ".join(
                    self._cap_word(w) for w in words if w and w.lower() not in skip)
        return out

    @staticmethod
    def _cap_word(word: str) -> str:
        return word[:WORD_CAP]

    @staticmethod
    def _model_free_tokens(text: str) -> bool:
        """True when every whitespace token is a pure CJK run of <=
        :data:`MODEL_BYPASS_MAX_CJK` chars — one word each by
        construction, so the segmenter model would return them
        unchanged.  (_CJK is a ONE-char class — fullmatch applies per
        character, not per token.)"""
        toks = text.split()
        return bool(toks) and all(
            len(tok) <= MODEL_BYPASS_MAX_CJK
            and all(_CJK.fullmatch(ch) for ch in tok)
            for tok in toks
        )

    @staticmethod
    def _pack_chunks(text: str) -> list[str]:
        """Split on sentence enders, then pack pieces into <= CHUNK_CHARS
        chunks (a piece longer than the budget — no enders — is hard-split)."""
        pieces = [p for p in _SENT_END.split(text) if p]
        chunks: list[str] = []
        buf = ""
        for piece in pieces:
            while len(piece) > CHUNK_CHARS:
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(piece[:CHUNK_CHARS])
                piece = piece[CHUNK_CHARS:]
            if len(buf) + len(piece) > CHUNK_CHARS and buf:
                chunks.append(buf)
                buf = ""
            buf += piece
        if buf:
            chunks.append(buf)
        return chunks or ([text] if text else [])

    # -------------------------------------------------------------- writes
    async def set_search_tsv_async(
        self,
        conn,
        table: str,
        rows: Sequence[dict[str, Any]],
        *,
        only_missing: bool = False,
    ) -> int:
        """Tokenize row texts and set search_tsv on the rows' PKs.

        *rows* are full or partial row dicts carrying the spec's PK +
        weighted columns (raw texts; tokenization happens here).  NULL/
        missing text contributes an empty tsvector, so a row with only a
        title still gets its A-weight lexemes.  Returns the number of rows
        submitted (executemany does not report per-row counts; with
        ``only_missing=True`` unchanged rows are skipped server-side by the
        IS NULL guard — bulk re-upserts stay cheap and content edits keep
        their stale tsvector until a refresh_missing pass heals them).
        """
        spec = self._specs[table]
        param_rows: list[list[Any]] = []
        usable: list[dict[str, Any]] = []
        for row in rows:
            pk_vals = [row.get(col) for col in spec.pk]
            if any(v is None for v in pk_vals):
                continue  # row without a resolvable PK — nothing to update
            usable.append(row)
            param_rows.append(pk_vals)

        if not usable:
            return 0

        flat_texts = [row.get(col) for row in usable for col, _w in spec.weighted]
        tokens = self.tokenize_for_search_batch(flat_texts)
        n_w = len(spec.weighted)
        for i, params in enumerate(param_rows):
            params[:0] = tokens[i * n_w : (i + 1) * n_w]  # prepend tokenized

        sql = self._update_sql(spec, only_missing)
        for c in range(0, len(param_rows), UPDATE_BATCH):
            await conn.executemany(sql, param_rows[c : c + UPDATE_BATCH])
        return len(param_rows)

    async def refresh_missing_search_tsv_async(
        self,
        conn,
        table: str,
        *,
        chunk_rows: int = REFRESH_CHUNK_ROWS,
    ) -> int:
        """Backfill/heal pass: index rows whose search_tsv IS NULL.

        Resumable (the NULL filter is the work queue), chunked with the
        repo's commit-chunk + cool-down discipline.  To force a full
        re-tokenization, NULL the column for the scope first, then run
        this.
        """
        spec = self._specs[table]
        cols_sql = ", ".join(spec.select_cols)
        total = 0
        while True:
            rows = [dict(r) for r in await conn.fetch(
                f"SELECT {cols_sql} FROM {spec.table} "
                f"WHERE search_tsv IS NULL LIMIT $1", chunk_rows)]
            if not rows:
                break
            n = await self.set_search_tsv_async(conn, table, rows)
            total += n
            await asyncio.sleep(REFRESH_CHUNK_PAUSE_S)
        if total:
            logger.info("search_tsv refresh: %s — %d rows indexed",
                        spec.table, total)
        return total

    async def refresh_lexicon_df_async(self, conn, table: str) -> int:
        """Recompute one table's high-DF lexeme rows in text.search_lexicon_df.

        Full replace in one transaction: DELETE the table's slice, then
        INSERT the lexemes whose document frequency (ts_stat over the whole
        ``search_tsv`` column) is >= :data:`LEXICON_DF_MIN_RATIO` of the
        table's row count.  The result is a tiny table (order 10^2 rows for
        news) the query side reads per search — run it after a tsv write
        pass completes.  Staleness is benign: an unflagged common lexeme
        just keeps the rank-ordered (slower) query path.  Returns the
        number of lexeme rows written.
        """
        from _common.labels import SEARCH_TABLE_CODE

        spec = self._specs[table]
        code = SEARCH_TABLE_CODE[spec.table.rsplit(".", 1)[-1]]
        tbl = spec.table.replace('"', '""')
        total = await conn.fetchval(f"SELECT count(*) FROM {tbl}")
        # threshold precomputed host-side: a bare `$n * $m` in the WHERE
        # is an ambiguous operator for asyncpg's untyped parameters.
        threshold = math.ceil((total or 0) * LEXICON_DF_MIN_RATIO)
        async with conn.transaction():
            await conn.execute(
                f"DELETE FROM {LEXICON_DF_TABLE} WHERE search_table = $1",
                code)
            if not total:
                return 0
            await conn.execute(
                f"INSERT INTO {LEXICON_DF_TABLE} "
                f"(search_table, lexeme, doc_freq, total_docs) "
                f"SELECT $1, word, ndoc, $2 "
                f"FROM ts_stat('SELECT search_tsv FROM {tbl}') "
                f"WHERE ndoc >= $3",
                code, total, threshold)
            n = await conn.fetchval(
                f"SELECT count(*) FROM {LEXICON_DF_TABLE} "
                f"WHERE search_table = $1", code)
        if n:
            logger.info("lexicon DF stats: %s — %d lexemes >= %.0f%% DF "
                        "(of %d rows)", spec.table, n,
                        LEXICON_DF_MIN_RATIO * 100, total)
        return n

    @staticmethod
    def _update_sql(spec: TsvSpec, only_missing: bool) -> str:
        # strip(): drop positions — GIN indexes lexemes only and ts_rank's
        # detoast cost dominated the position payload (module docstring).
        expr = "strip(" + " || ".join(
            f"setweight(to_tsvector('{TSCONFIG}', ${i}), '{w}')"
            for i, (_c, w) in enumerate(spec.weighted, start=1)
        ) + ")"
        pk_terms = " AND ".join(
            f"{col} = ${i}"
            for i, col in enumerate(spec.pk, start=len(spec.weighted) + 1)
        )
        guard = " AND search_tsv IS NULL" if only_missing else ""
        return (f"UPDATE {spec.table} SET search_tsv = {expr} "
                f"WHERE {pk_terms}{guard}")
