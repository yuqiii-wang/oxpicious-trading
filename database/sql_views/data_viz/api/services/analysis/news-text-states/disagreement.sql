-- News Text States — daily lexicon-vs-LLM disagreement counts (the
-- ambiguity flag): per corpus day, how many CFSD-scored articles
-- disagree with their LLM digestion score (sign mismatch AND
-- |llm-lex| >= 2 — the flag that halves the article's weight in the
-- daily blend). FAKE-NEWS EXCLUDED (2026-11-22): the digestions join
-- drops is_fake_news articles, matching the daily blend's population —
-- the blend scores non-fake articles only, so the baseline must count
-- the same set. The client reads the recent share against the trailing
-- baseline (Isakin-style ambiguity: disagreement = reduced conviction,
-- never a direction).
-- Serves GET /api/analysis/news-text-states/disagreement
-- args:
--   window_days: value int — how many corpus days back to return
    SELECT
      EXTRACT(EPOCH FROM n.date)::float8 AS date_epoch,
      COUNT(*)                           AS scored_count,
      COUNT(*) FILTER (WHERE l.disagrees) AS disagree_count
    FROM text.news_lexicon_scores l
    JOIN text.news n ON n.news_id = l.news_id
    JOIN text.news_digestions d ON d.news_id = n.news_id
    WHERE n.date >= CURRENT_DATE - {{window_days}}::int
      AND COALESCE(d.is_fake_news, false) = false
    GROUP BY n.date
    ORDER BY n.date
