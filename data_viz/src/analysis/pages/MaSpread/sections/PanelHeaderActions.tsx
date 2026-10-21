/**
 * PanelHeaderActions — the MaSpreadPanel card header's top-right controls:
 * the Bollinger multiplier dropdown (Price/{W} and Amt/MA pairs only), the
 * Amt | Slopes | Curvatures right-axis overlay switch, and the OHLC mode
 * toggle. Rendered only while the panel holds a loaded pair (the panel
 * guards with loading/error).
 */
import type { CSSProperties } from "react";
import {
  MenuItem,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import OhlcModeToggle from "@/shared/components/OhlcModeToggle";
import type { OhlcMode } from "@/lib/ohlc";
import { BOLL_K_OPTIONS } from "../constants";
import type { ChartOverlay } from "../types";

interface Props {
  /** Whether the Bollinger dropdown shows (Price or Amt pairs). */
  showBollinger: boolean;
  bollingerK: number;
  onBollingerKChange: (k: number) => void;
  chartOverlay: ChartOverlay;
  onChartOverlayChange: (o: ChartOverlay) => void;
  /** True while an Amt/MA pair's envelope view is shown (that view's
   *  whole plot IS amount — the overlay switch is disabled). */
  overlayDisabled: boolean;
  ohlcMode: OhlcMode;
  onOhlcModeChange: (m: OhlcMode) => void;
}

const SELECT_SX: CSSProperties = {
  height: 26,
  fontSize: "0.75rem",
};

export function PanelHeaderActions({
  showBollinger,
  bollingerK,
  onBollingerKChange,
  chartOverlay,
  onChartOverlayChange,
  overlayDisabled,
  ohlcMode,
  onOhlcModeChange,
}: Props) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mr: 0.5 }}>
      {showBollinger && (
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: "0.7rem", whiteSpace: "nowrap" }}
          >
            Bollinger
          </Typography>
          <Select
            size="small"
            value={bollingerK}
            onChange={(e) => onBollingerKChange(e.target.value as number)}
            sx={{
              ...SELECT_SX,
              "& .MuiSelect-select": { py: 0.25, px: 1, fontSize: "0.75rem" },
            }}
            renderValue={(v) =>
              v === 0 ? "Off" : `${Number(v).toFixed(1)}σ`
            }
          >
            {BOLL_K_OPTIONS.map((k) => (
              <MenuItem key={k} value={k} sx={{ fontSize: "0.75rem", py: 0.25 }}>
                {k === 0 ? "Off (0.0)" : `${k.toFixed(1)}σ`}
              </MenuItem>
            ))}
          </Select>
        </Stack>
      )}
      {/* Amt | Slopes | Curvatures — the header switch for the chart's
          right-axis overlay (mirrors the OhlcModeToggle group beside it).
          Slopes/Curvatures draw the DB-stored direct derivative lines of
          the price and the 5/20-day MA (6/20-day EMA while an EMA pair
          chip is selected) — value[t] − value[t−1] and its diff, no
          averaging. The RSI Trends overlay moved out (its own button row
          beneath the chart) and rides the "amt" slot via its days row.
          Disabled while an Amt/MA pair's envelope view is shown (that
          view's whole plot IS amount). */}
      <ToggleButtonGroup
        size="small"
        exclusive
        value={chartOverlay}
        onChange={(_, v: ChartOverlay | null) => {
          if (v) onChartOverlayChange(v);
        }}
        disabled={overlayDisabled}
      >
        <ToggleButton value="amt" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          Amt
        </ToggleButton>
        <ToggleButton value="slope" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          Slopes
        </ToggleButton>
        <ToggleButton value="curv" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          Curvatures
        </ToggleButton>
      </ToggleButtonGroup>
      <OhlcModeToggle value={ohlcMode} onChange={onOhlcModeChange} />
    </Stack>
  );
}
