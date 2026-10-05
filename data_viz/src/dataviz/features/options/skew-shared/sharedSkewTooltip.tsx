/**
 * Shared skew-over-time chart tooltip — works on the unified
 * SharedSkewPoint[] model for all data sources (oi_moneyness / greek_*).
 */
import React from "react";
import { fmtCompact, fmtNum } from "@/lib/series";
import { DOWN_COLOR, MA20_COLOR, UP_COLOR } from "@/theme/chart-palette";
import {
  CALL_ZONE_SERIES_NAME,
  PUT_ZONE_SERIES_NAME,
} from "../options-trend/bandData";
import { renderTooltip } from "../vol-smile/renderTooltip";
import type {
  SharedBandEvent,
  SharedSkewPoint,
  SharedWallZone,
  SharedWallZones,
} from "./types";

interface SharedSkewTooltipData {
  date: string;
  seriesItems: Array<{
    seriesName: string;
    value: [string, number | null];
    marker?: string;
    color?: string;
  }>;
  points: SharedSkewPoint[];
  expiryColorMap: Map<string, string>;
  /** Options-implied band events keyed by date (markers on the spot
   *  curve); rendered as a compact events line when the hovered date
   *  carries one. */
  bandEventsByDate?: Map<string, SharedBandEvent[]>;
  /** Chain-dominant wall zones per (date, side) — analysis.options_walls
   *  light-shade layer; rendered as per-side wall rows when the hovered
   *  date carries one. */
  wallZonesByDate?: SharedWallZones;
}

/** Wall lifecycle state color — ACTIVE side color, ERODED orange,
 *  BREACHED red (palette tokens, same semantics as the Options Trend
 *  band tooltip's state label). */
function wallStateColor(state: SharedWallZone["state"], sideColor: string): string {
  switch (state) {
    case "BREACHED":
      return DOWN_COLOR;
    case "ERODED":
      return MA20_COLOR;
    default:
      return sideColor;
  }
}

function ColoredDot({ color }: { color: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        borderRadius: "50%",
        backgroundColor: color,
        marginRight: 5,
        verticalAlign: "middle",
        border: "1px solid rgba(0,0,0,0.2)",
      }}
    />
  );
}

/** Signed compact form — explicit "+" for non-negative OI deltas. */
function signedCompact(v: number): string {
  return (v >= 0 ? "+" : "") + fmtCompact(v);
}

/** One chain-dominant wall zone row — the same per-side entry the
 *  Options Trend OI Bands tooltip shows, ranked chain-wide here: strike
 *  range, OI-weighted center, mass share, lifecycle state. */
function WallZoneRow({ name, zone, color }: { name: string; zone: SharedWallZone; color: string }) {
  return (
    <div>
      <ColoredDot color={color} />
      {name} · <b>{fmtNum(zone.low)}–{fmtNum(zone.high)}</b> (ctr {fmtNum(zone.center)})
      {zone.massShare != null && (
        <span style={{ opacity: 0.65 }}> · mass {(zone.massShare * 100).toFixed(1)}%</span>
      )}
      {zone.state != null && (
        <span style={{ color: wallStateColor(zone.state, color), fontWeight: 600 }}>
          {" "}· {zone.state}
        </span>
      )}
    </div>
  );
}

function SharedSkewTooltipContent({
  date,
  seriesItems,
  points,
  expiryColorMap,
  bandEventsByDate,
  wallZonesByDate,
}: SharedSkewTooltipData) {
  const d = points.find((s) => s.date === date);

  const children: React.ReactNode[] = [<b key="date">{date}</b>];

  for (const item of seriesItems) {
    const val = item.value[1];
    children.push(
      <div key={item.seriesName}>
        {item.color ? <ColoredDot color={item.color} /> : item.marker ?? ""}
        {item.seriesName}: <b>{val == null ? "—" : fmtNum(val)}</b>
      </div>,
    );
  }

  if (!d) return <React.Fragment>{children}</React.Fragment>;

  if (d.skewPct != null && Number.isFinite(d.skewPct)) {
    children.push(
      <div key="agg-skew">
        Skew Δ (agg):{" "}
        <b>
          {d.skewPct >= 0 ? "+" : ""}
          {fmtNum(d.skewPct, 2)}%
        </b>
      </div>,
    );
  }

  // Absolute OI context: chain-wide total plus each group's share — the
  // curves' thickness encodes the same numbers (peak OI per expiry).
  const oiRows = d.perExpiry.filter((pe) => pe.oiTotal != null);
  if (oiRows.length > 0) {
    const chainOi = oiRows.reduce((s, pe) => s + (pe.oiTotal ?? 0), 0);
    children.push(
      <div key="chain-oi">
        Chain OI: <b>{fmtCompact(chainOi)}</b> contracts
      </div>,
    );
  }

  // Corridor events (options_bands v2): fresh crosses of the OI-mass
  // corridor vs the prior session's corridor, with spot's OI-mass
  // position share and the validated lean for context. Amplified events
  // (sig) add the fresh strike-grid expansion that makes the cross more
  // significant — the breach is into freshly-listed price territory.
  const bandEvents = bandEventsByDate?.get(date);
  if (bandEvents && bandEvents.length > 0) {
    for (const ev of bandEvents) {
      const parts: React.ReactNode[] = [
        `Corridor ${ev.kind} (vs prior corridor)`,
      ];
      if (ev.spotPct != null && Number.isFinite(ev.spotPct)) {
        parts.push(
          <> · OI below spot <b>{fmtNum(ev.spotPct * 100, 0)}%</b></>,
        );
      }
      if (ev.lean && ev.lean !== "NEUTRAL") {
        parts.push(<> · lean <b>{ev.lean}</b></>);
      }
      if (ev.sig) {
        const side = ev.kind === "EXIT_HIGH" ? "higher" : "lower";
        parts.push(
          <>
            {" · "}
            <b>
              AMPLIFIED: +
              {ev.sigNewStrikes != null ? fmtNum(ev.sigNewStrikes, 0) : "?"}{" "}
              new {side} strikes/40d
            </b>{" "}
            (breach into fresh strike territory)
          </>,
        );
      }
      children.push(
        <div key={`band-${ev.kind}`}>
          ◈ {parts}
        </div>,
      );
    }
  }

  // Chain-dominant wall zones (analysis.options_walls, wall_type='zone')
  // — the per-side light-shade wall layer; tooltip rows mirror the
  // Options Trend OI Bands wall entries.
  const wallCall = wallZonesByDate?.call.get(date);
  const wallPut = wallZonesByDate?.put.get(date);
  if (wallCall) {
    children.push(
      <WallZoneRow key="wall-call" name={CALL_ZONE_SERIES_NAME} zone={wallCall} color={UP_COLOR} />,
    );
  }
  if (wallPut) {
    children.push(
      <WallZoneRow key="wall-put" name={PUT_ZONE_SERIES_NAME} zone={wallPut} color={DOWN_COLOR} />,
    );
  }

  if (d.perExpiry.length > 0) {
    children.push(
      <div key="per-expiry-label" style={{ opacity: 0.7 }}>
        Per-expiry (ΔSpot):
      </div>,
    );

    d.perExpiry.forEach((pe, idx) => {
      const spot = d.spot;
      const sk = pe.skewPrice;
      let gapSpotStr: React.ReactNode = "—";
      if (sk != null && Number.isFinite(sk) && spot != null) {
        gapSpotStr = fmtNum(spot - (sk as number), 2);
      }

      const sign =
        pe.skewPct != null && Number.isFinite(pe.skewPct)
          ? (pe.skewPct >= 0 ? "+" : "") + fmtNum(pe.skewPct, 2)
          : "—";

      children.push(
        <div key={`pe-${idx}`} style={{ paddingLeft: "8px" }}>
          <ColoredDot color={expiryColorMap.get(pe.expiry) ?? "#888"} />
          {pe.expiry}: <b>{sign}</b>{" "}
          ΔSpot=<b>{gapSpotStr}</b>
          {pe.oiTotal != null && (
            <span style={{ opacity: 0.75 }}>
              {" "}
              · OI {fmtCompact(pe.oiTotal)}
              {pe.oiDelta5d != null && <> · Δ5d {signedCompact(pe.oiDelta5d)}</>}
              {pe.oiDelta20d != null && <> · Δ20d {signedCompact(pe.oiDelta20d)}</>}
              {pe.oiMax20d != null && <> · 20d max {fmtCompact(pe.oiMax20d)}</>}
            </span>
          )}
        </div>,
      );
    });
  }

  return <React.Fragment>{children}</React.Fragment>;
}

export function makeSharedSkewTooltipFormatter(
  points: SharedSkewPoint[],
  expiryColorMap: Map<string, string>,
  bandEventsByDate?: Map<string, SharedBandEvent[]>,
  wallZonesByDate?: SharedWallZones,
): (params: unknown) => string {
  return (p: unknown): string => {
    const items = (Array.isArray(p) ? p : [p]) as Array<{
      seriesName: string;
      value: [string, number | null];
      marker?: string;
      color?: string;
    }>;
    if (items.length === 0) return "";
    const date = items[0].value[0];
    return renderTooltip(
      <SharedSkewTooltipContent
        date={date}
        seriesItems={items}
        points={points}
        expiryColorMap={expiryColorMap}
        bandEventsByDate={bandEventsByDate}
        wallZonesByDate={wallZonesByDate}
      />,
    );
  };
}
