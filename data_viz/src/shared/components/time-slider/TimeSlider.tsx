/**
 * TimeSlider — shared dual-thumb date-window slider (the CodeTrendChart
 * range slider, extracted).
 *
 * Slides over indexes into an ordered `dates` list (one entry per trading
 * day / event date); the thumb value labels and the start/end captions below
 * show the date at each index. Pure presentation + controlled state: the
 * caller owns the `[startIdx, endIdx]` window and applies the slice to its
 * chart/rows.
 */
import { Box, Slider, Stack, Typography } from "@mui/material";
import type { SxProps, Theme } from "@mui/material";

interface Props {
  /** Ordered dates (YYYY-MM-DD) — slider index i maps to dates[i]. */
  dates: string[];
  /** Selected window as inclusive [startIdx, endIdx] indexes into dates. */
  value: [number, number];
  onChange: (range: [number, number]) => void;
  /** Extra styles for the wrapper Box (defaults to px: 1). */
  sx?: SxProps<Theme>;
}

export default function TimeSlider({ dates, value, onChange, sx }: Props) {
  const maxIdx = Math.max(dates.length - 1, 0);
  return (
    <Box sx={{ px: 1, ...sx }}>
      <Slider
        value={value}
        onChange={(_, v) => onChange(v as [number, number])}
        min={0}
        max={maxIdx}
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
