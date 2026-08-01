/**
 * Commons — list of shared analyses.
 *
 * Each card navigates to the analysis's detail page. New analyses can be
 * added to the COMMONS_ANALYSES array below.
 */
import { Box, Card, CardActionArea, CardContent, Chip, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { ShowChart } from "@mui/icons-material";

interface CommonsAnalysis {
  /** Slug used in the URL: /analysis/commons/<slug>. */
  slug: string;
  /** Display title. */
  title: string;
  /** Short description of what the analysis computes. */
  description: string;
  /** Tag chips shown on the card. */
  tags: string[];
}

const COMMONS_ANALYSES: CommonsAnalysis[] = [
  {
    slug: "ma-spread",
    title: "MA-Spread",
    description:
      "Per-security 9-pair grid (5 Price/MA + 4 MA5/MA): each pair shows two curves " +
      "(short + long MA) with green fill when short > long (growth) and red fill " +
      "when short < long (decline). Monthly/quarterly/yearly summary stats per pair. " +
      "Supports ETF, Index, and Stock universes via a left-panel toggle " +
      "(Stock is reserved until stock_tech_stats is created). " +
      "Backed by analysis.mov_ave_spreads_detail + _summary.",
    tags: ["ETF", "Index", "Stock", "MA spread", "2-curve + fill"],
  },
  {
    slug: "perf-attr",
    title: "Sec Allocation Perf Attribution",
    description:
      "Daily return decomposition: ETF subject_return vs index benchmark_return. " +
      "Green fill = ETF outperforms; red fill = ETF underperforms. " +
      "6 broad-market benchmark indices (沪深300, 上证指数, 中证1000, 深证成指, 创业板指, 科创50). " +
      "Also shows amount_ratio = benchmark trading amount (yuan) / ETF trading amount (yuan). " +
      "Backed by analysis.sec_alloc_perf_attribution.",
    tags: ["ETF", "Index", "return decomposition", "2-curve + fill", "trading amount ratio"],
  },
  {
    slug: "capital-flow",
    title: "Industry Capital Flow (Broad-Market Adjusted)",
    description:
      "Captures each industry's trending popularity after stripping the dilution from " +
      "broad-market ETFs that share overlapping stock holdings with the industry. " +
      "Pure metrics: pure_flow, pure_growth, pure_popularity, observed_popularity, " +
      "popularity_retention. Browse industries (sorted by latest pure popularity), " +
      "drill into a (industry × benchmark) pair to view time-series in three modes " +
      "(popularity / returns / retention). " +
      "Backed by analysis.capital_flow.",
    tags: ["ETF", "Index", "industry popularity", "overlap-weighted", "broad-market adjustment"],
  },
];

export default function CommonsPage() {
  const navigate = useNavigate();

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        Commons
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Shared analyses — pick one to drill in.
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "1fr 1fr 1fr" },
          gap: 2,
        }}
      >
        {COMMONS_ANALYSES.map((a) => (
          <Card key={a.slug} variant="outlined" sx={{ height: "100%" }}>
            <CardActionArea
              onClick={() => navigate(`/analysis/commons/${a.slug}`)}
              sx={{ height: "100%" }}
            >
              <CardContent sx={{ height: "100%", display: "flex", flexDirection: "column", gap: 1 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <ShowChart fontSize="small" color="primary" />
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                    {a.title}
                  </Typography>
                </Box>
                <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                  {a.description}
                </Typography>
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                  {a.tags.map((t) => (
                    <Chip
                      key={t}
                      label={t}
                      size="small"
                      variant="outlined"
                      sx={{ fontSize: "0.7rem" }}
                    />
                  ))}
                </Box>
              </CardContent>
            </CardActionArea>
          </Card>
        ))}
      </Box>
    </Box>
  );
}
