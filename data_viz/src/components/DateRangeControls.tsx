/**
 * Date range picker + preset chips, wired to the global Zustand store.
 *
 * Presets cover the typical analysis windows used by the Python pipelines:
 *   • 1M / 3M / 6M / 1Y / 3Y / YTD / All
 *
 * The MUI x-date-pickers DatePicker requires a LocalizationProvider wrapping
 * the app — see main.tsx.
 */
import { Box, Chip, Stack, Typography } from "@mui/material";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import dayjs, { type Dayjs } from "dayjs";
import { useStore } from "@/store/filters";

interface Preset {
  label: string;
  getRange: (now: Dayjs) => [Dayjs | null, Dayjs];
}

const PRESETS: Preset[] = [
  { label: "1M", getRange: (n) => [n.subtract(1, "month"), n] },
  { label: "3M", getRange: (n) => [n.subtract(3, "month"), n] },
  { label: "6M", getRange: (n) => [n.subtract(6, "month"), n] },
  { label: "1Y", getRange: (n) => [n.subtract(1, "year"), n] },
  { label: "3Y", getRange: (n) => [n.subtract(3, "year"), n] },
  { label: "5Y", getRange: (n) => [n.subtract(5, "year"), n] },
  { label: "YTD", getRange: (n) => [dayjs(`${n.year()}-01-01`), n] },
  { label: "All", getRange: () => [null, dayjs()] },
];

export default function DateRangeControls() {
  const startDate = useStore((s) => s.startDate);
  const endDate = useStore((s) => s.endDate);
  const setStartDate = useStore((s) => s.setStartDate);
  const setEndDate = useStore((s) => s.setEndDate);
  const setDateRange = useStore((s) => s.setDateRange);

  const startDjs = startDate ? dayjs(startDate) : null;
  const endDjs = endDate ? dayjs(endDate) : dayjs();

  const applyPreset = (p: Preset) => {
    const now = dayjs();
    const [s, e] = p.getRange(now);
    setDateRange(s ? s.format("YYYY-MM-DD") : null, e.format("YYYY-MM-DD"));
  };

  return (
    <Stack
      direction={{ xs: "column", md: "row" }}
      spacing={2}
      alignItems={{ xs: "stretch", md: "center" }}
      sx={{
        p: 1.5,
        mb: 2,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        {PRESETS.map((p) => {
          const active = p.label === "All" ? startDate == null : false;
          return (
            <Chip
              key={p.label}
              label={p.label}
              size="small"
              color={active ? "primary" : "default"}
              variant={active ? "filled" : "outlined"}
              onClick={() => applyPreset(p)}
            />
          );
        })}
      </Stack>

      <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
        <DatePicker
          label="From"
          value={startDjs}
          maxDate={endDjs}
          format="YYYY-MM-DD"
          slotProps={{
            textField: { size: "small", sx: { width: 150 } },
          }}
          onChange={(v) => setStartDate(v ? v.format("YYYY-MM-DD") : null)}
        />
        <Typography variant="body2" color="text.secondary">
          →
        </Typography>
        <DatePicker
          label="To"
          value={endDjs}
          minDate={startDjs ?? undefined}
          format="YYYY-MM-DD"
          slotProps={{
            textField: { size: "small", sx: { width: 150 } },
          }}
          onChange={(v) => setEndDate(v ? v.format("YYYY-MM-DD") : null)}
        />
      </Box>
    </Stack>
  );
}
