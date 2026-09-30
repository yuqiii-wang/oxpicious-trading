/**
 * CodeTrendExpansionChart — the ONE shared closeable daily-trend expansion
 * used wherever a click opens a single code's chart beneath a parent view:
 *
 *   · LinkedEtfsFeedersTable row/sub-row click (unified ETF→feeder table)
 *   · CompositionPieChart slice click (via StockOhlcExpansionChart config)
 *
 * Thin chrome wrapper over the shared CodeTrendChart base: it configures
 * the sec_type source (fetch via /api/sec-baseline), adds the close button
 * to the header and an optional subtitle extra (e.g. holding weight).
 * secType accepts etf | index | stock — feeder funds use "etf" (their NAV
 * series is loaded into stats.etf_identity by builds.etf.feeder_nav).
 */
import { Box, IconButton } from "@mui/material";
import { Close } from "@mui/icons-material";
import { CodeTrendChart } from "@/ui/OhlcChart";
import type { CodeTrendSecType } from "@/ui/OhlcChart";

interface Props {
  /** Security type — selects the baseline endpoint + row mapping. */
  secType: CodeTrendSecType;
  /** Code — bare ("510880" ETF/fund, "012761" OTC feeder) or suffixed (stocks). */
  code: string;
  /** Display name (shown in the chart header). */
  name: string;
  /** Close (×) button handler — parent also toggles the source off. */
  onClose: () => void;
  /** aria-label for the close button (defaults "close code trend chart"). */
  closeLabel?: string;
  /** Extra subtitle fragment (e.g. "5.23% holding"). */
  subtitleExtra?: string;
  /** Chart height in px (defaults 310). */
  height?: number;
}

export default function CodeTrendExpansionChart({
  secType,
  code,
  name,
  onClose,
  closeLabel = "close code trend chart",
  subtitleExtra,
  height = 310,
}: Props) {
  return (
    <Box sx={{ mt: 1 }}>
      <CodeTrendChart
        secType={secType}
        code={code}
        name={name}
        height={height}
        subtitleExtra={subtitleExtra}
        headerAction={
          <IconButton aria-label={closeLabel} onClick={onClose} size="small">
            <Close fontSize="small" />
          </IconButton>
        }
      />
    </Box>
  );
}
