/**
 * RoofFloorSection — the "Roof/Floor Trendlines" row: the window buttons
 * (60 … 1275d — the 20d is not a roof/floor window) on the period grid,
 * the row's "Est levels" toggle, the est-method picker, and the section's
 * captions (the click-to-anchor hint, the rebound audit funnel, and the
 * est-levels readout at the chosen date). Clicking a window button
 * enables that rolling window's High/Low envelope on the chart and arms
 * the roof/floor interaction — clicking a date on the chart draws the
 * trendline through the window's top + 2nd highs (the roof) and top +
 * 2nd lows (the floor) from history, converging and stopping at the
 * clicked date. Clicking a window button is EXCLUSIVE: every other
 * section's picks clear (that handler lives in the panel).
 */
import { Box, Chip, Typography } from "@mui/material";
import { fmtNum } from "@/lib/series";
import {
  ROOF_FLOOR_ESTIMATE_METHODS,
  type MovAveSpreadPairRow,
  type MovAveSpreadRoofFloorEstimateRow,
  type MovAveSpreadRoofFloorRebound,
  type RoofFloorEstimateMethod,
} from "@shared/types";
import { SectionLabel } from "../SectionLabel";
import {
  ROOF_FLOOR_WINDOWS,
  RF_ESTIMATE_METHOD_LABELS,
  PERIOD_GRID_SX,
  PERIOD_CHIP_SX,
} from "../constants";
import { aggregateReboundAudit } from "../chartOption";

interface Props {
  roofFloorWindow: number | null;
  onWindowClick: (w: number) => void;
  rfEstOn: boolean;
  onEstToggle: () => void;
  rfEstMethod: RoofFloorEstimateMethod;
  onEstMethodChange: (m: RoofFloorEstimateMethod) => void;
  disabled: boolean;
  anchorIdx: number | null;
  firstPairRows: MovAveSpreadPairRow[];
  rfEstimates: MovAveSpreadRoofFloorEstimateRow[] | undefined;
  rfRebounds: MovAveSpreadRoofFloorRebound[] | undefined;
}

export function RoofFloorSection(props: Props) {
  const { roofFloorWindow, onWindowClick, rfEstOn, onEstToggle, disabled } = props;
  return (
    <>
      {/* Row label ("Roof/Floor Trendlines") spans the full grid width
          on its own row — same style as the Trading Amt/MA label — with
          the window buttons on a new row below it. The buttons keep the
          period-column alignment of the pair chips: 60d sits under the
          MA60 column, …, 1275d in the last column (columns 1-2 stay
          empty — the 20d is not a roof/floor window). */}
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        {/* Row label */}
        <SectionLabel id="roofFloorWindow" active={roofFloorWindow != null} />
        {/* Est-levels toggle (column 1, free while the window buttons
            sit at columns 3-8): enables the two horizontal estimated
            roof/floor lines at the chosen date under the picked method.
            Loads the "rfest" extras group on first pick. */}
        {roofFloorWindow != null && (
          <Chip
            label="Est levels"
            size="small"
            clickable
            disabled={disabled}
            color={rfEstOn ? "secondary" : "default"}
            variant={rfEstOn ? "filled" : "outlined"}
            onClick={onEstToggle}
            sx={{ gridColumn: 1, ...PERIOD_CHIP_SX }}
          />
        )}
        {ROOF_FLOOR_WINDOWS.map((w, col) => (
          <Chip
            key={w}
            label={`${w}d`}
            size="small"
            clickable
            disabled={disabled}
            color={roofFloorWindow === w ? "primary" : "default"}
            variant={roofFloorWindow === w ? "filled" : "outlined"}
            onClick={() => onWindowClick(w)}
            sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
          />
        ))}
      </Box>
      {roofFloorWindow != null && <RoofFloorActiveTail {...props} />}
    </>
  );
}

/** The captions + method picker shown while a window is enabled —
 *  roofFloorWindow is non-null here. */
function RoofFloorActiveTail({
  roofFloorWindow,
  rfEstOn,
  rfEstMethod,
  onEstMethodChange,
  disabled,
  anchorIdx,
  firstPairRows,
  rfEstimates,
  rfRebounds,
}: Props) {
  return (
    <>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
      >
        click a date on the chart to draw roof/floor trendlines ·
        right axis: Δ since last anchor (Δ Low above the zero line ·
        Δ High below) replaces the amt bars
        {anchorIdx != null && firstPairRows[anchorIdx]
          ? ` · anchor ${firstPairRows[anchorIdx].date}`
          : ""}
      </Typography>
      {/* Rebound audit captions (the "rfaudit" extras group, loaded
          with the first window pick): the selected window's rebound
          frequency/amplitude + retest hold/break funnel for both
          sides, from the code's own recorded extremes. */}
      {rfRebounds != null &&
        (() => {
          const evs = rfRebounds ?? [];
          const line = (side: "floor" | "roof"): string => {
            const s = aggregateReboundAudit(evs, roofFloorWindow, side);
            if (s.nEvents === 0) {
              return `no recorded ${side} events at ${roofFloorWindow}d`;
            }
            const parts = [
              `${s.nEvents} extremes`,
              `${s.nRebounded}/${s.nEvents} rebounded (${Math.round(s.reboundPct ?? 0)}%` +
                (s.avgReboundPct != null ? `, avg ${s.avgReboundPct.toFixed(1)}%` : "") +
                ")",
            ];
            if (s.nRetested > 0) {
              parts.push(
                `${s.nRetested} retested → ${s.nHeld} held / ${s.nBroken} broken` +
                  (s.nHeld > 0
                    ? ` (${s.nHeldLaterBroken}/${s.nHeld} holds later broken)`
                    : "") +
                  (s.avgBouncePct != null
                    ? ` · bounce avg ${s.avgBouncePct.toFixed(1)}%`
                    : "") +
                  (s.avgDepthPct != null
                    ? ` · depth avg ${s.avgDepthPct.toFixed(1)}%`
                    : ""),
              );
            }
            return parts.join(" · ");
          };
          const cap = { variant: "caption", color: "text.secondary", sx: { display: "block", fontSize: "0.65rem" } } as const;
          return (
            <>
              <Typography {...cap} sx={{ ...cap.sx, mt: 0.5 }}>
                rebound audit ({roofFloorWindow}d · this code): floor — {line("floor")}
              </Typography>
              <Typography {...cap}>
                roof — {line("roof")} · ▲/▼ = rebounded extreme before the chosen date, near the median roof/floor (±5%) · source: analysis.mov_ave_spreads_roof_floor_rebounds
              </Typography>
            </>
          );
        })()}
      {/* Est-levels method picker — one chip per quantitative method
          (columns 3-8), shown while the toggle is on. The picked
          method's roof/floor estimates for the chosen date (anchor
          date, latest row by default) draw as the two horizontal
          dashed lines. */}
      {rfEstOn && (
        <Box sx={{ ...PERIOD_GRID_SX, mt: 0.5 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              gridColumn: "1 / span 2",
              fontSize: "0.7rem",
              alignSelf: "center",
            }}
          >
            est method
          </Typography>
          {ROOF_FLOOR_ESTIMATE_METHODS.map((m, col) => (
            <Chip
              key={m}
              label={RF_ESTIMATE_METHOD_LABELS[m] ?? m}
              size="small"
              clickable
              disabled={disabled}
              color={rfEstMethod === m ? "secondary" : "default"}
              variant={rfEstMethod === m ? "filled" : "outlined"}
              onClick={() => onEstMethodChange(m)}
              sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
            />
          ))}
        </Box>
      )}
      {rfEstOn &&
        rfEstimates != null &&
        rfEstimates.length === firstPairRows.length &&
        firstPairRows.length > 0 &&
        (() => {
          const idx = anchorIdx ?? firstPairRows.length - 1;
          const row = rfEstimates[idx];
          const roof =
            row?.[`roof_${rfEstMethod}_${roofFloorWindow}d`] ?? null;
          const floor =
            row?.[`floor_${rfEstMethod}_${roofFloorWindow}d`] ?? null;
          const date = firstPairRows[idx]?.date ?? "—";
          return (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              est levels @ {date} · {roofFloorWindow}d ·{" "}
              {RF_ESTIMATE_METHOD_LABELS[rfEstMethod] ?? rfEstMethod}:
              {roof != null && floor != null ? (
                <>
                  {" "}roof {fmtNum(roof)} · floor {fmtNum(floor)}{" "}
                  (two dashed lines — click a date to move them)
                </>
              ) : (
                " no estimate for this date/method — try another method"
              )}
            </Typography>
          );
        })()}
      {rfEstOn &&
        (rfEstimates == null ||
          rfEstimates.length !== firstPairRows.length) && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
          >
            {rfEstimates == null
              ? "est levels loading…"
              : "no estimate rows for this code — run python -m analysis.mov_ave_spread to build analysis.mov_ave_spreads_roof_floor_estimates"}
          </Typography>
        )}
    </>
  );
}
