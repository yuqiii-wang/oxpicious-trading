/**
 * Derivatives — list of derivatives analyses.
 *
 * Mirrors CommonsPage / CompositesPage structure. Each card navigates to a
 * detail page under /analysis/derivatives/<slug>. Add new derivatives
 * analyses to the DERIVATIVES_ANALYSES array below.
 */
import { Box, Card, CardActionArea, CardContent, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { TrendingUp } from "@mui/icons-material";

interface DerivativesAnalysis {
  /** Slug used in the URL: /analysis/derivatives/<slug>. */
  slug: string;
  /** Display title. */
  title: string;
  /** Short description of what the analysis computes. */
  description: string;
}

export const DERIVATIVES_ANALYSES: DerivativesAnalysis[] = [
  {
    slug: "margin-trends",
    title: "Margin Trends",
    description:
      "Per-security RONGZI (融资) margin flows: price with daily margin " +
      "balance + buy, ma5/ma20/ma60, and slope. ETF + Stock only.",
  },
  {
    slug: "futures",
    title: "Futures Basis Analysis",
    description:
      "CFFEX futures vs underlying: the price curves with the gap vs " +
      "underlying in the tooltip, plus the 20-day rolling correlation.",
  },
  {
    slug: "options",
    title: "Options Analysis",
    description:
      "Open-interest wall, volatility smile, and Greeks (Delta/Theta/Gamma/" +
      "Vega/Rho) for SZSE ETF and CFFEX index options.",
  },
];

export default function DerivativesPage() {
  const navigate = useNavigate();

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        Derivatives
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Derivatives analyses — options, futures, and other derivative instruments.
      </Typography>
      {DERIVATIVES_ANALYSES.length === 0 ? (
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
          <TrendingUp fontSize="small" />
          <Typography variant="body2">
            No derivatives analyses yet — add entries to <code>DERIVATIVES_ANALYSES</code> to populate this page.
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
          {DERIVATIVES_ANALYSES.map((a) => (
            <Card key={a.slug} variant="outlined" sx={{ height: "100%" }}>
              <CardActionArea
                onClick={() => navigate(`/analysis/derivatives/${a.slug}`)}
                sx={{ height: "100%" }}
              >
                <CardContent sx={{ height: "100%", display: "flex", flexDirection: "column", gap: 1 }}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <TrendingUp fontSize="small" color="primary" />
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
