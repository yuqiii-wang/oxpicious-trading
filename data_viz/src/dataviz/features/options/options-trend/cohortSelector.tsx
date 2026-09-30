/**
 * CohortSelector — extracted UI toggle controls for OptionsTrendPanel.
 * Manages active/history mode and the unified month selection:
 *   - "All" aggregates every expiry cohort in the current mode
 *   - an individual month selects only that month's expiries
 */
import { Box, ToggleButton, ToggleButtonGroup } from "@mui/material";

interface Props {
  cohortMode: "active" | "history";
  onCohortModeChange: (v: "active" | "history") => void;
  monthFilter: "all" | string;
  onMonthFilterChange: (v: "all" | string) => void;
  availableMonths: string[];
}

export default function CohortSelector({
  cohortMode,
  onCohortModeChange,
  monthFilter,
  onMonthFilterChange,
  availableMonths,
}: Props) {
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1, mb: 1 }}>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={cohortMode}
        onChange={(_, v) => {
          if (v) onCohortModeChange(v as "active" | "history");
        }}
      >
        <ToggleButton value="active" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          Active
        </ToggleButton>
        <ToggleButton value="history" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          History
        </ToggleButton>
      </ToggleButtonGroup>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={monthFilter}
        onChange={(_, v) => {
          if (v) onMonthFilterChange(v as "all" | string);
        }}
        sx={{ flexWrap: "wrap", maxWidth: "100%" }}
      >
        <ToggleButton value="all" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          All
        </ToggleButton>
        {availableMonths.map((m) => (
          <ToggleButton
            key={m}
            value={m}
            title={`Expiry month ${m}`}
            sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}
          >
            {m}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </Box>
  );
}
