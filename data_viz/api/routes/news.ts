/**
 * News API routes — read access to the text schema (populated by builds.text).
 *
 *   GET /api/news/themes
 *     Sector → industry tree (SectorNode[]) with news counts per industry.
 *     Industry level only — items[] is always empty (no L3 security level).
 *
 *   GET /api/news/calendar?sector_id=&industry_id=&search=&start=&end=
 *     Per-day news counts for the date bar's dots; co-filtered by the
 *     classification scope + keyword search so the bar reacts to the nav.
 *
 *   GET /api/news/items?sector_id=&industry_id=&search=&search_mode=&date=&limit=&offset=
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
import {
  getNewsItem,
  listNewsAuthors,
  listNewsCalendar,
  listNewsComments,
  listNewsItems,
  listNewsStrategyThemes,
  listNewsThemes,
} from "../services/news.service.js";
import { runPythonModule } from "../services/py-runner.service.js";
import type { NewsSearchItem } from "../../shared/types.js";

const router = Router();

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
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

router.get("/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listNewsThemes(str(req.query.source) ?? null, str(req.query.author) ?? null));
  } catch (err) {
    console.error("[news/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listNewsStrategyThemes(str(req.query.source) ?? null, str(req.query.author) ?? null));
  } catch (err) {
    console.error("[news/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/authors", async (req: Request, res: Response) => {
  try {
    res.json(
      await listNewsAuthors({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        search: str(req.query.search) ?? null,
        searchAny: searchMode(req.query.search_mode) === "any",
        source: str(req.query.source) ?? null,
      }),
    );
  } catch (err) {
    console.error("[news/authors] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/calendar", async (req: Request, res: Response) => {
  try {
    res.json(
      await listNewsCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        search: str(req.query.search) ?? null,
        searchAny: searchMode(req.query.search_mode) === "any",
        source: str(req.query.source) ?? null,
        author: str(req.query.author) ?? null,
        start: str(req.query.start) ?? null,
        end: str(req.query.end) ?? null,
      }),
    );
  } catch (err) {
    console.error("[news/calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
    res.json(
      await listNewsItems({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        search: str(req.query.search) ?? null,
        searchAny: searchMode(req.query.search_mode) === "any",
        source: str(req.query.source) ?? null,
        author: str(req.query.author) ?? null,
        date: str(req.query.date) ?? null,
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[news/items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/news/item?news_id= — full article row (untruncated content +
// comment count) for the feed's click-to-expand card.
router.get("/item", async (req: Request, res: Response) => {
  try {
    const newsId = Number(req.query.news_id);
    if (!Number.isFinite(newsId) || newsId <= 0) {
      res.status(400).json({ error: "Missing 'news_id'" });
      return;
    }
    res.json(await getNewsItem(newsId));
  } catch (err) {
    console.error("[news/item] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/news/comments?news_id= — threaded comments of one article
// (roots by votes, replies nested under their root).
router.get("/comments", async (req: Request, res: Response) => {
  try {
    const newsId = Number(req.query.news_id);
    if (!Number.isFinite(newsId) || newsId <= 0) {
      res.status(400).json({ error: "Missing 'news_id'" });
      return;
    }
    res.json(await listNewsComments(newsId));
  } catch (err) {
    console.error("[news/comments] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

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

router.get("/tokenize", async (req: Request, res: Response) => {
  try {
    const textB64 = str(req.query.text_b64);
    if (!textB64) {
      res.status(400).json({ error: "Missing 'text_b64'" });
      return;
    }
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
      res.status(500).json({
        error: "tokenize failed",
        stderr_tail: result.stderr.slice(-1000) || result.stdout.slice(-1000),
      });
      return;
    }
    res.json({ tokens });
  } catch (err) {
    console.error("[news/tokenize] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

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
router.post("/search", async (req: Request, res: Response) => {
  try {
    const question = typeof req.body?.question === "string" ? req.body.question.trim() : "";
    const source = typeof req.body?.source === "string" ? req.body.source.trim() : "";
    const author = typeof req.body?.author === "string" ? req.body.author.trim() : "";

    if (!question) {
      res.status(400).json({ success: false, stderr_tail: "Missing 'question'" });
      return;
    }
    if (!NEWS_SEARCH_ENABLED_SOURCES.has(source)) {
      res.status(400).json({
        success: false,
        stderr_tail: `Source '${source || "(none)"}' is not enabled for question search (enabled: ${[...NEWS_SEARCH_ENABLED_SOURCES].join(", ")})`,
      });
      return;
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
  } catch (err) {
    console.error("[news/search] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

export default router;
