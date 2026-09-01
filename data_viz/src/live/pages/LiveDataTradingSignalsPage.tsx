/**
 * Live Data — Trading Signals page.
 *
 * UI scaffold only (no data wired yet): a control bar with an
 * Analysis / Strategy mode toggle and a date selector that mirrors the
 * Market Movements pattern (Autocomplete roster, newest first; picking the
 * newest entry means "latest"). Content sections will be added per mode
 * once the signal backends exist.
 */
import { useState } from "react";
import {
  Autocomplete,
  Box,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";

type SignalsMode = "analysis" | "strategy";

export default function LiveDataTradingSignalsPage() {
  const [mode, setMode] = useState<SignalsMode>("analysis");
  // Date selector mirrors Market Movements: null = latest available date
  // (server resolves it once fetching is wired); a concrete date freezes
  // the page on that historical day. Roster is empty until the dates
  // endpoint exists — the field renders the selected/placeholder value.
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const availableDates: string[] = [];

  return (
    <Stack spacing={2}>
      {/* Control bar: mode toggle + date selector */}
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          onChange={(_, v: SignalsMode | null) => {
            if (v) setMode(v);
          }}
          sx={{ height: 32 }}
        >
          <ToggleButton value="analysis" sx={{ px: 1.5, fontSize: "0.75rem" }}>
            Analysis
          </ToggleButton>
          <ToggleButton value="strategy" sx={{ px: 1.5, fontSize: "0.75rem" }}>
            Strategy
          </ToggleButton>
        </ToggleButtonGroup>
        <Autocomplete
          size="small"
          sx={{ minWidth: 150 }}
          disableClearable
          options={availableDates.length > 0 ? availableDates : selectedDate ? [selectedDate] : []}
          value={selectedDate ?? ""}
          onChange={(_e, v) => {
            if (v) setSelectedDate(v);
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Date"
              variant="outlined"
              size="small"
            />
          )}
        />
      </Stack>

      {/* Placeholder content per mode until signal backends are wired */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          py: 10,
        }}
      >
        <Typography variant="body2" color="text.secondary">
          Trading Signals — {mode === "analysis" ? "Analysis" : "Strategy"} view coming soon.
        </Typography>
      </Box>
    </Stack>
  );
}
