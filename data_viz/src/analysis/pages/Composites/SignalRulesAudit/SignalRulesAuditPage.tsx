/**
 * Signal Rules Audit page (Composites) — the per-family census and the
 * pairwise family overlap of the standalone signal history
 * (analysis_signals.signal_type_audit + signal_overlap_audit, built by
 * `python -m analysis.signal_rules_audit`).
 *
 * The header toolbar: the PERIOD selector ("All history" = the API-side
 * SUM re-aggregation of the stored year rows + one toggle per year), the
 * METRIC toggle (overlap % / lift / Jaccard — what the heatmap colors),
 * and the refresh button POSTs /signals-audit/run (incremental pipeline
 * run through the WSL py-runner) and refetches on success. Each section's
 * fetch rides `useChartData` with the period as a dep — a spinner runs
 * while in flight and stale rows stay visible (the DbAudit page pattern).
 *
 * The overlap is ASYMMETRIC by design: matrix row = base family A,
 * column = other family B — "of A's signal days, the fraction B also
 * fired on". The lift (overlap vs chance expectation) separates genuine
 * co-firing (crash-day capitulation families) from base-rate noise; the
 * pair table's same/opposite-action split is the conflict audit.
 */
import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { useChartData } from "@/shared/charts/base-chart";
import { useDocs } from "@/shared/docs";
import {
  fetchSignalAuditMeta,
  fetchSignalOverlap,
  fetchSignalTypeSummary,
  runSignalRulesAuditRefresh,
} from "@/lib/api-client";
import OverlapHeatmap from "./OverlapHeatmap";
import TypeSummaryTable from "./TypeSummaryTable";
import PairDetailTable from "./PairDetailTable";
import { familyLabel, fmtPct } from "./format";
import type { HeatmapMetric } from "./overlapHeatmapOption";
import type { SignalAuditPeriod } from "@/lib/api-client";

const METRICS: HeatmapMetric[] = ["overlap", "lift", "jaccard"];

const METRIC_LABELS: Record<HeatmapMetric, string> = {
  overlap: "Overlap %",
  lift: "Lift",
  jaccard: "Jaccard",
};

export default function SignalRulesAuditPage() {
  const docs = useDocs();
  const [period, setPeriod] = useState<SignalAuditPeriod>("all");
  const [metric, setMetric] = useState<HeatmapMetric>("overlap");
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  // The period selector's options + the trailing (partial) year mark.
  const {
    data: meta,
    loading: metaLoading,
    refresh: refreshMeta,
  } = useChartData(() => fetchSignalAuditMeta(), []);

  const latestYear = meta?.rows[0]?.period ?? null;

  const {
    data: summary,
    loading: summaryLoading,
    error: summaryError,
    refresh: refreshSummary,
  } = useChartData(() => fetchSignalTypeSummary(period), [period]);

  const {
    data: overlap,
    loading: overlapLoading,
    error: overlapError,
    refresh: refreshOverlap,
  } = useChartData(() => fetchSignalOverlap(period), [period]);

  const loading = metaLoading || summaryLoading || overlapLoading;

  // Heatmap axes: families ordered by signal-day count desc (the census
  // order), display labels via the repo's family registry.
  const { familyCodes, familyLabels } = useMemo(() => {
    const rows = summary?.rows ?? [];
    return {
      familyCodes: rows.map((r) => r.signal_type),
      familyLabels: rows.map((r) =>
        familyLabel(r.signal_type_label, r.signal_type),
      ),
    };
  }, [summary]);

  const pairRows = useMemo(
    () => [...(overlap?.rows ?? [])].sort((a, b) => b.lift - a.lift),
    [overlap],
  );

  const onRefreshRun = async () => {
    if (runBusy) return;
    setRunBusy(true);
    setRunError(null);
    const r = await runSignalRulesAuditRefresh();
    setRunBusy(false);
    if (r.success || r.already_running) {
      refreshMeta();
      refreshSummary();
      refreshOverlap();
    } else {
      setRunError(r.stderr_tail || "signal_rules_audit run failed");
    }
  };

  const periodChoices: SignalAuditPeriod[] = [
    "all",
    ...(meta?.rows.map((r) => r.period) ?? []),
  ];

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1, p: 1 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
        <Typography variant="h6">Signal Rules Audit (信号规则审计)</Typography>
        {(loading || runBusy) && (
          <CircularProgress size={16} aria-label="loading" />
        )}
        <Box sx={{ flex: 1 }} />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={period}
          onChange={(_, v) => {
            if (v != null) setPeriod(v as SignalAuditPeriod);
          }}
        >
          {periodChoices.map((p) => (
            <ToggleButton key={String(p)} value={p} sx={{ px: 1.5, py: 0.2, fontSize: 12 }}>
              {p === "all"
                ? "All history"
                : `${p}${p === latestYear ? " (partial)" : ""}`}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={metric}
          onChange={(_, v) => {
            if (v != null) setMetric(v as HeatmapMetric);
          }}
        >
          {METRICS.map((m) => (
            <ToggleButton key={m} value={m} sx={{ px: 1.5, py: 0.2, fontSize: 12 }}>
              {METRIC_LABELS[m]}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Tooltip title="Run python -m analysis.signal_rules_audit (incremental) and refetch">
          <span>
            <IconButton
              size="small"
              onClick={onRefreshRun}
              disabled={runBusy}
              aria-label="run signal rules audit"
            >
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {docs.text("ui.composites.signal-rules-audit.page")}

      {runError && <Alert severity="error">{runError}</Alert>}

      <OverlapHeatmap
        familyCodes={familyCodes}
        familyLabels={familyLabels}
        cells={overlap?.rows ?? []}
        metric={metric}
        variant="bare"
        loading={overlapLoading}
        error={
          overlapError
            ? `Failed to load the overlap matrix: ${overlapError}`
            : null
        }
        headerAction={
          <Typography variant="caption" sx={{ opacity: 0.7 }}>
            {period === "all"
              ? "all stored years — counts summed, ratios re-derived"
              : overlap?.rows[0] != null
                ? `${overlap.rows[0].period_start} → ${overlap.rows[0].period_end}`
                : ""}
          </Typography>
        }
      />

      {summaryError && (
        <Alert severity="error">
          Failed to load the family census: {summaryError}
        </Alert>
      )}
      <TypeSummaryTable
        rows={summary?.rows ?? []}
        enableFilters={summary?.enable_filters === true}
        filterScopeDeps={[period]}
      />

      <PairDetailTable
        rows={pairRows}
        enableFilters={overlap?.enable_filters === true}
        filterScopeDeps={[period]}
      />

      <Typography variant="caption" sx={{ opacity: 0.6 }}>
        Population: history_signals standalone trading tier (buy/sell,
        confidence ≥ 0, no portfolio-sourced rows), events collapsed to
        distinct (code, date) per family. Overlap asymmetry: row = base
        family, column = observed-in family. Universe share{" "}
        {fmtPct(
          summary?.rows[0] != null && summary.rows[0].signal_universe > 0
            ? summary.rows[0].n_events / summary.rows[0].signal_universe
            : null,
        )}{" "}
        shown per family above is its own share of that universe.
      </Typography>
    </Box>
  );
}
