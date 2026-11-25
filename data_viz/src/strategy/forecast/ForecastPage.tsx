/**
 * ForecastPage — the Strategy → Forecast tab (/strategy/forecast).
 *
 * The next-day ±kσ scenario register: per study index, the trailing-20d
 * σ of daily returns projects six next-day closes (±1σ/±2σ/±3σ) and
 * each scenario shows WHICH singleton-strategy signal would trigger
 * under that close — the active analysis rule cells of the
 * close-derivable families evaluated against the synthetic close by
 * `python -m strategy.forecast_next_day` (strategy.forecast_next_day;
 * nightly strategy_daily step 2 — this page's Run Forecast button is
 * the same module, submitted to the cluster strategist).
 *
 * Read path: GET /api/strategy/forecast (the latest anchor per code).
 * Run path: POST /api/strategy/forecast/run → the async submit + the
 * process-status poll (useForecastRunPoll) — the Singleton page's
 * Run/Train pattern, one tag. The register renders as the
 * TRADING-SIGNALS-LIST (the live SignalTable idiom): one parent row per
 * (scenario, code) — grouped by scenario (the σ rung), then code —
 * filtered by the σ-rung toggle (1σ/2σ/3σ).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import ArrowBack from "@mui/icons-material/ArrowBack";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import {
  fetchForecast,
  fetchStrategyProcessResult,
  forecastRunTag,
  runForecast,
  type ForecastNextDayRow,
} from "@/lib/api-client";
import { ForecastSignalsTable } from "./ForecastSignalsTable";
import { useForecastRunPoll } from "./useForecastRunPoll";
import { fmtDateTime } from "@/shared/datetime";

/** The module's scope — index, the 10 study indices (v1). */
const SEC_TYPE = "index" as const;

/** The σ rungs the register carries. */
const SIGMA_RUNGS = [1, 2, 3] as const;

export default function ForecastPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<ForecastNextDayRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string>("");
  // defaults to the 1σ rung only — the other rungs opt in via the toggle
  const [sigmaFilter, setSigmaFilter] = useState<number[]>([SIGMA_RUNGS[0]]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [failureTail, setFailureTail] = useState<string | null>(null);

  const tag = forecastRunTag(SEC_TYPE);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchForecast(
        SEC_TYPE,
        selectedCode || null,
      );
      setRows(res.rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [selectedCode]);

  useEffect(() => {
    void load();
  }, [load]);

  // The code selector's options — the codes the register carries.
  const codes = useMemo(() => {
    const set = new Set(rows.map((r) => r.code));
    return [...set].sort();
  }, [rows]);

  // The σ-rung filter — only the selected rungs' scenarios render.
  const filtered = useMemo(
    () => rows.filter((r) => sigmaFilter.includes(Number(r.sigma_mult))),
    [rows, sigmaFilter],
  );

  // The register's run time — the latest write stamp across the loaded
  // rows (the nightly run / the last Run Forecast touch).
  const runAt = useMemo(() => {
    if (rows.length === 0) return null;
    return rows.reduce<string | null>(
      (max, r) =>
        max == null || r.updated_at > max ? r.updated_at : max,
      null,
    );
  }, [rows]);

  const onSettled = useCallback(
    async (exitCode: number | null) => {
      if (exitCode === 0 || exitCode === null) {
        await load();
        return;
      }
      try {
        const result = await fetchStrategyProcessResult(tag);
        const lines = (result.stderr || result.stdout || "").trim();
        setFailureTail(
          lines ? lines.split("\n").slice(-5).join("\n") : `exit ${exitCode}`,
        );
      } catch {
        setFailureTail(`exit ${exitCode}`);
      }
    },
    [load, tag],
  );

  const poll = useForecastRunPoll({ tag, onSettled });

  // Run Forecast — the async submit; the poll's exit edge reloads or
  // surfaces the process's own stderr tail.
  const handleRun = useCallback(async () => {
    setSubmitError(null);
    setFailureTail(null);
    try {
      const res = await runForecast(SEC_TYPE, tag);
      if (!res.accepted && !res.already_running) {
        setSubmitError(res.error ?? "the strategist refused the run");
      }
      poll.refresh();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    }
  }, [poll, tag]);

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <IconButton
            onClick={() => navigate("/strategy/singleton")}
            size="small"
            aria-label="back to strategy singleton"
          >
            <ArrowBack />
          </IconButton>
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Forecast — next-day σ scenarios
            </Typography>
            <Typography variant="body2" color="text.secondary">
              ±1σ/±2σ/±3σ next-day closes from the 20d return σ · which
              strategy signal would trigger under each close (index, the
              study indices)
            </Typography>
            <Tooltip
              title="When the forecast pipeline last wrote these rows (strategy.forecast_next_day.updated_at — the nightly run or the last Run Forecast)"
              arrow
            >
              <Typography variant="caption" color="text.secondary">
                run at {fmtDateTime(runAt)}
              </Typography>
            </Tooltip>
          </Box>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          {loading || poll.refreshing ? (
            <Tooltip title="loading">
              <CircularProgress size={20} />
            </Tooltip>
          ) : null}
          <Tooltip title="Which σ rungs of the ladder to show (1σ / 2σ / 3σ next-day scenarios)">
            <ToggleButtonGroup
              size="small"
              value={sigmaFilter}
              onChange={(_, v: number[]) => {
                // at least one rung stays selected
                if (v.length) setSigmaFilter(v);
              }}
              aria-label="sigma filter"
            >
              {SIGMA_RUNGS.map((k) => (
                <ToggleButton key={k} value={k} sx={{ px: 1.5, py: 0.25 }}>
                  {k}σ
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Tooltip>
          <Select
            size="small"
            value={selectedCode}
            onChange={(e) => setSelectedCode(e.target.value)}
            displayEmpty
            sx={{ minWidth: 130 }}
            aria-label="code filter"
          >
            <MenuItem value="">
              <em>All codes</em>
            </MenuItem>
            {codes.map((code) => (
              <MenuItem key={code} value={code}>
                {code}
              </MenuItem>
            ))}
          </Select>
          <Button
            variant="contained"
            color="primary"
            onClick={handleRun}
            disabled={poll.active}
            startIcon={
              poll.active ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <PlayArrowIcon />
              )
            }
          >
            {poll.active ? "Running…" : "Run Forecast"}
          </Button>
        </Box>
      </Box>

      {poll.active ? (
        <Alert severity="info" sx={{ mt: 1.5 }}>
          The forecast pipeline is running on the strategist — the table
          reloads when it finishes.
        </Alert>
      ) : null}
      {error ? (
        <Alert severity="error" sx={{ mt: 1.5 }}>{error}</Alert>
      ) : null}
      {submitError ? (
        <Alert severity="error" sx={{ mt: 1.5 }}>{submitError}</Alert>
      ) : null}
      {failureTail ? (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          Forecast run failed — stderr tail:
          <Box component="pre" sx={{ m: 0.5, whiteSpace: "pre-wrap" }}>
            {failureTail}
          </Box>
        </Alert>
      ) : null}

      <Box sx={{ mt: 2 }}>
        {rows.length > 0 && filtered.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 6, textAlign: "center" }}>
            No scenarios for the selected σ rungs — enable at least one of 1σ/2σ/3σ.
          </Typography>
        ) : (
          <ForecastSignalsTable
            rows={filtered}
            loading={loading}
            secType={SEC_TYPE}
          />
        )}
      </Box>
    </Box>
  );
}
