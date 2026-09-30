import { fetchJson } from "./_cache";
import type {
  DebtDailyResponse,
  PbocOmaResponse,
} from "@shared/types";

export function fetchDebtDaily(
  startDate?: string | null,
  endDate?: string | null,
): Promise<DebtDailyResponse> {
  const params = new URLSearchParams();
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<DebtDailyResponse>(`/api/debt${qs ? `?${qs}` : ""}`);
}

/** PBoC Open Market Announcements — small dataset, no date filter (TTL-only cache). */
export function fetchPbocOmaAnnouncements(): Promise<PbocOmaResponse> {
  return fetchJson<PbocOmaResponse>(`/api/debt/oma`);
}
