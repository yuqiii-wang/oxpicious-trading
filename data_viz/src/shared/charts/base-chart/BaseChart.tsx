/**
 * BaseChart — the shared base every chart component renders.
 *
 * Owns everything charts used to hand-roll one copy each of:
 *   - card chrome (title / subtitle / header action) via ChartCard, or a
 *     bare body for charts embedded in an outer layout;
 *   - the async placeholders: error Alert, initial-load spinner, empty text —
 *     one visual treatment across all pages;
 *   - the EChart passthroughs (height, group connect, ready / events /
 *     canvas-click) so interaction wiring survives unchanged.
 *
 * State precedence in the chart area: `error` → Alert; no option yet →
 * spinner while `loading`, else the `emptyText` caption; option present →
 * EChart (a `loading` refresh then only overlays a small spinner, keeping
 * the mounted chart's zoom / tooltip state — or, with `freezeOnLoading`, a
 * full-cover translucent backdrop with a centered spinner that freezes the
 * plot by swallowing pointer events). `children` (controls) render in
 * every state.
 *
 * Also owns the AI Ask "?" beside the title (card variant): captures the
 * echarts instance through a composed onReady, derives plot info from the
 * built option merged with the optional `aiAsk` spec, and renders the
 * shared AiAskButton via ChartCard's titleAddon slot.
 */
import { useCallback, useMemo, useRef } from "react";
import type { ECharts } from "echarts";
import { Alert, Box, CircularProgress, Typography } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { AiAskButton, derivePlotInfo } from "@/shared/ai-ask";
import type { BaseChartProps } from "./types";

export default function BaseChart({
  option,
  height = 320,
  minHeight = 200,
  variant = "card",
  title,
  subtitle,
  aiAsk,
  headerAction,
  children,
  loading = false,
  freezeOnLoading = false,
  error = null,
  emptyText = "No data",
  group,
  onReady,
  onEvents,
  onCanvasClick,
}: BaseChartProps) {
  const hasOption = option != null;

  // Live instance for the AI Ask screenshot — composed with the caller's
  // onReady so cross-chart sync consumers are unaffected.
  const instanceRef = useRef<ECharts | null>(null);
  const handleReady = useCallback(
    (instance: ECharts) => {
      instanceRef.current = instance;
      onReady?.(instance);
    },
    [onReady],
  );
  const getInstance = useCallback(() => instanceRef.current, []);

  const showAiAsk = variant === "card" && aiAsk !== null;
  const aiAskPlotInfo = useMemo(
    () =>
      showAiAsk
        ? derivePlotInfo({ title, subtitle, option, spec: aiAsk ?? undefined })
        : null,
    [showAiAsk, title, subtitle, option, aiAsk],
  );
  const placeholderMinHeight =
    typeof height === "number" ? Math.min(height, 240) : minHeight;

  const body = error ? (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: placeholderMinHeight,
      }}
    >
      <Alert severity="error" sx={{ py: 0.5, width: "100%" }}>
        {error}
      </Alert>
    </Box>
  ) : !hasOption ? (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: placeholderMinHeight,
      }}
    >
      {loading ? (
        <CircularProgress size={28} />
      ) : (
        <Typography
          variant="caption"
          sx={{ color: "var(--chart-subtitle)", fontSize: "0.75rem" }}
        >
          {emptyText}
        </Typography>
      )}
    </Box>
  ) : (
    <Box sx={{ position: "relative" }}>
      <EChart
        option={option}
        height={height}
        minHeight={minHeight}
        group={group}
        onReady={handleReady}
        onEvents={onEvents}
        onCanvasClick={onCanvasClick}
      />
      {loading &&
        (freezeOnLoading ? (
          // Frozen refresh — the backdrop's hit area swallows canvas /
          // dataZoom pointer events so the mounted plot can't be interacted
          // with while a fetch is in flight (same treatment as the
          // ForecastTable freeze backdrop).
          <Box
            sx={{
              position: "absolute",
              inset: 0,
              zIndex: 2,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              bgcolor: "rgba(122, 122, 122, 0.12)",
              backdropFilter: "blur(1px)",
              cursor: "wait",
            }}
          >
            <CircularProgress size={28} />
          </Box>
        ) : (
          <CircularProgress
            size={18}
            sx={{ position: "absolute", top: 8, right: 8, opacity: 0.7 }}
          />
        ))}
    </Box>
  );

  if (variant === "bare") {
    return (
      <Box sx={{ width: "100%" }}>
        {children}
        {body}
      </Box>
    );
  }
  return (
    <ChartCard
      title={title}
      titleAddon={
        showAiAsk ? (
          <AiAskButton plotInfo={aiAskPlotInfo} getInstance={getInstance} />
        ) : undefined
      }
      subtitle={subtitle}
      action={headerAction}
      height={height}
    >
      {children}
      {body}
    </ChartCard>
  );
}
