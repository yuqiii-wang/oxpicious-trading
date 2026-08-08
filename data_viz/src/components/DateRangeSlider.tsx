/**
 * DateRangeSlider — shared date-range slider with start/end date labels.
 *
 * Renders an MUI Slider bound to a `[startIdx, endIdx]` pair plus two
 * captions resolving each index to its date string. Used by every chart
 * panel that windows its data by date index across dataviz + analysis
 * (EtfMarginPanel, IndexPanel, StockPanel, MaSpreadPanel, PerfAttrPanel,
 * IndustrySentimentsPlot, BenchmarkPriceChart, MarketTrendChart,
 * AnnualSentimentPanel, DebtBaselinePage).
 *
 * The `dates` array is the full x-axis category list (date strings). The
 * slider value is a pair of indices into this array; the labels and the
 * drag tooltip resolve each index back to its date string.
 */
import { Box, Slider, Stack, Typography } from "@mui/material";
import type { SxProps, Theme } from "@mui/material";

interface DateRangeSliderProps {
  /** Current `[startIdx, endIdx]` pair (indices into `dates`). */
  value: [number, number];
  /** Called with the new pair when the user drags either thumb. */
  onChange: (value: [number, number]) => void;
  /** Max index (typically `dates.length - 1`). */
  max: number;
  /** Full date-string array — indexed by slider value for labels. */
  dates: string[];
  /** Min index. Defaults to 0. */
  min?: number;
  /** Hide the slider when there is ≤1 data point (max ≤ 0). Default true. */
  hideWhenSingle?: boolean;
  /** Optional override/extension for the outer Box sx. */
  sx?: SxProps<Theme>;
}

export default function DateRangeSlider({
  value,
  onChange,
  max,
  dates,
  min = 0,
  hideWhenSingle = true,
  sx,
}: DateRangeSliderProps) {
  if (hideWhenSingle && max <= 0) return null;
  return (
    <Box sx={[{ px: 1, mt: 0.5 }, sx] as SxProps<Theme>}>
      <Slider
        value={value}
        onChange={(_, v) => onChange(v as [number, number])}
        min={min}
        max={max}
        size="small"
        valueLabelDisplay="auto"
        valueLabelFormat={(idx) => dates[idx] ?? ""}
        sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
      />
      <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
          {dates[value[0]] ?? "—"}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
          {dates[value[1]] ?? "—"}
        </Typography>
      </Stack>
    </Box>
  );
}
