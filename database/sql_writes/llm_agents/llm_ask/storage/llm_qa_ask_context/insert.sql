-- SQL write action — llm.llm_qa_ask_context INSERT (the 1:1 chart
-- context row). Executed by llm_agents/llm_ask/storage.py (_persist_ask
-- via _context_row) inside the per-ask transaction: the chart context
-- the question was asked against — hot filter columns + the verbatim
-- plotInfo JSONB.
--
-- Params (asyncpg $n, in order):
--   $1  ask_id       BIGINT — the parent ask row
--   $2  chart_kind   TEXT | NULL — the chart's kind
--   $3  chart_title  TEXT | NULL
--   $4  chart_subtitle TEXT | NULL
--   $5  chart_intro  TEXT | NULL
--   $6  industry_id  TEXT | NULL — the scope's industry pick
--   $7  sector_id    TEXT | NULL — the scope's sector pick
--   $8  window_start TEXT | NULL — the chart window's start
--   $9  window_end   TEXT | NULL — the chart window's end
--   $10 granularity  TEXT | NULL — the window's granularity
--   $11 page         TEXT | NULL — the page the ask came from
--   $12 theme_mode   TEXT — the UI theme the chart rendered in
--   $13 plot_info    JSONB — the verbatim plotInfo payload
--   $14 n_images     INTEGER — how many screenshots the ask carried
INSERT INTO llm.llm_qa_ask_context
    (ask_id, chart_kind, chart_title, chart_subtitle, chart_intro,
     industry_id, sector_id, window_start, window_end, granularity,
     page, theme_mode, plot_info, n_images)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
        $13::jsonb, $14)
