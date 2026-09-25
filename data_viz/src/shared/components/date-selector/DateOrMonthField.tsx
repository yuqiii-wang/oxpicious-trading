/**
 * DateOrMonthField — text-first "point in time" field accepting BOTH an
 * exact date ("YYYY-MM-DD") and a year-month ("YYYY-MM"), with a calendar
 * popover for picking the exact day.
 *
 * Where DateSelector is the calendar-first single-trading-day picker, this
 * field is for pinning an ask/anchor to a point in time where month
 * precision is meaningful (e.g. the AI Ask modal's as-of date): typing is
 * the primary input, the calendar a convenience. `value` is the raw field
 * text — the owner validates with isDateOrMonth and canonicalizes with
 * normalizeDateOrMonth (./dateOrMonth).
 */
import { useState } from "react";
import {
  IconButton,
  InputAdornment,
  Popover,
  TextField,
  Tooltip,
} from "@mui/material";
import type { SxProps, Theme } from "@mui/material";
import CalendarMonthOutlinedIcon from "@mui/icons-material/CalendarMonthOutlined";
import { StaticDatePicker } from "@mui/x-date-pickers/StaticDatePicker";
import dayjs, { type Dayjs } from "dayjs";
import { normalizeDateOrMonth } from "./dateOrMonth";

interface Props {
  /** Field text — "YYYY-MM-DD", "YYYY-MM", "" (nothing pinned), or
   *  anything mid-edit (the owner owns validation). */
  value: string;
  /** Keystroke-level change — fires with the raw text, partials included. */
  onChange: (value: string) => void;
  /** Field label (default "Date"). */
  label?: string;
  placeholder?: string;
  error?: boolean;
  helperText?: React.ReactNode;
  disabled?: boolean;
  /** Optional tooltip around the field (arrow on). */
  tooltip?: string;
  /** Field min width in px (default 190). */
  minWidth?: number;
  /** Extra styles for the input TextField. */
  sx?: SxProps<Theme>;
}

export default function DateOrMonthField({
  value,
  onChange,
  label = "Date",
  placeholder = "YYYY-MM-DD or YYYY-MM",
  error,
  helperText,
  disabled = false,
  tooltip,
  minWidth = 190,
  sx,
}: Props) {
  const [pickerAnchor, setPickerAnchor] = useState<HTMLElement | null>(null);
  // What the calendar opens on: the field's date when it parses (a bare
  // year-month lands on its first day), else today.
  const norm = normalizeDateOrMonth(value);
  const picked = norm
    ? dayjs(norm.length === 7 ? `${norm}-01` : norm)
    : dayjs();

  const pick = (v: Dayjs | null) => {
    onChange(v && v.isValid() ? v.format("YYYY-MM-DD") : "");
    setPickerAnchor(null);
  };

  const field = (
    <TextField
      label={label}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      error={error}
      helperText={helperText}
      disabled={disabled}
      size="small"
      sx={{ minWidth, ...sx }}
      InputProps={{
        endAdornment: (
          <InputAdornment position="end">
            <IconButton
              size="small"
              aria-label="Pick an exact date"
              title="Pick an exact date"
              disabled={disabled}
              onClick={(e) => setPickerAnchor(e.currentTarget)}
              sx={{ p: 0.25, mr: 0.5 }}
            >
              <CalendarMonthOutlinedIcon sx={{ fontSize: "1.1rem" }} />
            </IconButton>
          </InputAdornment>
        ),
      }}
    />
  );
  return (
    <>
      {tooltip ? <Tooltip title={tooltip} arrow>{field}</Tooltip> : field}
      <Popover
        open={pickerAnchor !== null}
        anchorEl={pickerAnchor}
        onClose={() => setPickerAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <StaticDatePicker
          displayStaticWrapperAs="desktop"
          views={["year", "month", "day"]}
          openTo="day"
          value={picked}
          onChange={pick}
        />
      </Popover>
    </>
  );
}
