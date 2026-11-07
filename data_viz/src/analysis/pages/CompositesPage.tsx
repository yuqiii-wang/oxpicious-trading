/**
 * Composites — list of composite analyses.
 *
 * Mirrors CommonsPage structure. Each card navigates to a detail page under
 * /analysis/composites/<slug>. Add new composite analyses to the
 * COMPOSITE_ANALYSES array below.
 */
import { Box, Card, CardActionArea, CardContent, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { Layers } from "@mui/icons-material";

interface CompositeAnalysis {
  /** Slug used in the URL: /analysis/composites/<slug>. */
  slug: string;
  /** Display title. */
  title: string;
  /** Short description of what the analysis computes. */
  description: string;
}

export const COMPOSITE_ANALYSES: CompositeAnalysis[] = [
  {
    slug: "opposite-industry-correlations",
    title: "Opposite Industry Correlations",
    description:
      "Industry MA trends with the broad-market benchmark removed, auditing " +
      "raw vs benchmark-removed correlation and the opposite score over " +
      "20/60/255-day windows.",
  },
  {
    slug: "ai-market-qa",
    title: "AI Market Sentiments QA (AI 市场情绪问答)",
    description:
      "The LLM Q&A knowledge base feed (llm.llm_qa): one ask-and-answer " +
      "per industry per day, scoped by the classification pick or the " +
      "top-ranked HYPE/DRAIN industries, over the benchmark price chart. " +
      "The ask is the keyword audit over the Q&A corpus — per-day " +
      "occurrences of the market-description keyword families (下跌/上涨, " +
      "放量/缩量, 利空/利好) with 10y rarity ranks, read against the news " +
      "audit for tone divergence.",
  },
  {
    slug: "market-sentiment-news",
    title: "Market Sentiments News (市场情绪新闻)",
    description:
      "The raw news corpus feed (text.news: zhihu recaps, gov policy, " +
      "PBoC ops) under the same scope, day pick, and keyword search. The " +
      "ask is the keyword audit — per-day occurrence counts of the " +
      "market-description keyword families (下跌/上涨, 放量/缩量, 利空/利好) " +
      "with 10y rarity ranks.",
  },
];

export default function CompositesPage() {
  const navigate = useNavigate();

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        Composites
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Composite analyses — multi-factor blends and combined views.
      </Typography>
      {COMPOSITE_ANALYSES.length === 0 ? (
        <Box
          sx={{
            p: 4,
            border: "1px dashed",
            borderColor: "divider",
            borderRadius: 1,
            color: "text.secondary",
            display: "flex",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Layers fontSize="small" />
          <Typography variant="body2">
            No composite analyses yet — add entries to <code>COMPOSITE_ANALYSES</code> to populate this page.
          </Typography>
        </Box>
      ) : (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "1fr 1fr 1fr" },
            gap: 2,
          }}
        >
          {COMPOSITE_ANALYSES.map((a) => (
            <Card key={a.slug} variant="outlined" sx={{ height: "100%" }}>
              <CardActionArea
                onClick={() => navigate(`/analysis/composites/${a.slug}`)}
                sx={{ height: "100%" }}
              >
                <CardContent sx={{ height: "100%", display: "flex", flexDirection: "column", gap: 1 }}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <Layers fontSize="small" color="primary" />
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                      {a.title}
                    </Typography>
                  </Box>
                  <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                    {a.description}
                  </Typography>
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Box>
      )}
    </Box>
  );
}
