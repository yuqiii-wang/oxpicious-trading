/**
 * StockOhlcExpansionChart — closeable daily OHLC expansion for a single
 * stock, rendered below the composition pie chart when the user clicks a
 * stock slice in Layer 2.
 *
 * Thin chrome wrapper over the shared `CodeTrendChart` base component: it
 * configures the stock source, adds the close button to the header and the
 * holding weight to the subtitle. The chart itself (OHLC bars + MAs + PE +
 * dividends, fetched from /api/stock-baseline) is CodeTrendChart's default
 * setup, identical to the Stock Baseline page's StockPanel and the Recent
 * Movements signal page.
 *
 * The close (×) button calls `onClose`; the parent CompositionPieChart also
 * toggles the slice off when the same stock is clicked again.
 */
import { Box, IconButton } from "@mui/material";
import { Close } from "@mui/icons-material";
import CodeTrendChart from "@/components/CodeTrendChart";

interface Props {
  /** Stock code — suffixed ("000001.SZ") or bare ("000001"). */
  code: string;
  /** Display name (from the pie chart's stock_name). */
  name: string;
  /** Weight % the stock held in the parent ETF/index (for the subtitle). */
  weightPct?: number;
  onClose: () => void;
}

export default function StockOhlcExpansionChart({
  code,
  name,
  weightPct,
  onClose,
}: Props) {
  return (
    <Box sx={{ mt: 1 }}>
      <CodeTrendChart
        secType="stock"
        code={code}
        name={name}
        height={310}
        subtitleExtra={weightPct != null ? `${weightPct.toFixed(2)}% holding` : undefined}
        headerAction={
          <IconButton aria-label="close stock chart" onClick={onClose} size="small">
            <Close fontSize="small" />
          </IconButton>
        }
      />
    </Box>
  );
}
