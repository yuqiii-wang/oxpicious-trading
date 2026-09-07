/**
 * SecTypeToggle — shared ETF / Index / Stock sec_type toggle used by the
 * analysis pages' header controls. `options` restricts the visible buttons
 * (e.g. ["index", "etf"] for a two-type page).
 */
import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import type { SecNavSecType } from "./types";

interface SecTypeToggleProps {
  value: SecNavSecType;
  onChange: (t: SecNavSecType) => void;
  /** Visible buttons in display order. Default: etf, index, stock. */
  options?: SecNavSecType[];
  /** Disable all buttons (pages locked to one sec_type). */
  disabled?: boolean;
}

const LABELS: Record<SecNavSecType, string> = {
  etf: "ETF",
  index: "Index",
  stock: "Stock",
};

export default function SecTypeToggle({ value, onChange, options = ["etf", "index", "stock"], disabled = false }: SecTypeToggleProps) {
  return (
    <ToggleButtonGroup
      value={value}
      exclusive
      size="small"
      disabled={disabled}
      onChange={(_, v) => {
        if (v) onChange(v as SecNavSecType);
      }}
    >
      {options.map((t) => (
        <ToggleButton key={t} value={t}>
          {LABELS[t]}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}
