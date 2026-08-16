/**
 * Expiry OI Bands panel — OI evolution river for ONE selected expiry cohort.
 *
 * While the Market Interest Wall shows one DAY (all expiries side by side),
 * this plot follows one EXPIRY over its lifetime:
 *
 *   • x-axis = trading dates, y-axis = price (strike levels, yuan)
 *   • the underlying spot price is overlaid as a line on the same y-axis
 *   • each horizontal band = ONE strike (contract pair) of the cohort:
 *       – band THICKNESS ∝ total OI (call + put) at that strike on that date
 *       – band DARKNESS also ∝ total OI: light pastel tints while the band
 *         is thin, deepening toward full red/green as the thickness
 *         saturates — so wall-sized OI stays visually loud even when the
 *         width curve has maxed out
 *       – the red↔green mix encodes the put/call OI share: the vertical
 *         color gradient splits call-green above / put-red below at the
 *         put-share latitude with a wide blend band, so a slight call
 *         majority reads as "light green with lighter red" while a dominant
 *         side shows its color with only a sliver of the other
 *       – both the top and bottom boundaries of the band fade out via an
 *         alpha gradient — no hard edges anywhere, bands blend softly
 *         even when adjacent strikes overlap
 *
 * Expiry cohorts are grouped by expiry_date (not expiry_month, which would
 * merge the same calendar month from different years across history).
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, ToggleButton, ToggleButtonGroup } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import type { OptionsRow } from "../../../../shared/types";
import {
  DOWN_COLOR,
  PRICE_SCALE,
  SPOT_COLOR,
  UP_COLOR,
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
} from "@/theme/chart-palette";
import { fmtMil, fmtNum } from "@/lib/series";
import type { EChartsOption } from "echarts";

// ----------------------------------------------------------------------------
// Band texture generation — offscreen canvas per put-share bucket (1% steps)
// ----------------------------------------------------------------------------

const BAND_TEX_W = 8;
const BAND_TEX_H = 128;
/** Half-width of the red↔green blend band (fraction of band thickness). */
const BLEND_BAND = 0.28;
const bandTextureCache = new Map<number, HTMLCanvasElement>();

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  if (h.length !== 6) return [0, 0, 0];
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function mixHex(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const bl = Math.round(b1 + (b2 - b1) * t);
  return `rgb(${r},${g},${bl})`;
}

/**
 * Render one band texture: vertical color gradient (call-green above →
 * put-red below, split at the put-share latitude with a wide blend band),
 * then an alpha gradient that fades BOTH the top and bottom boundaries to
 * transparent so band edges vanish instead of drawing hard horizontal
 * lines. The texture is stretched horizontally per date cell — color is
 * uniform along x within one cell, and adjacent cells differ by at most one
 * 1% bucket, so the mix evolves smoothly along the time axis.
 *
 * `strength` (0..1) is the OI darkness channel: both side colors are mixed
 * toward white at the same rate, so small-OI bands render as pale pastels
 * while wall-sized OI (width-saturated) bands deepen toward full color.
 */
function getBandTexture(putPct: number, strength: number): HTMLCanvasElement {
  const putBucket = Math.max(0, Math.min(100, Math.round(putPct)));
  const strBucket = Math.max(0, Math.min(100, Math.round(strength * 100)));
  const cacheKey = putBucket * 101 + strBucket;
  const cached = bandTextureCache.get(cacheKey);
  if (cached) return cached;

  const w = BAND_TEX_W;
  const h = BAND_TEX_H;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  const putFrac = putBucket / 100;
  const str = strBucket / 100;
  const upSoft = mixHex("#ffffff", UP_COLOR, str);
  const downSoft = mixHex("#ffffff", DOWN_COLOR, str);

  // Color split — the blend band never swallows the minority side entirely:
  // its half-width is capped at 92% of the smaller share, so even a 2% put
  // share stays visible as a thin soft-red gradient sliver.
  // NOTE: canvas gradient coordinates run top→bottom, so the split latitude
  // for a put share `putFrac` (measured from the BOTTOM) is `1 - putFrac`.
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  if (putFrac <= 0) {
    ctx.fillStyle = upSoft;
  } else if (putFrac >= 1) {
    ctx.fillStyle = downSoft;
  } else {
    const split = 1 - putFrac;
    const bl = Math.min(BLEND_BAND, putFrac * 0.92, (1 - putFrac) * 0.92);
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, upSoft);
    grad.addColorStop(Math.max(0, split - bl), upSoft);
    grad.addColorStop(Math.min(1, split + bl), downSoft);
    grad.addColorStop(1, downSoft);
    ctx.fillStyle = grad;
  }
  ctx.fill();

  // Alpha fade — both vertical boundaries vanish smoothly (peak opacity at
  // the band's midline, transparent at the top and bottom edges).
  ctx.globalCompositeOperation = "destination-in";
  const fade = ctx.createLinearGradient(0, 0, 0, h);
  fade.addColorStop(0, "rgba(0,0,0,0)");
  fade.addColorStop(0.18, "rgba(0,0,0,0.85)");
  fade.addColorStop(0.5, "rgba(0,0,0,1)");
  fade.addColorStop(0.82, "rgba(0,0,0,0.85)");
  fade.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = fade;
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  ctx.fill();
  ctx.globalCompositeOperation = "source-over";

  bandTextureCache.set(cacheKey, canvas);
  return canvas;
}

// ----------------------------------------------------------------------------
// Data shaping
// ----------------------------------------------------------------------------

interface ExpiryCohort {
  /** expiry_date string (unique cohort key across years). */
  key: string;
  /** Short label "YYYY-MM". */
  label: string;
  totalOi: number;
}

interface BandCell {
  value: [number, number]; // [dateIdx, strikeYuan] — for encode + tooltip
  date: string;
  strikeY: number;
  callOi: number;
  putOi: number;
  totalOi: number;
  putPct: number;
  /** Precomputed band thickness in px (∝ sqrt-scaled total OI). */
  h: number;
  /** Precomputed color darkness 0..1 (∝ power-scaled total OI). */
  strength: number;
}

function buildCohorts(rows: OptionsRow[]): ExpiryCohort[] {
  const byExpiry = new Map<string, number>();
  for (const r of rows) {
    byExpiry.set(r.expiry_date, (byExpiry.get(r.expiry_date) ?? 0) + r.open_interest);
  }
  return Array.from(byExpiry.entries())
    .map(([key, totalOi]) => ({
      key,
      label: key.length >= 7 ? key.slice(0, 7) : key,
      totalOi,
    }))
    .sort((a, b) => a.key.localeCompare(b.key));
}

function buildCells(rows: OptionsRow[], expiryDate: string) {
  const byDate = new Map<
    string,
    { strikes: Map<number, { c: number; p: number }>; spotRaw: number }
  >();
  for (const r of rows) {
    if (r.expiry_date !== expiryDate) continue;
    let d = byDate.get(r.date);
    if (!d) {
      d = { strikes: new Map(), spotRaw: r.underlying_close };
      byDate.set(r.date, d);
    }
    const cell = d.strikes.get(r.strike_price) ?? { c: 0, p: 0 };
    if (r.option_type === "CALL") cell.c += r.open_interest;
    else cell.p += r.open_interest;
    d.strikes.set(r.strike_price, cell);
  }
  const dates = Array.from(byDate.keys()).sort((a, b) => a.localeCompare(b));
  const cells: BandCell[] = [];
  let oiMax = 1;
  dates.forEach((date, xi) => {
    const d = byDate.get(date)!;
    for (const [k, cell] of d.strikes) {
      const totalOi = cell.c + cell.p;
      if (totalOi <= 0) continue;
      if (totalOi > oiMax) oiMax = totalOi;
      cells.push({
        value: [xi, k / PRICE_SCALE],
        date,
        strikeY: k / PRICE_SCALE,
        callOi: cell.c,
        putOi: cell.p,
        totalOi,
        putPct: (cell.p / totalOi) * 100,
        h: 0, // filled in the second pass once oiMax is known
        strength: 0,
      });
    }
  });
  // Second pass — thickness ∝ sqrt(total OI / oiMax); color darkness ramps
  // with a HIGHER power so it stays light while the band is still growing
  // and deepens toward full color as the thickness saturates (wall strikes
  // pop in both channels). The floor keeps small-OI bands faintly tinted
  // instead of invisible-white, and the sub-linear power spreads the dark
  // range over a healthy share of entries rather than a handful of outliers.
  for (const cell of cells) {
    const frac = cell.totalOi / oiMax;
    cell.h = BAND_H_MIN + (BAND_H_MAX - BAND_H_MIN) * Math.sqrt(frac);
    cell.strength =
      COLOR_STRENGTH_MIN + (1 - COLOR_STRENGTH_MIN) * Math.pow(frac, COLOR_STRENGTH_POWER);
  }
  const spot = dates.map((dt) => byDate.get(dt)!.spotRaw / PRICE_SCALE);
  return { dates, cells, spot, oiMax };
}

// ----------------------------------------------------------------------------
// Chart option
// ----------------------------------------------------------------------------

/** Band thickness range in px — thickness ∝ sqrt-scaled total OI. */
const BAND_H_MIN = 3;
const BAND_H_MAX = 26;
/**
 * Color darkness channel — mixes red/green toward white by OI magnitude.
 *   strength = MIN + (1 - MIN) * (OI / oiMax) ^ POWER
 * MIN=0.30 keeps the smallest bands faintly tinted (not blank white) and
 * POWER=0.65 ramps slower than the sqrt width curve, so darkness mainly
 * kicks in as thickness approaches saturation while a healthy share of
 * entries (roughly the top quartile of OI) still reaches clearly dark
 * tones — the scale is not hogged by a few extreme outliers.
 */
const COLOR_STRENGTH_MIN = 0.3;
const COLOR_STRENGTH_POWER = 0.65;

function buildBandsOption(
  dates: string[],
  cells: BandCell[],
  spot: (number | null)[],
  themeMode: "light" | "dark",
): EChartsOption {
  const c = axisColors(themeMode);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const renderItem = (params: any, api: any) => {
    const cell = cells[params.dataIndex as number];
    if (!cell) return null;
    const coord = api.coord([cell.value[0], cell.value[1]]) as number[];
    const cx = coord[0];
    const cy = coord[1];
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
    // One category step in px — each cell spans half a step on both sides
    // so adjacent date cells tile seamlessly into one continuous band.
    const step = api.size ? (api.size([1, 0]) as number[]) : null;
    const half = step && step[0] > 0 ? step[0] / 2 : 4;
    return {
      type: "image" as const,
      style: {
        image: getBandTexture(cell.putPct, cell.strength),
        x: cx - half,
        y: cy - cell.h / 2,
        width: half * 2,
        height: cell.h,
      },
    };
  };

  const marker = (color: string) =>
    `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:4px;vertical-align:middle"></span>`;

  /** Compact OI string — "41.5M" style for large values, plain otherwise. */
  const fmtOi = (v: number) => (v >= 1e6 ? fmtMil(v) : fmtNum(v));

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 20, top: 36, bottom: 50 }),
    dataZoom: commonDataZoom(),
    legend: commonLegend(themeMode, { data: ["Spot"] }),
    tooltip: {
      // Axis trigger — hovering a date shows the WHOLE vertical slice:
      // spot + every strike band on that date (sorted by OI, dominant
      // option type named first per line).
      trigger: "axis",
      axisPointer: {
        type: "line",
        snap: true,
        lineStyle: { color: c.textColor, type: "dashed", opacity: 0.5 },
      },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          seriesName?: string;
          axisValue?: string;
          dataIndex?: number;
          value?: number | [number, number];
          data?: BandCell | number | null;
        }>;
        if (arr.length === 0) return "";
        const spotParam = arr.find((p) => p.seriesName === "Spot");
        const bandCells = arr
          .filter((p) => p.seriesName !== "Spot")
          .map((p) => p.data as BandCell)
          .filter((d): d is BandCell => !!d && typeof d !== "number");
        const dateStr = spotParam?.axisValue ?? bandCells[0]?.date ?? "";
        const lines: string[] = [`<b>${dateStr}</b>`];
        const spotV =
          typeof spotParam?.value === "number" ? spotParam.value : spotParam?.value?.[1];
        const hasSpot = spotV != null && Number.isFinite(spotV);
        // Single price-DESCENDING list (matches the y-axis, top→bottom) with
        // the spot price interleaved at its price position among the strikes.
        const entries: Array<{ price: number; line: string }> = [];
        if (hasSpot) {
          entries.push({
            price: spotV as number,
            line: `${marker(SPOT_COLOR)} <b>Spot</b> · <b>${fmtNum(spotV as number)}</b>`,
          });
        }
        // All strikes on this date; each line names the DOMINANT option type
        // and its share. Dot renders in DARK red/green when the dominant
        // share exceeds 80%, otherwise a light tint of that color.
        for (const d of bandCells) {
          const callDominant = d.callOi >= d.putOi;
          const domName = callDominant ? "Call" : "Put";
          const domPct = callDominant ? 100 - d.putPct : d.putPct;
          const baseColor = callDominant ? UP_COLOR : DOWN_COLOR;
          const domColor = domPct > 80 ? baseColor : mixHex(baseColor, "#ffffff", 0.55);
          entries.push({
            price: d.strikeY,
            line: `${marker(domColor)} K=<b>${fmtNum(d.strikeY)}</b> · ${fmtOi(d.totalOi)} · ${domName} ${fmtNum(domPct)}% <span style="opacity:0.65">(C ${fmtOi(d.callOi)} / P ${fmtOi(d.putOi)})</span>`,
          });
        }
        entries.sort((a, b) => b.price - a.price);
        for (const e of entries) lines.push(e.line);
        return lines.join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, rotate: 30 },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "Price (元)",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10, formatter: (v: number) => fmtNum(v) },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "custom",
        name: "OI Bands",
        renderItem,
        data: cells,
        encode: { x: 0, y: 1 },
        clip: true,
        progressive: 0,
        z: 3,
      },
      {
        type: "line",
        name: "Spot",
        data: spot,
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: { color: SPOT_COLOR, width: 1.6 },
        z: 10,
      },
    ],
  };
}

// ----------------------------------------------------------------------------
// Panel component
// ----------------------------------------------------------------------------

interface Props {
  rows: OptionsRow[];
}

export default function ExpiryOiBandsPanel({ rows }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const cohorts = useMemo(() => buildCohorts(rows), [rows]);
  const [selectedExpiry, setSelectedExpiry] = useState<string>("");
  // Cohort filter: "active" = expiry_date >= latest data date (still
  // tradable), "history" = already-expired cohorts.
  const [cohortMode, setCohortMode] = useState<"active" | "history">("active");

  const lastDate = useMemo(() => {
    let mx = "";
    for (const r of rows) if (r.date > mx) mx = r.date;
    return mx;
  }, [rows]);

  const filteredCohorts = useMemo(
    () =>
      cohorts.filter((co) =>
        cohortMode === "active" ? co.key >= lastDate : co.key < lastDate,
      ),
    [cohorts, cohortMode, lastDate],
  );

  // Keep selection valid across underlying switches and mode changes;
  // default to the most liquid cohort (max total OI over its lifetime).
  useEffect(() => {
    if (filteredCohorts.length === 0) {
      if (selectedExpiry !== "") setSelectedExpiry("");
      return;
    }
    if (!filteredCohorts.some((co) => co.key === selectedExpiry)) {
      const best = filteredCohorts.reduce((a, b) => (b.totalOi > a.totalOi ? b : a));
      setSelectedExpiry(best.key);
    }
  }, [filteredCohorts, selectedExpiry]);

  const built = useMemo(
    () => (selectedExpiry ? buildCells(rows, selectedExpiry) : null),
    [rows, selectedExpiry],
  );

  if (cohorts.length === 0) {
    return (
      <Alert severity="info">No expiry cohorts available for this underlying.</Alert>
    );
  }

  const selectedLabel =
    cohorts.find((co) => co.key === selectedExpiry)?.label ?? selectedExpiry;

  return (
    <ChartCard
      title="Expiry OI Bands (vs Spot)"
      subtitle={`One expiry over time · ${selectedLabel} (${selectedExpiry}) · each horizontal band = one strike · thickness + darkness ∝ total OI · green=call / red=put share blended · soft vanishing edges · spot line overlay`}
      height={460}
    >
      <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1, mb: 1 }}>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={cohortMode}
          onChange={(_, v) => {
            if (v) setCohortMode(v as "active" | "history");
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
          value={selectedExpiry}
          onChange={(_, v) => {
            if (v) setSelectedExpiry(v as string);
          }}
          sx={{ flexWrap: "wrap", maxWidth: "100%" }}
        >
          {filteredCohorts.map((co) => (
            <ToggleButton
              key={co.key}
              value={co.key}
              title={`Expires ${co.key}`}
              sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}
            >
              {co.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Box>
      {built && built.cells.length > 0 ? (
        <EChart
          option={buildBandsOption(
            built.dates,
            built.cells,
            built.spot,
            themeMode,
          )}
          height={420}
        />
      ) : filteredCohorts.length === 0 ? (
        <Alert severity="info">
          No {cohortMode} expiry cohorts (latest data date {lastDate || "—"}).
        </Alert>
      ) : (
        <Alert severity="info">No OI data for expiry {selectedLabel}.</Alert>
      )}
    </ChartCard>
  );
}
