/**
 * Commons — list of shared analyses.
 *
 * Each card navigates to the analysis's detail page. New analyses can be
 * added to the COMMONS_ANALYSES array below.
 */
import { Box, Card, CardActionArea, CardContent, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { ShowChart } from "@mui/icons-material";

interface CommonsAnalysis {
  /** Slug used in the URL: /analysis/commons/<slug>. */
  slug: string;
  /** Display title. */
  title: string;
  /** Short description of what the analysis computes. */
  description: string;
}

const COMMONS_ANALYSES: CommonsAnalysis[] = [
  {
    slug: "ma-spread",
    title: "MA-Spread",
    description:
      "Per-security 9-pair MA grid (5 price/MA + 4 MA5/MA): short vs long " +
      "curves with growth/decline fill and monthly/quarterly/yearly stats.",
  },
  {
    slug: "industry-sentiments",
    title: "Industry Sentiments",
    description:
      "Each industry's member indices rebased to 100 over the visible " +
      "window, with the mean line and ±1σ band per pool size.",
  },
  {
    slug: "pe",
    title: "PE",
    description:
      "Per-security close vs PE and trailing-12m dividend yield, with the " +
      "band-break valuation-streaks card.",
  },
  {
    slug: "dividends",
    title: "Dividends",
    description:
      "Per-security dividend view: ex-dividend dates marked on the price " +
      "chart (ETF/Stock) or the OHLC + PE baseline (Index).",
  },
  {
    slug: "etf-holdings",
    title: "ETF Holdings",
    description:
      "Browse the ETF classification nav and drill into each ETF's stock " +
      "weights and composition, falling back to the tracking index's " +
      "constituents.",
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
              </CardContent>
            </CardActionArea>
          </Card>
        ))}
      </Box>
    </Box>
  );
}
