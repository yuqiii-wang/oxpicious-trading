/**
 * Signals — list of trading-signal analyses.
 *
 * Each card navigates to the signal's detail page. New signals can be
 * added to the SIGNALS_ANALYSES array below.
 */
import { Box, Card, CardActionArea, CardContent, Chip, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { ShowChart } from "@mui/icons-material";

interface SignalItem {
  /** Slug used in the URL: /analysis/signals/<slug>. */
  slug: string;
  /** Display title. */
  title: string;
  /** Short description of what the signal computes. */
  description: string;
  /** Tag chips shown on the card. */
  tags: string[];
}

const SIGNALS_ANALYSES: SignalItem[] = [
  {
    slug: "recent-movements",
    title: "Recent Movements",
    description:
      "Per-security recent price-movement signals. Pick a security via the " +
      "sec_type toggle (ETF / Index / Stock), the CodeSearchBar, or the " +
      "shared SecClassificationNav (L1 sector → L2 industry + parallel " +
      "strategy → theme column, exchange filter, L3 security chips) and the " +
      "page renders the security's daily price-trend chart (OHLC + MAs).",
    tags: ["ETF", "Index", "Stock", "signal", "classification nav"],
  },
];

export default function SignalsPage() {
  const navigate = useNavigate();

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        Signals
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Trading-signal analyses — pick one to drill in.
      </Typography>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "1fr 1fr 1fr" },
          gap: 2,
        }}
      >
        {SIGNALS_ANALYSES.map((a) => (
          <Card key={a.slug} variant="outlined" sx={{ height: "100%" }}>
            <CardActionArea
              onClick={() => navigate(`/analysis/signals/${a.slug}`)}
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
