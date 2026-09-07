/**
 * DateSelector — shared single-day picker (Material Design).
 *
 * A MUI x-date-pickers calendar with year → month → day views; pass
 * `showTime` for the DateTimePicker variant that adds the HH:mm clock
 * view (value format then "YYYY-MM-DD HH:mm").
 *
 * Controlled:
 *   • `value` = concrete date, or null = "the default" (e.g. biz today /
 *     latest available) — the field then displays `defaultDate`.
 *   • `onChange` fires with the picked date, or with null when the user
 *     picks the `defaultDate` entry (or clears the field), so "back to
 *     live/latest" stays one callback (pages keep null = live semantics
 *     without extra mapping).
 *   • `dates` (optional roster of available days, any order) bounds the
 *     calendar and disables days without data, so only real trading days
 *     are selectable.
 */
import { Tooltip } from "@mui/material";
import type { SxProps, Theme } from "@mui/material";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import { DateTimePicker } from "@mui/x-date-pickers/DateTimePicker";
import dayjs, { type Dayjs } from "dayjs";

const DATE_FMT = "YYYY-MM-DD";
const DATE_TIME_FMT = "YYYY-MM-DD HH:mm";

interface Props {
  /** Selected date ("YYYY-MM-DD", or "YYYY-MM-DD HH:mm" with showTime);
   *  null = the default (`defaultDate`). */
  value: string | null;
  /** Picked date; null when the `defaultDate` entry was picked/cleared. */
  onChange: (date: string | null) => void;
  /** Available days (YYYY-MM-DD, any order) — calendar bounds + only
   *  these days stay selectable. Omit for a free calendar. */
  dates?: string[];
  /** What value=null resolves to (shown in the field; picking it → null). */
  defaultDate?: string;
  /** Field label (default "Date"). */
  label?: string;
  /** Optional tooltip rendered around the field (arrow on). */
  tooltip?: string;
  /** Add HH:mm time picking (DateTimePicker clock view). */
  showTime?: boolean;
  /** Field min width in px (default 150). */
  minWidth?: number;
  disabled?: boolean;
  /** Extra styles for the input TextField. */
  sx?: SxProps<Theme>;
}

export default function DateSelector({
  value,
  onChange,
  dates,
  defaultDate,
  label = "Date",
  tooltip,
  showTime = false,
  minWidth = 150,
  disabled = false,
  sx,
}: Props) {
  const fmt = showTime ? DATE_TIME_FMT : DATE_FMT;
  // null = default — display defaultDate until a concrete pick is made.
  const dayValue = value ? dayjs(value) : defaultDate ? dayjs(defaultDate) : null;
  // YYYY-MM-DD compares correctly as strings, so min/max work in any order.
  const minDate = dates?.length
    ? dayjs(dates.reduce((a, b) => (a < b ? a : b)))
    : undefined;
  const maxDate = dates?.length
    ? dayjs(dates.reduce((a, b) => (a > b ? a : b)))
    : undefined;
  const available = new Set(dates ?? []);
  const shouldDisableDate = dates?.length
    ? (d: Dayjs) => !available.has(d.format(DATE_FMT))
    : undefined;

  const handle = (v: Dayjs | null) => {
    if (!v || !v.isValid()) {
      onChange(null);
      return;
    }
    const out = v.format(fmt);
    // Picking the default day returns to null (live/biz-today) mode.
    onChange(out === defaultDate ? null : out);
  };

  const field = showTime ? (
    <DateTimePicker
      label={label}
      views={["year", "month", "day", "hours", "minutes"]}
      value={dayValue}
      minDate={minDate}
      maxDate={maxDate}
      shouldDisableDate={shouldDisableDate}
      format={fmt}
      disabled={disabled}
      slotProps={{
        textField: { size: "small", sx: { minWidth, ...sx } },
      }}
      onChange={handle}
    />
  ) : (
    <DatePicker
      label={label}
      views={["year", "month", "day"]}
      value={dayValue}
      minDate={minDate}
      maxDate={maxDate}
      shouldDisableDate={shouldDisableDate}
      format={fmt}
      disabled={disabled}
      slotProps={{
        textField: { size: "small", sx: { minWidth, ...sx } },
      }}
      onChange={handle}
    />
  );
  return tooltip ? (
    <Tooltip title={tooltip} arrow>
      {field}
    </Tooltip>
  ) : (
    field
  );
}
