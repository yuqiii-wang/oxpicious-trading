/**
 * Global filter / theme state for the data_viz dashboard.
 */
import { create } from "zustand";
import type { OptionsVenue } from "@/lib/api-client/options";

export type ThemeMode = "light" | "dark";

export interface SnapshotDate {
  label: string;
  date: string;
}

interface AppState {
  /** Light or dark mode (Material Design). */
  themeMode: ThemeMode;
  toggleTheme: () => void;
  setThemeMode: (mode: ThemeMode) => void;

  /** Global date-range filter applied to all dashboards. */
  startDate: string | null;
  endDate: string | null;
  setStartDate: (d: string | null) => void;
  setEndDate: (d: string | null) => void;
  setDateRange: (start: string | null, end: string | null) => void;

  /** Options — currently selected underlying. */
  underlyingCode: string;
  setUnderlyingCode: (code: string) => void;

  /** Options — venue toggle: 'SZSE' (ETF) / 'SSE' (ETF) / 'CFFEX' (index).
   *  Map to the API's target_type via venueToTargetType (SZSE+SSE → ETF). */
  optionsVenue: OptionsVenue;
  setOptionsVenue: (v: OptionsVenue) => void;

  /** Options — 4 snapshot dates (Q4 start / last quarter / last month / latest). */
  snapshotDates: SnapshotDate[];
  setSnapshotDate: (idx: number, date: string) => void;
  setSnapshotDates: (dates: SnapshotDate[]) => void;

  /** ETF + Margin — currently selected L1 sector id (e.g. "FIN", "TECH", "BROAD"). */
  sectorId: string | null;
  setSectorId: (id: string | null) => void;

  /** ETF + Margin — currently selected L2 industry slug (e.g. "banks", "semi"). */
  industrySlug: string | null;
  setIndustrySlug: (slug: string | null) => void;

  /** Exchange filter — 'PRIMARY' (default, all Greater-China), 'SS' (SSE), 'SZ' (SZSE), 'BJ' (BSE), 'HK', 'OVERSEAS', or null (no filter). */
  exchange: string | null;
  setExchange: (ex: string | null) => void;

  /** Legacy theme slug — kept for backward compat, mapped to industrySlug. */
  themeSlug: string | null;
  setThemeSlug: (slug: string | null) => void;
}

const DEFAULT_SNAPSHOTS: SnapshotDate[] = [
  { label: "Q4 Start", date: "" },
  { label: "Last Quarter", date: "" },
  { label: "Last Month", date: "" },
  { label: "Latest", date: "" },
];

export const useStore = create<AppState>((set) => ({
  themeMode: "dark",
  toggleTheme: () =>
    set((s) => ({ themeMode: s.themeMode === "light" ? "dark" : "light" })),
  setThemeMode: (mode) => set({ themeMode: mode }),

  startDate: null,
  endDate: null,
  setStartDate: (d) => set({ startDate: d }),
  setEndDate: (d) => set({ endDate: d }),
  setDateRange: (start, end) => set({ startDate: start, endDate: end }),

  // Default matches the default optionsVenue='SZSE' (SZSE ETF codes).
  underlyingCode: "159919",
  setUnderlyingCode: (code) => set({ underlyingCode: code }),

  optionsVenue: "SZSE",
  setOptionsVenue: (v) => set({ optionsVenue: v }),

  snapshotDates: DEFAULT_SNAPSHOTS,
  setSnapshotDate: (idx, date) =>
    set((s) => {
      const next = s.snapshotDates.map((sd, i) =>
        i === idx ? { ...sd, date } : sd,
      );
      return { snapshotDates: next };
    }),
  setSnapshotDates: (dates) => set({ snapshotDates: dates }),

  sectorId: "BROAD",
  setSectorId: (id) => set({ sectorId: id, industrySlug: null }),

  industrySlug: null,
  setIndustrySlug: (slug) => set({ industrySlug: slug }),

  exchange: "PRIMARY",
  setExchange: (ex) => set({ exchange: ex }),

  themeSlug: null,
  setThemeSlug: (slug) => set({ industrySlug: slug }),
}));
