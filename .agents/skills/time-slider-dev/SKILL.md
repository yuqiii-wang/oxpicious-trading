---
name: time-slider-dev
description: Time-slider and cross-chart date-window dev conventions for the data_viz React UI — the shared TimeSlider component (index-based dual-thumb), the ECharts dataZoom slider as the single shared window control in stacked panels, the manual dataZoom broadcast + syncTipTo hover crosshair (why echarts.connect breaks on null rows), and the rAF-throttle/lazyUpdate blank-paint pitfall. Use when building or changing any stacked multi-chart panel with a shared time window, adding a time slider to a page, wiring cross-chart hover/tooltip sync, or debugging charts that mount but paint nothing.
---

# Time slider + cross-chart date-window conventions

Distilled from the News Text States stacked panel build (2026-11-22)
and the MaSpread / News Audit precedents. Three mechanisms exist; pick
by interaction, never stack all of them.

## The three mechanisms

| Mechanism | Component/Helper | Controls | Use when |
| --- | --- | --- | --- |
| Index slider (MUI) | `src/shared/components/time-slider/TimeSlider.tsx` | the ROW SET the charts receive | explicit date-range picking over a distinct date list (News Audit window grid); the caller slices rows |
| ECharts dataZoom slider | `commonDataZoom()` in `theme/chart-palette.ts` | the viewport inside ONE chart's option | viewport zooming over the loaded history (the panel's THE time slider) |
| Manual broadcast | page-local `handleDataZoom` + `syncTipTo` (`shared/charts/tooltipSync.ts`) | other charts' zoom windows + hover crosshair | stacked sibling charts sharing ONE window without echarts.connect |

## Stacked-panel pattern (News Text States precedent)

One semantic chart per business family, all in ONE outlined parent box,
ONE shared time window:

1. **The slider lives on exactly ONE chart** (the top one) — its
   `commonDataZoom()` keeps BOTH entries (inside + slider). Sibling
   charts strip the slider: `stripZoomSlider(builder(...))` filters the
   option's `dataZoom` array to the inside entry. Never ship two
   visible sliders for one shared window.
2. **Zoom broadcast**: every chart's `onEvents.dataZoom` handler reads
   `params.batch?.[0] ?? params` for `{start, end}` and
   `dispatchAction({ type: "dataZoom", start, end })` on every sibling
   instance, guarded by a `useRef(false)` syncing flag (the dispatch
   re-fires the siblings' own dataZoom events — without the flag the
   broadcast ping-pongs forever).
3. **Hover crosshair**: `updateAxisPointer` on each chart →
   `syncTipTo(sibling, params, tipSyncingRef, from)` per sibling. Each
   chart keeps its OWN axis tooltip (it renders that chart's own
   series). Same syncing-flag guard lives inside syncTipTo.
4. **Instance registry**: collect instances via BaseChart's
   `onReady={chartOnReady(i)}` into a `useRef<Array<ECharts | null>>`;
   guard every dispatch with `!inst.isDisposed()`.

## Why NOT echarts.connect for tooltips

`echarts.connect(group)` syncs dataZoom AND tooltip AND legend as one
bundle. The tooltip sync resolves the hovered point from each receiving
chart's series at the forwarded `seriesIndex+dataIndex` — when that
series has a **null** at the hovered date the resolved point is NaN and
the tooltip silently goes empty/stale (the MaSpread finding, documented
in `tooltipSync.ts`). connect remains fine for pure dataZoom sharing on
null-free panels (MaSpread uses both mechanisms side by side: connect
keeps the zoom window, syncTipTo keeps the crosshair).

## The rAF-throttle pitfall — charts that mount but never paint

ECharts `setOption(..., { lazyUpdate: true })` schedules the paint on
requestAnimationFrame. Browsers **stop firing rAF entirely** in
occluded/backgrounded panes (the in-app browser pane throttles to zero
when the host app is in another view). Symptom: instances alive
(`[_echarts_instance_]` present, two empty zrender DIVs), zero canvas
layers, no console errors, every chart on the page blank — and it
self-heals the moment a real user interacts.

**`EChart.tsx` therefore sets `lazyUpdate: false`** (synchronous
paint; options land only on fetch/theme changes, so the cost is nil).
NEVER reintroduce lazyUpdate in a chart wrapper — if you see "chart
mounts but paints nothing" in review or QA, check that flag first, and
verify with a FRESH browser tab (a long-lived automation tab can carry
the throttled state across navigations).

## Perf contract for stacked panels (the tooltip-slowness study)

- `sampling: "lttb"` + `triggerLineEvent: false` on every dense line
  (1.6k-point lines; triggerLineEvent off removes per-segment
  hit-testing — the dominant mousemove cost in line series).
- `silent: true` on context series (a price close line never
  participates in hover/emphasis).
- One small canvas per semantic chart: a tooltip repaint invalidates
  its own ~200px band, not a 1183×700 mega-canvas; the mega-option's
  cross-grid `axisPointer.link` re-rendered EVERY band per mousemove —
  per-chart tooltips need no link.
- Synthetic `dispatchEvent(MouseEvent)` timing is NOT a reliable perf
  proxy: zrender may not process synthetic events synchronously (0.0ms
  readings observed). Judge by structure (canvas count/size, ink
  ratios) plus real interaction.

## Verification checklist

1. `npm run check` + ESLint on touched files.
2. Fresh browser tab (never a long-lived one): all instances render
   canvas layers; canvas pixel-sampled for ink.
3. Drag the top chart's slider → every sibling's window moves with it.
4. Hover one chart → the date crosshair shows on the siblings, each
   with its own series values.
5. Light/dark toggle → all charts re-render (the theme hook rule in
   `ui-charts`).
