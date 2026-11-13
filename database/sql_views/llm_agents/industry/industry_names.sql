-- The industry vocabulary behind the preview text classification's
-- industry-attempt read (deep_agents langgraph decision lane,
-- industry_candidates.py): the DISTINCT industry names the repo's
-- industry study tables key on, so a tool-result text's industry
-- labels always come from the vocabulary the analysis schema itself
-- uses (never a hardcoded list drifting from the data).
-- serves: llm_agents.deep_agents.langgraph.industry_candidates
-- args: none
SELECT DISTINCT name
FROM stats.industry_identity
WHERE name IS NOT NULL AND name <> ''
ORDER BY name
