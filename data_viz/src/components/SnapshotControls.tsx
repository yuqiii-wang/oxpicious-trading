/**
 * Underlying ETF selector + Snapshot Date picker for SZSE Options page.
 *
 * The snapshot date controls only the Volatility Smile + Market Interest Wall
 * panels — trend plots (Annual Sentiment) use the full data range.
 *
 * Also exports autoDeriveSnapshots() for the StatTable's 4 snapshot columns.
 */
import {
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import dayjs, { type Dayjs } from "dayjs";
import { useStore } from "@/store/filters";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";

interface UnderlyingOpt {
  code: string;
  name: string;
}

interface Props {
  underlyings: UnderlyingOpt[];
  dates: string[];
  selectedDate: string;
  onSelectedDateChange: (date: string) => void;
}

function pickFirstOnOrAfter(dates: string[], target: string): string {
  const sorted = [...dates].sort();
  for (const d of sorted) if (d >= target) return d;
  return sorted[sorted.length - 1] ?? "";
}

/**
 * Auto-derive 4 snapshot dates from the available data — mirrors
 * get_snapshot_dates() in plot_szse_options.py.
 */
export function autoDeriveSnapshots(dates: string[]): { label: string; date: string }[] {
  if (dates.length === 0) {
    return [
      { label: "Q4 Start", date: "" },
      { label: "Last Quarter", date: "" },
      { label: "Last Month", date: "" },
      { label: "Latest", date: "" },
    ];
  }
  const sorted = [...dates].sort();
  const latest = sorted[sorted.length - 1];
  const latestD = dayjs(latest);

  // Last month start: first trading day of the previous month
  const lastMonthStart = latestD.subtract(1, "month").startOf("month");
  const lm = pickFirstOnOrAfter(sorted, lastMonthStart.format("YYYY-MM-DD"));

  // Last quarter start: first trading day of the first month of the previous quarter
  const curQ = Math.floor((latestD.month() - 1) / 3); // 0..3
  const prevQStartMonth = ((curQ - 1 + 4) % 4) * 3; // 0..11
  const prevQYear = curQ === 0 ? latestD.year() - 1 : latestD.year();
  const lqTarget = dayjs(`${prevQYear}-${String(prevQStartMonth + 1).padStart(2, "0")}-01`);
  const lq = pickFirstOnOrAfter(sorted, lqTarget.format("YYYY-MM-DD"));

  // Q4 start: first trading day of October of the previous year
  const q4Target = dayjs(`${latestD.year() - 1}-10-01`);
  const q4 = pickFirstOnOrAfter(sorted, q4Target.format("YYYY-MM-DD"));

  return [
    { label: "Q4 Start", date: q4 },
    { label: "Last Quarter", date: lq },
    { label: "Last Month", date: lm },
    { label: "Latest", date: latest },
  ];
}

export default function SnapshotControls({ underlyings, dates, selectedDate, onSelectedDateChange }: Props) {
  const underlyingCode = useStore((s) => s.underlyingCode);
  const setUnderlyingCode = useStore((s) => s.setUnderlyingCode);

  const minDate = dates.length > 0 ? dayjs(dates[0]) : undefined;
  const maxDate = dates.length > 0 ? dayjs(dates[dates.length - 1]) : undefined;

  return (
    <Stack
      direction={{ xs: "column", md: "row" }}
      spacing={2}
      alignItems={{ xs: "stretch", md: "center" }}
      flexWrap="wrap"
      useFlexGap
      sx={{
        p: 1.5,
        mb: 2,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
      }}
    >
      <FormControl size="small" sx={{ minWidth: 220 }}>
        <InputLabel>Underlying ETF</InputLabel>
        <Select
          value={underlyingCode}
          label="Underlying ETF"
          onChange={(e) => setUnderlyingCode(e.target.value)}
        >
          {underlyings.map((u) => (
            <MenuItem key={u.code} value={u.code}>
              {UNDERLYING_LABELS[u.code] ?? u.name} ({u.code})
            </MenuItem>
          ))}
        </Select>
      </FormControl>

      <Typography variant="subtitle2" sx={{ fontWeight: 600, minWidth: 110 }}>
        Snapshot Date
      </Typography>

      <DatePicker
        label="Select date"
        value={selectedDate ? dayjs(selectedDate) : null}
        format="YYYY-MM-DD"
        minDate={minDate}
        maxDate={maxDate}
        slotProps={{
          textField: { size: "small", sx: { width: 180 } },
        }}
        onChange={(v: Dayjs | null) => onSelectedDateChange(v ? v.format("YYYY-MM-DD") : "")}
      />

      <Typography variant="caption" color="text.secondary">
        Controls Volatility Smile + Market Interest Wall only — trend plots use full history
      </Typography>
    </Stack>
  );
}
