/** The Summary History section — the date-event bar over the planned
 *  runs, the thread-id search box, and the multi-run day's dropdown. */
import { Box, CircularProgress, IconButton, InputAdornment, MenuItem, Paper, TextField, Tooltip, Typography } from "@mui/material";
import { Close, Search } from "@mui/icons-material";
import DateEventStrip, {
  type DateEvent,
} from "@/shared/components/date-events/DateEventStrip";
import { MUTED_PALETTE } from "@/theme/chart-palette";
import { runLabel } from "../../ThreadGraph/runLabel";
import type { AiSummaryRun } from "@/lib/api-client/aiSummary";
import type { ScopeSubjectEntry } from "../../AnalysisItemsTree";

export default function SummaryHistorySection({
  subjects,
  scopeLabel,
  threadSearch,
  onThreadSearchChange,
  threadSearching,
  onThreadSearch,
  runEvents,
  datesLoading,
  selectedRunDate,
  onRunDotClick,
  runsOnSelectedDate,
  selectedThreadId,
  onThreadPick,
  liveThreadId,
  onDeleteRun,
}: {
  subjects: ScopeSubjectEntry[];
  scopeLabel: string;
  threadSearch: string;
  onThreadSearchChange: (value: string) => void;
  threadSearching: boolean;
  onThreadSearch: () => void;
  runEvents: DateEvent[];
  datesLoading: boolean;
  selectedRunDate: string | null;
  onRunDotClick: (e: DateEvent) => void;
  runsOnSelectedDate: AiSummaryRun[];
  selectedThreadId: string | null;
  onThreadPick: (threadId: string) => void;
  liveThreadId: string | null;
  onDeleteRun: (threadId: string) => void;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 0 }}>
          Summary History
          {subjects.length > 0
            ? ` — scoped to ${scopeLabel} · market-wide runs included`
            : " — all runs"}
        </Typography>
        <Tooltip title="Search a run by its thread id — paste the full id or a leading prefix; the lookup ignores the scope pick">
          <TextField
            size="small"
            placeholder="Search by thread id"
            value={threadSearch}
            onChange={(e) => onThreadSearchChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void onThreadSearch();
            }}
            sx={{ ml: "auto", width: 300 }}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    edge="end"
                    aria-label="Search run by thread id"
                    disabled={threadSearch.trim() === "" || threadSearching}
                    onClick={() => void onThreadSearch()}
                  >
                    {threadSearching ? (
                      <CircularProgress size={16} color="inherit" />
                    ) : (
                      <Search fontSize="small" />
                    )}
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
        </Tooltip>
      </Box>
      <DateEventStrip
        events={runEvents}
        typeMeta={{
          runs: { label: "Summary runs", color: MUTED_PALETTE[0] },
        }}
        selectedId={selectedRunDate}
        onEventClick={onRunDotClick}
        height={72}
        showLegend={false}
        emptyText={
          datesLoading
            ? "Loading run history…"
            : "No planned runs yet — generate a summary with a plan prompt."
        }
        enableZoom
        defaultWindowDays={92}
      />
      {selectedRunDate !== null && runsOnSelectedDate.length > 1 && (
        <TextField
          select
          size="small"
          label={`Runs on ${selectedRunDate}`}
          value={selectedThreadId ?? ""}
          onChange={(e) => onThreadPick(e.target.value)}
          SelectProps={{
            // The closed field shows just the picked run's label — the
            // delete cross belongs to the dropdown's options only
            // (MUI would otherwise clone the selected option's children,
            // cross button included, into the closed display).
            renderValue: (picked) => {
              const pickedRun = runsOnSelectedDate.find(
                (r) => r.thread_id === String(picked),
              );
              return pickedRun ? runLabel(pickedRun) : String(picked);
            },
          }}
          sx={{ mt: 1, minWidth: 340, maxWidth: 520 }}
          helperText="Several runs that day — pick one to open its thread graph"
        >
          {runsOnSelectedDate.map((r) => (
            <MenuItem
              key={r.thread_id}
              value={r.thread_id}
              sx={{
                // The delete cross rides at the option's tail and only
                // shows while the pointer is on the option.
                "& .run-delete": { visibility: "hidden" },
                "&:hover .run-delete": { visibility: "visible" },
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  width: "100%",
                  minWidth: 0,
                }}
              >
                <Typography variant="inherit" noWrap sx={{ flex: 1, minWidth: 0 }}>
                  {runLabel(r)}
                </Typography>
                <IconButton
                  className="run-delete"
                  size="small"
                  edge="end"
                  aria-label="Delete run"
                  disabled={r.thread_id === liveThreadId}
                  onClick={(e) => {
                    // Not a pick — purge the run and keep the menu
                    // open instead of selecting it.
                    e.stopPropagation();
                    void onDeleteRun(r.thread_id);
                  }}
                >
                  <Close fontSize="small" />
                </IconButton>
              </Box>
            </MenuItem>
          ))}
        </TextField>
      )}
    </Paper>
  );
}
