/**
 * Derivatives — list of derivatives analyses.
 *
 * Mirrors CommonsPage / CompositesPage structure. Each card navigates to a
 * detail page under /analysis/derivatives/<slug>. Add new derivatives
 * analyses to the DERIVATIVES_ANALYSES array below.
 */
import { Box, Card, CardActionArea, CardContent, Chip, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { TrendingUp } from "@mui/icons-material";

interface DerivativesAnalysis {
  /** Slug used in the URL: /analysis/derivatives/<slug>. */
  slug: string;
  /** Display title. */
  title: string;
  /** Short description of what the analysis computes. */
  description: string;
  /** Tag chips shown on the card. */
  tags: string[];
}

const DERIVATIVES_ANALYSES: DerivativesAnalysis[] = [];

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
      )}
    </Box>
  );
}
