/**
 * AI — the Analysis app's AI section (/analysis/ai).
 *
 * The section lands directly on the Analysis Summary body. The former
 * "Thread Graph" tab was folded into it — the Summary History strip's
 * dot click now opens the run's thread graph beneath the bar. Add tab
 * entries back here when a second AI view arrives.
 */
import { Box, Typography } from "@mui/material";
import AnalysisSummaryPage from "./AnalysisSummaryPage";

export default function AiPage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        AI
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        AI-assisted analysis — generated summaries over the picked analysis items.
      </Typography>
      <AnalysisSummaryPage />
    </Box>
  );
}
