---
name: ui-charts
description: Conventions for building and editing chart components in the data_viz React UI — every chart inherits the shared base-chart kit (BaseChart for props/chrome/states, baseChartOption for the style preamble, useChartThemeMode for theme). Use whenever writing, editing, or reviewing ANY chart component, ECharts option builder, chart tooltip, or chart styling under data_viz/src — candles, lines, pies, heatmaps, event strips — even if the request only says "add a chart", "fix chart colors", or "make this plot dark-mode aware".
---

# data_viz chart conventions

Every chart renders through one four-layer stack. Never hand-roll what a
layer below already owns.

## The stack

1. `EChart` — `data_viz/src/components/EChart.tsx`. The engine wrapper:
   init/dispose, ResizeObserver auto-resize, dataZoom-viewport preservation
   across `notMerge` rebuilds, `group` connect, `onReady/onEvents/
   onCanvasClick`. Never call `echarts.init` directly and never edit this
   file casually — its zoom-preservation logic is load-bearing.
2. `ChartCard` — `data_viz/src/components/ChartCard.tsx`. MUI card chrome
   (title/subtitle/action, all optional — header hidden when all absent).
3. **base-chart kit** — `data_viz/src/shared/charts/base-chart/`. The layer
   every chart component inherits:
   - `<BaseChart>` — card chrome (`variant="card"`, default) or bare body
     (`variant="bare"`, for charts embedded in an outer card/layout) +
     the unified async placeholders (loading spinner / error Alert /
     emptyText caption; precedence error → spinner/empty when no option →
     EChart with a small corner spinner while `loading` refreshes) + the
     EChart passthroughs (`height`, `minHeight`, `group`, `onReady`,
     `onEvents`, `onCanvasClick`). In-card controls go in `children` —
     rendered in EVERY state.
   - `baseChartOption(mode, overrides)` — the shared option preamble:
     `{ backgroundColor: "transparent", animation: false,
     legend: commonLegend(mode), tooltip: commonTooltip(mode),
     grid: commonGrid(), ...overrides }`. A fragment key set to `null` is
     omitted (`legend: null` for single-series charts, `grid: null` for
     pies); `grid` also accepts an array for multi-grid stacked panels;
     every other EChartsOption key spreads last.
   - `commonTooltip(mode, extra)` — themed axis-trigger cross-snap tooltip
     (tooltip-card colors from `theme/chart-palette`). Override
     `trigger`/`axisPointer`/`formatter` through `extra` instead of
     re-declaring color blocks; use `{ trigger: "item", axisPointer:
     undefined }` for pies/walls/scatter-item tooltips.
   - `emptyChartOption(mode, text)` — centered "[No data]"-style option,
     for charts that render an option in every state.
   - `useChartThemeMode()` — the ONLY way a chart component resolves
     light/dark. Reactive from the zustand store.
   - `useChartData(fetcher, deps)` — `{ data, loading, error, refresh }`
     fetch lifecycle with stale-response cancellation. The fetcher's
     identity is NOT a dependency (inline arrows are fine).
4. Chart components + their pure `*Option.ts` builders
   (`chartOption/` subdir or sibling `*Option.ts`), colocated with their
   feature/page.

## Rules

### Theme — single source of truth

- Never accept a `themeMode` prop, never pass one at call sites, and never
  read `useStore.getState().themeMode` in a builder — that read is
  non-reactive, so the chart keeps stale colors after a theme toggle until
  some other dependency changes. This was a real bug class (smile option,
  shared-skew, convergence, Market Interest Wall) — the fix was threading
  the hook value through.
- Component: `const themeMode = useChartThemeMode();`
- Pure builder: take `mode: ThemeMode`
  (`import type { ThemeMode } from "@/store/filters"`). Never a literal
  `"light" | "dark"` union.

### New chart component

- `interface Props extends BaseChartProps { ...data props }` and render
  `<BaseChart>`. Pass `option={null}` while there is nothing to plot — the
  base renders the placeholders. Compose error strings in the component
  (e.g. `` `Failed to load X: ${error}` ``) and pass via `error`.
- Simple fetch → `useChartData`. KEEP a custom effect when it has an
  open-guard (`if (!open || !code) return`), side-effect callbacks
  (`onLoadingChange`), parallel multi-fetch (`Promise.all`), post-fetch
  state resets, or poll/silent-refresh semantics.
- Rich tooltips render React elements into ECharts formatters via
  `renderReactElement` + `tooltipComponents` from
  `data_viz/src/lib/react-tooltip-renderer.ts`, styled with
  `TOOLTIP_CARD_STYLE` tokens — not ad-hoc HTML strings.

### Option builders

- Pure `.ts` files returning COMPLETE options. `EChart` applies
  `notMerge: true` — a partial option leaves stale series on the canvas
  (toggles that remove series would never remove them).
- Start from `baseChartOption(mode, {...})`; keep chart-specific
  grid/legend/tooltip overrides explicit; no legend → `legend: null`.
- Keep option-memo inputs identity-stable: module-level
  `const NO_EPISODES: X[] = []` defaults for optional array props. A fresh
  `[]` per render dirties the memo and forces a full notMerge rebuild —
  measured ~1 s on the 1700-candle OHLC chart.
- Colors come from `@/theme/chart-palette` (CSS-var tokens, MUTED_PALETTE,
  GROUP_MAJOR_COLORS, expiry gradients) — never hard-code hex.

### Don't restructure these

- Cross-chart sync: `echarts.connect` groups (pass `group` through
  BaseChart), `useDataZoomSync`, manual showTip/hideTip — keep whichever
  mechanism the page already uses.
- `CodeTrendChart` (`src/components/CodeTrendChart.tsx`) is a class with a
  documented protected-hook inheritance API (`fetchSource`,
  `renderChart`, `renderHeaderActions`, ...). Extend it through those
  hooks; do not convert it to a function component or force BaseChart into
  it — it inherits base styles through StockOhlcChart.
- Financial semantics (indicator math, streak/signal/margin scoring) is
  sacred — chart plumbing changes only.

## Example

```tsx
// src/analysis/pages/MyFeature/MyTrendChart.tsx
import { useMemo } from "react";
import {
  BaseChart,
  useChartThemeMode,
  useChartData,
} from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { buildMyTrendOption } from "./myTrendOption";

interface Props extends BaseChartProps {
  code: string;
}

export default function MyTrendChart({ code, ...base }: Props) {
  const themeMode = useChartThemeMode();
  const { data, loading, error } = useChartData(
    () => fetchMyRows(code),
    [code],
  );
  const option = useMemo(
    () => (data && data.length > 0 ? buildMyTrendOption(data, themeMode) : null),
    [data, themeMode],
  );
  return (
    <BaseChart
      title="My Trend"
      subtitle={`${data?.length ?? 0} bars`}
      loading={loading}
      error={error ? `Failed to load my rows: ${error}` : null}
      option={option}
      emptyText="No rows for this code"
      height={320}
      {...base}
    />
  );
}
```

```ts
// src/analysis/pages/MyFeature/myTrendOption.ts
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { axisColors, commonDataZoom, commonGrid } from "@/theme/chart-palette";

export function buildMyTrendOption(
  rows: MyRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 50 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, { /* per-chart formatter */ }),
    xAxis: {
      type: "category",
      data: rows.map((r) => r.date),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: { type: "value", axisLabel: { color: c.textColor, fontSize: 9 } },
    series: [{ type: "line", name: "Close", data: rows.map((r) => r.close) }],
  });
}
```

## Verify

- `cd data_viz && npm run check` and `npx eslint <changed files>` — zero
  NEW errors versus the pre-existing baseline set.
- Visual check at the Vite dev server (http://localhost:5173): render the
  page AND toggle light/dark. A chart that keeps old colors after the
  toggle means a non-reactive theme read crept in — fix it before done.
