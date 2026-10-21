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
    title: "Short-Term Tech",
    description:
      "Per-security recent price-movement signals. Pick a security via the " +
      "sec_type toggle (ETF / Index / Stock), the CodeSearchBar, or the " +
      "shared SecClassificationNav (L1 sector → L2 industry + parallel " +
      "strategy → theme column, exchange filter, L3 security chips) and the " +
      "page renders the security's daily price-trend chart (OHLC + MAs).",
    tags: ["ETF", "Index", "Stock", "signal", "classification nav"],
  },
  {
    slug: "hedge-pairs",
    title: "Hedge Pairs",
    description:
      "Industry-pair hedge view on the SAME multi-select classification " +
      "nav as Composites (industries + strategy themes, no per-index " +
      "chips). Its nav carries a Pair-mode toggle (only here): pair mode " +
      "allows exactly two picks — a 3rd pick auto-forgets the oldest; " +
      "pick 1 = subject (hedging instrument), pick 2 = benchmark, with a " +
      "role-swap button (default 红利 vs 科技). The upper plot — the " +
      "PAIR PORTFOLIO chart — draws both industries' composite close " +
      "curves on TWO separate y-axes (the MA-Spread dual-axis chart " +
      "style) with the portfolio's signal events as buy/sell/wait " +
      "markers; the lower plot charts the pair's hedge ratio (raw / " +
      "market-offset, trailing 20/60/255d windows). The hedge_pairs " +
      "SIGNAL tier frames the plots (market-decoupled: entries pair " +
      "the legs' own Short-Term-Tech RSI capitulations within ~5 " +
      "days, never a market gate): the pair portfolio's managed RULE " +
      "GROUPS above (the shared portfolio-signal component takes the " +
      "pair's two industry codes) and the replayed event HISTORY " +
      "below.",
    tags: ["Index", "signal", "industry pair", "pair mode", "hedge ratio", "dual axis", "signal rules + history"],
  },
  {
    slug: "options",
    title: "Options",
    description:
      "Options signals — pick the underlying via the venue toggle " +
      "(ETF · SZSE / ETF · SSE / Index · CFFEX) and the Underlying " +
      "dropdown: the page renders the OI-weighted Moneyness Skew panel " +
      "(Underlying Price & Positioning) and the OPTIONS-WALL breach " +
      "signals' own UI (the signal-marked trend with the grey-diamond " +
      "no-action markers, and the shared signal-history table with the " +
      "signal-type / sub-signal-type dropdowns — the family fixed as " +
      "the type, its sub types narrowing the bars, rows and markers " +
      "together) — the options_wall family lives here, never mixed " +
      "into the shared signals pages.",
    tags: ["ETF", "Index", "signal", "options", "OI skew", "options wall"],
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
