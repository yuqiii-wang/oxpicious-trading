/**
 * News API routes — read access to the text schema (populated by builds.text).
 *
 *   GET /api/news/themes
 *     Sector → industry tree (SectorNode[]) with news counts per industry.
 *     Industry level only — items[] is always empty (no L3 security level).
 *
 *   GET /api/news/calendar?sector_id=&industry_id=&industry_ids=&search=&start=&end=
 *     Per-day news counts for the date bar's dots; co-filtered by the
 *     classification scope + keyword search so the bar reacts to the nav.
 *     industry_ids (comma-joined, present-even-empty) is the explicit-set
 *     scope used by the composites page's multi-select / ranked-industries
 *     scopes — same semantics as the AI routes'.
 *
 *   GET /api/news/items?sector_id=&industry_id=&industry_ids=&search=&search_mode=&date=&limit=&offset=
 *     One page of articles for the same filters (+ optional single-day pick).
 *     search_mode=any ORs the whitespace terms (tokenized question search)
 *     instead of the default AND.
 *
 *   GET /api/news/tokenize?text_b64=
 *     Naive tokenization of a question into news-taxonomy keywords
 *     (builds.text.tokenize via WSL python) — feeds the question bar's
 *     LOCAL search.
 *
 *   GET /api/news/item?news_id= / GET /api/news/comments?news_id=
 *     Full article row (untruncated content + comment_count) and the
 *     threaded comment tree for one article — the shared NewsFeedPage's
 *     click-to-expand card lazily fetches these.
 *
 *   POST /api/news/search  body: { question, source, author }
 *     Interactive ONLINE question search (the News page's question bar):
 *     spawns the source's downloader in one-off question mode via the shared
 *     WSL py-runner, WAITS for it to exit, parses the marker-prefixed JSON
 *     envelope from stdout and returns the raw items. Only sources in
 *     NEWS_SEARCH_ENABLED_SOURCES are accepted (currently zhihu).
 */
import { Router, type Request, type Response } from "express";
import { boolParam, HttpError, intParam, sendError, wrapAsync } from "../lib/http.js";
import {
  getNewsItem,
  listNewsAuthors,
  listNewsCalendar,
  listNewsComments,
  listNewsItems,
  listNewsStrategyThemes,
  listNewsThemes,
} from "../services/market/news.service.js";
import { segmentSearchTerms } from "../services/infra/search-query.service.js";
import { runPythonModule } from "../services/infra/py-runner.service.js";
import type { NewsSearchItem } from "../../shared/types.js";

/** Word cap for the /search-tokens fallback list — enough OR terms for
 *  recall, small enough for the UI's Local chip to stay readable. */
const SEARCH_TOKENS_MAX_WORDS = 12;

const router = Router();

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
}

/** industry_ids list — comma-joined. PRESENT (even empty) scopes to exactly
 *  that set (empty → no rows); ABSENT leaves the scope to the other params.
 *  Same semantics as the AI routes' industry_ids. */
function industryIds(v: unknown): string[] | null {
  if (v === undefined) return null;
  return String(v).split(",").map((s) => s.trim()).filter(Boolean);
}

/** Keyword-search term combinator: "any" (OR across terms — used by the
 *  question bar's tokenized local search) or the default "all" (AND). */
function searchMode(v: unknown): "any" | "all" {
  return str(v) === "any" ? "any" : "all";
}

/** Sources that support the interactive question search — the News page
 *  disables every other source chip while the question bar has input.
 *  Mirrored UI-side (NewsPage QUESTION_SEARCH_ENABLED_SOURCES). */
const NEWS_SEARCH_ENABLED_SOURCES = new Set<string>(["zhihu"]);

/** Python-side marker (downloads/macro/zhihu/news SEARCH_RESULT_MARKER) —
 *  the logger stream shares stdout, so scan for the LAST marker line. */
const SEARCH_RESULT_MARKER = "@@NEWS_SEARCH_JSON@@";

router.get("/themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listNewsThemes(str(req.query.source) ?? null, str(req.query.author) ?? null));
}));

router.get("/strategy-themes", wrapAsync(async (req: Request, res: Response) => {
  res.json(await listNewsStrategyThemes(str(req.query.source) ?? null, str(req.query.author) ?? null));
}));

router.get("/authors", wrapAsync(async (req: Request, res: Response) => {
  res.json(
    await listNewsAuthors({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      search: str(req.query.search) ?? null,
      searchAny: searchMode(req.query.search_mode) === "any",
      source: str(req.query.source) ?? null,
    }),
  );
}));

router.get("/calendar", wrapAsync(async (req: Request, res: Response) => {
  res.json(
    await listNewsCalendar({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      industryIds: industryIds(req.query.industry_ids),
      search: str(req.query.search) ?? null,
      searchAny: searchMode(req.query.search_mode) === "any",
      source: str(req.query.source) ?? null,
      author: str(req.query.author) ?? null,
      bigFluctuationOnly: boolParam(req, "is_big_fluctuation") ?? false,
      start: str(req.query.start) ?? null,
      end: str(req.query.end) ?? null,
    }),
  );
}));

router.get("/items", wrapAsync(async (req: Request, res: Response) => {
  res.json(
    await listNewsItems({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      industryIds: industryIds(req.query.industry_ids),
      search: str(req.query.search) ?? null,
      searchAny: searchMode(req.query.search_mode) === "any",
      source: str(req.query.source) ?? null,
      author: str(req.query.author) ?? null,
      bigFluctuationOnly: boolParam(req, "is_big_fluctuation") ?? false,
      date: str(req.query.date) ?? null,
      dateFrom: str(req.query.date_from) ?? null,
      dateTo: str(req.query.date_to) ?? null,
      limit: intParam(req, "limit") ?? null,
      offset: intParam(req, "offset") ?? null,
    }),
  );
}));

// GET /api/news/item?news_id= — full article row (untruncated content +
// comment count) for the feed's click-to-expand card.
router.get("/item", wrapAsync(async (req: Request, res: Response) => {
  const newsId = Number(req.query.news_id);
  if (!Number.isFinite(newsId) || newsId <= 0) throw HttpError.missingParam("news_id");
  res.json(await getNewsItem(newsId));
}));

// GET /api/news/comments?news_id= — threaded comments of one article
// (roots by votes, replies nested under their root).
router.get("/comments", wrapAsync(async (req: Request, res: Response) => {
  const newsId = Number(req.query.news_id);
  if (!Number.isFinite(newsId) || newsId <= 0) throw HttpError.missingParam("news_id");
  res.json(await listNewsComments(newsId));
}));

// ---------------------------------------------------------------------------
//  Naive tokenization for the question bar's LOCAL search.
//
//  Spawns `python -m builds.text.tokenize --text-b64 <q>` via the shared WSL
//  py-runner: splits the raw question into curated news-taxonomy keywords
//  (builds.text.keywords) that then drive the SAME /api/news/items keyword
//  search the old header bar drove (with search_mode=any — OR across tokens).
//  Deduped by a fixed process-id-tag (tokenization is pure CPU, ~seconds).
// ---------------------------------------------------------------------------
const TOKENIZE_RESULT_MARKER = "@@NEWS_TOKENIZE_JSON@@";

router.get("/tokenize", wrapAsync(async (req: Request, res: Response) => {
  const textB64 = str(req.query.text_b64);
  if (!textB64) throw HttpError.missingParam("text_b64");
  const result = await runPythonModule(
    "builds.text.tokenize",
    ["--text-b64", textB64],
    { processIdTag: "news-tokenize" },
  );
  let tokens: string[] | null = null;
  const idx = result.stdout.lastIndexOf(TOKENIZE_RESULT_MARKER);
  if (idx >= 0) {
    try {
      const payload = JSON.parse(result.stdout.slice(idx + TOKENIZE_RESULT_MARKER.length));
      tokens = Array.isArray(payload?.tokens) ? payload.tokens : null;
    } catch {
      tokens = null;
    }
  }
  if (tokens === null) {
    sendError(res, 500, "tokenize failed", "UPSTREAM_FAILED", {
      stderr_tail: result.stderr.slice(-1000) || result.stdout.slice(-1000),
    });
    return;
  }
  res.json({ tokens });
}));

// ---------------------------------------------------------------------------
//  Segmented search tokens for the question bar's full-text FALLBACK.
//
//  When the taxonomy tokenizer matches nothing, the page searches the
//  question's StructBERT-segmented WORDS instead (search_mode=any ORs
//  them): a whole raw sentence as ONE term would AND all its words in the
//  GIN search_tsv and match nothing. Spawns the shared query segmenter
//  (python -m _common.tokenization via search-query.service — cached per
//  search string) and flattens the per-term token strings into an ordered
//  word list.
// ---------------------------------------------------------------------------
router.get("/search-tokens", wrapAsync(async (req: Request, res: Response) => {
  const textB64 = str(req.query.text_b64);
  if (!textB64) throw HttpError.missingParam("text_b64");
  const raw = Buffer.from(textB64, "base64").toString("utf8");
  const segmented = await segmentSearchTerms(raw);
  if (segmented === null) {
    sendError(res, 500, "segmentation failed", "UPSTREAM_FAILED");
    return;
  }
  const words: string[] = [];
  for (const tokens of segmented) {
    for (const w of (tokens ?? "").split(/\s+/)) {
      // Single-character words are segmentation noise in an OR search
      // (六代机 -> 六/代/机 floods the results with 机) — keep >= 2 chars.
      if (w.length >= 2 && !words.includes(w)) words.push(w);
    }
  }
  res.json({ tokens: words.slice(0, SEARCH_TOKENS_MAX_WORDS) });
}));

// ---------------------------------------------------------------------------
//  Interactive question search (News page question bar).
//
//  Spawns `python -m downloads.macro.zhihu.news --question-b64 <q>
//  [--author-b64 <a>] --count 10` via the shared WSL py-runner and waits.
//  Question/author travel base64-encoded because runPythonModule joins argv
//  with spaces inside a bash -lc command line — raw free text (spaces,
//  quotes, $) would be mangled by shell parsing. Deduped by process-id-tag
//  `news-search:<source>`: a second submit while one is in flight resolves
//  immediately with `already_running: true`.
// ---------------------------------------------------------------------------
router.post("/search", wrapAsync(async (req: Request, res: Response) => {
  const question = typeof req.body?.question === "string" ? req.body.question.trim() : "";
  const source = typeof req.body?.source === "string" ? req.body.source.trim() : "";
  const author = typeof req.body?.author === "string" ? req.body.author.trim() : "";

  if (!question) throw HttpError.missingParam("question");
  if (!NEWS_SEARCH_ENABLED_SOURCES.has(source)) {
    throw new HttpError(
      400,
      "INVALID_PARAM",
      `Source '${source || "(none)"}' is not enabled for question search (enabled: ${[...NEWS_SEARCH_ENABLED_SOURCES].join(", ")})`,
    );
  }

  const tag = `news-search:${source}`;
  const args = [
    "--question-b64", Buffer.from(question, "utf8").toString("base64"),
    "--count", "10",
  ];
  if (author) {
    args.push("--author-b64", Buffer.from(author, "utf8").toString("base64"));
  }
  console.log(
    `[news/search] python -m downloads.macro.zhihu.news --question "${question}" source=${source} author=${author || "-"}`,
  );

  const result = await runPythonModule("downloads.macro.zhihu.news", args, {
    processIdTag: tag,
  });

  // Parse the result envelope: last marker-prefixed line of stdout.
  let payload: {
    success?: boolean;
    total_returned?: number;
    returned?: number;
    out_file?: string;
    items?: NewsSearchItem[];
  } | null = null;
  const idx = result.stdout.lastIndexOf(SEARCH_RESULT_MARKER);
  if (idx >= 0) {
    try {
      payload = JSON.parse(result.stdout.slice(idx + SEARCH_RESULT_MARKER.length));
    } catch {
      payload = null;
    }
  }

  res.json({
    success: result.success && payload?.success === true,
    already_running: result.already_running === true,
    question,
    source,
    author: author || null,
    total: payload?.returned ?? 0,
    total_returned: payload?.total_returned ?? 0,
    out_file: payload?.out_file ?? null,
    items: payload?.items ?? [],
    stdout_tail: result.stdout.slice(-2000),
    stderr_tail: result.stderr.slice(-2000),
  });
}));

export default router;
