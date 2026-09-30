/**
 * Shared data shaping utilities for options trend charts.
 * Extracted from AnnualSentimentPanel.tsx for reuse across the
 * merged OptionsTrendPanel.
 */
import type {
  OptionsExpiryMetaResponse,
  OptionsOiDailyResponse,
  OptionsRow,
} from "@shared/types";

export interface DailyOi {
  date: string;
  callOi: number;
  putOi: number;
  pcRatio: number;
}

export interface ExpiryContract {
  code: string;
  name: string;
  optionType: "CALL" | "PUT";
  prevDayOi: number;
}

export interface ExpiryMarker {
  expiryDate: string;
  tradingDate: string;
  contracts: ExpiryContract[];
  totalPrevDayOi: number;
}

export interface ExpiryMarkerDataItem {
  value: [number, number];
  marker: ExpiryMarker;
}

export function buildDailyOi(rows: OptionsRow[]): DailyOi[] {
  const byDate = new Map<string, { callOi: number; putOi: number }>();
  for (const r of rows) {
    if (!byDate.has(r.date)) byDate.set(r.date, { callOi: 0, putOi: 0 });
    const d = byDate.get(r.date)!;
    if (r.option_type === "CALL") d.callOi += r.open_interest;
    else d.putOi += r.open_interest;
  }
  const out = Array.from(byDate.entries())
    .map(([date, v]) => ({
      date,
      callOi: v.callOi,
      putOi: v.putOi,
      pcRatio: v.callOi > 0 ? v.putOi / v.callOi : NaN,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
  return out;
}

export function buildExpiryMarkers(rows: OptionsRow[]): ExpiryMarker[] {
  const contractRows = new Map<string, Map<string, OptionsRow>>();
  const contractExpiry = new Map<string, string>();
  const contractName = new Map<string, string>();

  for (const r of rows) {
    if (r.expiry_date) {
      contractExpiry.set(r.contract_code, r.expiry_date);
    }
    if (r.contract_name) {
      contractName.set(r.contract_code, r.contract_name);
    }
    if (!contractRows.has(r.contract_code)) {
      contractRows.set(r.contract_code, new Map());
    }
    contractRows.get(r.contract_code)!.set(r.date, r);
  }

  const byExpiry = new Map<string, { contracts: Map<string, ExpiryContract>; tradingDate: string }>();
  for (const [code, expiryDate] of contractExpiry) {
    const rowByDate = contractRows.get(code);
    if (!rowByDate) continue;

    let lastDate = "";
    for (const d of rowByDate.keys()) {
      if (d <= expiryDate && d > lastDate) lastDate = d;
    }
    if (!lastDate) continue;

    const row = rowByDate.get(lastDate);
    if (!row) continue;

    if (!byExpiry.has(expiryDate)) {
      byExpiry.set(expiryDate, { contracts: new Map(), tradingDate: lastDate });
    }
    const entry = byExpiry.get(expiryDate)!;

    if (!entry.contracts.has(code)) {
      entry.contracts.set(code, {
        code,
        name: contractName.get(code) || row.contract_name || code,
        optionType: row.option_type,
        prevDayOi: row.open_interest,
      });
    }
  }

  const markers: ExpiryMarker[] = [];
  for (const [expiryDate, entry] of byExpiry) {
    const contracts = Array.from(entry.contracts.values());
    const total = contracts.reduce((s, c) => s + c.prevDayOi, 0);
    markers.push({
      expiryDate,
      tradingDate: entry.tradingDate,
      contracts,
      totalPrevDayOi: total,
    });
  }

  return markers.sort((a, b) => a.tradingDate.localeCompare(b.tradingDate));
}

// ----------------------------------------------------------------------------
//  Payload-based builders — consume the per-plot SQL aggregates (/oi-daily,
//  /expiry-meta) instead of raw quote rows. The row-based builders above
//  stay for consumers that still hold the combined blob (annual sentiment).
// ----------------------------------------------------------------------------

/** Daily call/put OI (all cohorts summed) from the /oi-daily response. */
export function buildDailyFromOiDaily(resp: OptionsOiDailyResponse): DailyOi[] {
  const byDate = new Map<string, { callOi: number; putOi: number }>();
  for (const r of resp.daily) {
    let d = byDate.get(r.date);
    if (!d) {
      d = { callOi: 0, putOi: 0 };
      byDate.set(r.date, d);
    }
    d.callOi += r.call_oi;
    d.putOi += r.put_oi;
  }
  return Array.from(byDate.entries())
    .map(([date, v]) => ({
      date,
      callOi: v.callOi,
      putOi: v.putOi,
      pcRatio: v.callOi > 0 ? v.putOi / v.callOi : NaN,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

/** Expiry markers from the /expiry-meta response — per expiry group, the
 * contracts' final OI (last session ≤ expiry) with the group's last
 * trading date; deterministic (the SQL already picked each contract's
 * final session). */
export function buildExpiryMarkersFromMeta(
  resp: OptionsExpiryMetaResponse,
): ExpiryMarker[] {
  const byExpiry = new Map<
    string,
    { contracts: ExpiryContract[]; tradingDate: string }
  >();
  for (const m of resp.markers) {
    let entry = byExpiry.get(m.expiry_date);
    if (!entry) {
      entry = { contracts: [], tradingDate: m.last_date };
      byExpiry.set(m.expiry_date, entry);
    }
    if (m.last_date > entry.tradingDate) entry.tradingDate = m.last_date;
    entry.contracts.push({
      code: m.contract_code,
      name: m.contract_name || m.contract_code,
      optionType: m.option_type,
      prevDayOi: m.open_interest,
    });
  }

  const markers: ExpiryMarker[] = [];
  for (const [expiryDate, entry] of byExpiry) {
    const total = entry.contracts.reduce((s, c) => s + c.prevDayOi, 0);
    markers.push({
      expiryDate,
      tradingDate: entry.tradingDate,
      contracts: entry.contracts,
      totalPrevDayOi: total,
    });
  }
  return markers.sort((a, b) => a.tradingDate.localeCompare(b.tradingDate));
}
