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
      "Toggle ETF / Index / Stock via the top-bar toggle; ThemeSelector + " +
      "CodeSearchBar mirror the other analysis-commons pages " +
      "(Stock is reserved until stock_tech_stats is created). " +
      "Backed by analysis.mov_ave_spreads_detail + _summary.",
    tags: ["ETF", "Index", "Stock", "MA spread", "2-curve + fill"],
  },
  {
    slug: "industry-sentiments",
    title: "Industry Sentiments",
    description:
      "Each industry's member INDEX VALUES plotted directly, rebased to 100 " +
      "at the start of the visible (zoom) window. Rebased-to-100 makes member " +
      "indices comparable regardless of absolute price level. Toggle pool " +
      "size to filter by member count (small <51 stocks, mid <301, large " +
      "otherwise). The dashed mean line and ±1σ band are precomputed " +
      "server-side across member indices per pool_size slice (anchored at " +
      "history start). Broad-market indices (BROAD_CSI/SSE/SZSE/STAR) appear " +
      "under the FIN sector and are aggregated identically. Tooltip shows " +
      "actual close + rebased % + stock_num per index.",
    tags: ["Index", "industry sentiment", "rebased to 100", "multi-line", "pool size", "mean/var overlay"],
  },
  {
    slug: "pe-dividend",
    title: "PE & Dividend",
    description:
      "Per-security valuation: close price (left y-axis) vs PE / PE MA20 + " +
      "trailing-12m dividend yield (right y-axis). Index securities show " +
      "all four series; ETF/Stock show close + dividend_yield only (no PE " +
      "source). Beneath the chart, a monthly 5-year rolling stats table " +
      "(min/max PE 5y, min/max Div 5y, dividend_var_5y, dividend_stability_5y) " +
      "with an is_active flag on the latest snapshot. Click any date on the " +
      "chart to highlight the matching month-end row in the table. Backed by " +
      "analysis.pe_and_dividends + analysis.pe_and_dividend_stats; close + " +
      "raw PE are JOINed live from stats so the UI always shows the freshest " +
      "source values.",
    tags: ["ETF", "Index", "Stock", "PE", "dividend yield", "valuation", "5y stats"],
  },
  {
    slug: "fourier-freqs",
    title: "Fourier Frequencies",
    description:
      "Per-security dominant cycle detection via real FFT on trailing close " +
      "prices. For each trading date, takes the trailing range_days window " +
      "(20/60/255/500/750 days), detrends, applies numpy.rfft, and stores " +
      "the dominant cycle period (freq, in trading days) + its amplitude. " +
      "The chart plots freq over time, one line per window size — revealing " +
      "cycle-regime shifts (e.g. a 255-day window whose dominant period " +
      "collapses from ~60d to ~20d signals a shift from quarterly to " +
      "monthly cyclicality). Y-axis is log-scaled. Index only for now; " +
      "ETF/Stock pending the Python populator. Backed by " +
      "analysis.fourier_freqs.",
    tags: ["Index", "FFT", "cycle period", "frequency analysis", "amplitude"],
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
