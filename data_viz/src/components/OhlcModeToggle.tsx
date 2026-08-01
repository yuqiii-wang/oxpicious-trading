/**
 * OhlcModeToggle — shared Absolute / % Change toggle for every OHLC panel.
 *
 * Mirrors the ToggleButtonGroup used by the analysis correlation plot
 * (PerfAttrPage). Default mode is "percentage" — callers should initialize
 * their state with `useState<OhlcMode>("percentage")`.
 */
import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import type { OhlcMode } from "@/lib/ohlc";

interface Props {
  value: OhlcMode;
  onChange: (mode: OhlcMode) => void;
  size?: "small" | "medium";
}

export default function OhlcModeToggle({
  value,
  onChange,
  size = "small",
}: Props) {
  return (
    <ToggleButtonGroup
      size={size}
      exclusive
      value={value}
      onChange={(_, v) => {
        if (v) onChange(v as OhlcMode);
      }}
    >
      <ToggleButton value="absolute" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
        Absolute
      </ToggleButton>
      <ToggleButton value="percentage" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
        % Change
      </ToggleButton>
    </ToggleButtonGroup>
  );
}
